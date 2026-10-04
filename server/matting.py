"""Local recurrent human-video matting for behind-person caption compositing.

Robust Video Matting (RVM) is used only to estimate a temporally stable person
alpha matte. Caption text, timing, placement, and styling remain owned by the
existing ASS renderer.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

RVM_MODEL_NAME = "rvm_mobilenetv3_fp32.onnx"
RVM_MODEL_URL = (
    "https://github.com/PeterL1n/RobustVideoMatting/releases/download/"
    "v1.0.0/rvm_mobilenetv3_fp32.onnx"
)
RVM_MODEL_SHA256 = "88d4531297118f595bf2fd60f6f566aec2e559393802d1f436c380f0cbbd2828"
ProgressCallback = Callable[[int, str], None]


class MattingUnavailable(RuntimeError):
    """Raised when local matting cannot run and normal caption rendering should be used."""


@dataclass(frozen=True)
class MattingResult:
    mask_path: str
    cached: bool
    frame_count: int
    inference_size: tuple[int, int]


def ensure_person_mask(
    video_path: str,
    mask_path: str,
    *,
    model_path: str | None = None,
    max_inference_size: int | None = None,
    progress: ProgressCallback | None = None,
) -> MattingResult:
    """Create or reuse a lossless grayscale RVM alpha-mask video.

    Frames are processed sequentially and all four RVM recurrent states are
    recycled. Inference is bounded for practical CPU use, then the soft alpha
    is upscaled to the source dimensions before lossless FFV1 encoding.
    """
    try:
        import av
        import numpy as np
        import onnxruntime as ort
    except ImportError as error:
        raise MattingUnavailable(
            "Behind-person rendering needs av, numpy, and onnxruntime. "
            "Install server/requirements.txt to enable it."
        ) from error

    source = Path(video_path).resolve()
    destination = Path(mask_path).resolve()
    metadata_path = destination.with_suffix(destination.suffix + ".json")
    max_size = max_inference_size or _env_int("RVM_MAX_INFERENCE_SIZE", 512, 256, 1024)

    try:
        container = av.open(str(source))
        stream = container.streams.video[0]
        width = int(stream.codec_context.width)
        height = int(stream.codec_context.height)
        fps = float(stream.average_rate or stream.base_rate or 30)
        expected_frames = int(stream.frames or 0)
        container.close()
    except Exception as error:
        raise MattingUnavailable(f"Could not inspect video for person matting: {error}") from error

    inference_width, inference_height = _bounded_size(width, height, max_size)
    fingerprint = _mask_fingerprint(
        source, width, height, fps, inference_width, inference_height
    )
    if _valid_cache(destination, metadata_path, fingerprint):
        cached_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        _notify(progress, 78, "reusing cached person matte")
        return MattingResult(
            str(destination),
            True,
            int(cached_metadata.get("frame_count", 0)),
            (inference_width, inference_height),
        )

    selected_model = Path(model_path or os.getenv("RVM_MODEL_PATH") or _default_model_path())
    _ensure_model(selected_model, progress)
    destination.parent.mkdir(parents=True, exist_ok=True)

    providers = _providers(ort)
    try:
        session = ort.InferenceSession(str(selected_model), providers=providers)
    except Exception as error:
        raise MattingUnavailable(f"Could not load the RVM ONNX model: {error}") from error

    temporary_mask = Path(
        tempfile.mktemp(prefix="person-mask-", suffix=".mkv", dir=str(destination.parent))
    )
    writer = _open_mask_writer(temporary_mask, width, height, fps)
    frame_count = 0
    recurrent = [np.zeros((1, 1, 1, 1), dtype=np.float32) for _ in range(4)]
    downsample_ratio = np.asarray([1.0], dtype=np.float32)
    expand = _env_int("RVM_ALPHA_EXPAND", 1, 0, 3)
    alpha_low = _env_float("RVM_ALPHA_LOW", 0.08, 0.0, 0.95)
    alpha_high = _env_float("RVM_ALPHA_HIGH", 0.45, alpha_low + 0.01, 1.0)

    try:
        _notify(progress, 35, "matting person and foreground")
        with av.open(str(source)) as input_container:
            video_stream = input_container.streams.video[0]
            for frame in input_container.decode(video_stream):
                resized = frame.reformat(
                    width=inference_width,
                    height=inference_height,
                    format="rgb24",
                ).to_ndarray()
                source_tensor = np.ascontiguousarray(
                    resized.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
                )
                outputs = session.run(
                    None,
                    {
                        "src": source_tensor,
                        "r1i": recurrent[0],
                        "r2i": recurrent[1],
                        "r3i": recurrent[2],
                        "r4i": recurrent[3],
                        "downsample_ratio": downsample_ratio,
                    },
                )
                alpha = np.clip(outputs[1][0, 0], 0.0, 1.0)
                recurrent = outputs[2:6]
                # RVM produces a soft matte. Directly using it makes captions
                # look faintly visible through skin/clothes. Harden the matte
                # while retaining a narrow feathered edge for hair and hands.
                alpha = np.clip(
                    (alpha - alpha_low) / (alpha_high - alpha_low), 0.0, 1.0
                )
                alpha = alpha * alpha * (3.0 - 2.0 * alpha)
                if expand:
                    alpha = _expand_alpha(alpha, expand, np)
                mask_small = np.rint(alpha * 255.0).astype(np.uint8)
                mask_full = av.VideoFrame.from_ndarray(mask_small, format="gray").reformat(
                    width=width, height=height, format="gray"
                ).to_ndarray()
                writer.stdin.write(mask_full.tobytes())
                frame_count += 1
                if frame_count % 15 == 0:
                    if expected_frames:
                        fraction = min(frame_count / expected_frames, 1.0)
                        _notify(progress, 35 + round(fraction * 40), "matting person and foreground")
                    else:
                        _notify(progress, 55, f"matting foreground frame {frame_count}")

        writer.stdin.close()
        stderr = writer.stderr.read().decode("utf-8", errors="replace")
        return_code = writer.wait(timeout=120)
        if return_code != 0:
            raise RuntimeError(f"FFmpeg mask encoding failed: {stderr[-1200:]}")
        if frame_count == 0:
            raise RuntimeError("RVM did not receive any video frames.")

        temporary_mask.replace(destination)
        metadata = {
            **fingerprint,
            "frame_count": frame_count,
            "model": RVM_MODEL_NAME,
            "providers": providers,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        _notify(progress, 78, "person matte ready")
        return MattingResult(
            str(destination), False, frame_count, (inference_width, inference_height)
        )
    except Exception as error:
        if writer.poll() is None:
            writer.kill()
        temporary_mask.unlink(missing_ok=True)
        if isinstance(error, MattingUnavailable):
            raise
        raise MattingUnavailable(f"Person matting failed: {error}") from error


def _default_model_path() -> str:
    return str(Path(__file__).resolve().parent / "models" / RVM_MODEL_NAME)


def _ensure_model(model_path: Path, progress: ProgressCallback | None) -> None:
    if model_path.exists() and _sha256(model_path) == RVM_MODEL_SHA256:
        return
    if os.getenv("RVM_AUTO_DOWNLOAD", "true").strip().lower() not in {
        "1", "true", "yes", "on"
    }:
        raise MattingUnavailable(
            f"RVM model not found at {model_path}. Enable RVM_AUTO_DOWNLOAD or "
            "set RVM_MODEL_PATH to the official FP32 ONNX model."
        )

    model_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = model_path.with_suffix(model_path.suffix + ".part")
    _notify(progress, 25, "downloading RVM MobileNetV3 model")
    try:
        with urllib.request.urlopen(RVM_MODEL_URL, timeout=120) as response, open(
            temporary, "wb"
        ) as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if _sha256(temporary) != RVM_MODEL_SHA256:
            raise RuntimeError("downloaded model checksum did not match")
        temporary.replace(model_path)
    except Exception as error:
        temporary.unlink(missing_ok=True)
        raise MattingUnavailable(f"Could not obtain the RVM model: {error}") from error


def _open_mask_writer(path: Path, width: int, height: int, fps: float):
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
        "-r", f"{fps:.8f}", "-i", "pipe:0", "-an", "-c:v", "ffv1",
        "-level", "3", "-pix_fmt", "gray", str(path),
    ]
    try:
        return subprocess.Popen(
            command, stdin=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except Exception as error:
        raise MattingUnavailable(f"Could not start FFmpeg mask writer: {error}") from error


def _providers(ort) -> list[str]:
    available = set(ort.get_available_providers())
    requested = os.getenv("RVM_PROVIDER", "").strip()
    if requested:
        if requested not in available:
            raise MattingUnavailable(
                f"RVM provider {requested!r} is unavailable; installed providers: "
                + ", ".join(sorted(available))
            )
        return [requested]
    return ["CUDAExecutionProvider", "CPUExecutionProvider"] if "CUDAExecutionProvider" in available else ["CPUExecutionProvider"]


def _bounded_size(width: int, height: int, maximum: int) -> tuple[int, int]:
    scale = min(maximum / max(width, height), 1.0)
    resized_width = max(32, int(math.ceil(width * scale / 32)) * 32)
    resized_height = max(32, int(math.ceil(height * scale / 32)) * 32)
    return resized_width, resized_height


def _expand_alpha(alpha, radius: int, np):
    expanded = alpha
    for _ in range(radius):
        padded = np.pad(expanded, 1, mode="edge")
        expanded = np.maximum.reduce(
            [
                padded[:-2, :-2], padded[:-2, 1:-1], padded[:-2, 2:],
                padded[1:-1, :-2], padded[1:-1, 1:-1], padded[1:-1, 2:],
                padded[2:, :-2], padded[2:, 1:-1], padded[2:, 2:],
            ]
        )
    return expanded


def _mask_fingerprint(
    source: Path,
    width: int,
    height: int,
    fps: float,
    inference_width: int,
    inference_height: int,
) -> dict:
    stat = source.stat()
    return {
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
        "width": width,
        "height": height,
        "fps": round(fps, 6),
        "inference_width": inference_width,
        "inference_height": inference_height,
        "model_sha256": RVM_MODEL_SHA256,
        "alpha_expand": _env_int("RVM_ALPHA_EXPAND", 1, 0, 3),
        "alpha_low": _env_float("RVM_ALPHA_LOW", 0.08, 0.0, 0.95),
        "alpha_high": _env_float("RVM_ALPHA_HIGH", 0.45, 0.09, 1.0),
    }


def _valid_cache(mask_path: Path, metadata_path: Path, fingerprint: dict) -> bool:
    if not mask_path.exists() or mask_path.stat().st_size < 1024 or not metadata_path.exists():
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return all(metadata.get(key) == value for key, value in fingerprint.items())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(os.getenv(name, str(default)))))
    except ValueError:
        return default


def _notify(callback: ProgressCallback | None, percent: int, phase: str) -> None:
    if callback:
        callback(percent, phase)
