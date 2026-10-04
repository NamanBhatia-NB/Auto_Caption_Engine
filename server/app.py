"""Auto Caption Engine API.

The service intentionally keeps jobs on disk/in memory for a simple local demo:
video bytes never leave the machine when the local Whisper mode is selected.
"""

from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import time
import uuid
from pathlib import Path
from threading import Lock, Thread

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename

from render import ECLIPSE_STYLE, generate_eclipse_ass, render_captioned_video
from transcribe import retime_segment_text, transcribe_with_faster_whisper


load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024

ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = ROOT / "uploads"
OUTPUT_DIR = ROOT / "outputs"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# This is a local demo server, so an in-memory job registry is enough and keeps
# the setup free of a database.  Files are still kept under predictable folders
# so a job can be previewed/downloaded until the server is restarted.
jobs: dict[str, dict] = {}
jobs_lock = Lock()
ALLOWED_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm"}


@app.errorhandler(413)
def upload_too_large(_error):
    return jsonify({"error": "That video is larger than the configured upload limit."}), 413


@app.route("/api/upload", methods=["POST"])
def upload_video():
    """Store a video and return its metadata/job id."""
    if "video" not in request.files:
        return jsonify({"error": "Choose a video file first."}), 400
    uploaded = request.files["video"]
    original_name = secure_filename(uploaded.filename or "")
    if not original_name:
        return jsonify({"error": "The uploaded file has no filename."}), 400
    extension = Path(original_name).suffix.lower().lstrip(".")
    if extension not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Supported formats: MP4, MOV, AVI, MKV, and WebM."}), 415

    job_id = uuid.uuid4().hex[:8]
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    video_path = job_dir / f"input.{extension}"
    uploaded.save(video_path)

    try:
        info = get_video_info(str(video_path))
        if not info["has_video"]:
            raise ValueError("The file does not contain a readable video stream.")
    except Exception as error:
        video_path.unlink(missing_ok=True)
        job_dir.rmdir()
        return jsonify({"error": str(error)}), 422

    job = {
        "id": job_id,
        "status": "uploaded",
        "video_path": str(video_path),
        "video_info": info,
        "original_filename": original_name,
        "captions": None,
        "output_path": None,
        "style": dict(ECLIPSE_STYLE),
        "progress": 0,
        "error": None,
        "created_at": time.time(),
    }
    with jobs_lock:
        jobs[job_id] = job
    return jsonify({
        "job_id": job_id,
        "status": job["status"],
        "video_info": info,
        "filename": original_name,
    })


@app.route("/api/transcribe", methods=["POST"])
def start_transcription():
    """Start local Faster-Whisper transcription."""
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    language = str(data.get("language", "auto")).lower()
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Invalid job id."}), 400
    if job["status"] not in {"uploaded", "transcribed", "error"}:
        return jsonify({"error": f"Cannot transcribe while job is {job['status']}."}), 409

    job.update({"status": "transcribing", "progress": 5, "phase": "preparing audio", "error": None})

    def worker():
        try:
            job.update({"progress": 10, "phase": "extracting audio"})
            audio_path = extract_audio(job["video_path"], job_id, suffix=".wav")
            try:
                job.update({"progress": 20, "phase": "loading faster-whisper model"})
                result = transcribe_with_faster_whisper(
                    audio_path,
                    language=language,
                    model_size=data.get("model_size")
                    or os.getenv("WHISPER_MODEL_SIZE"),
                )
            finally:
                Path(audio_path).unlink(missing_ok=True)
            if not result.get("segments"):
                raise ValueError("No spoken words were returned by the transcription engine.")
            job.update({"captions": result, "status": "transcribed", "progress": 100, "phase": "complete", "error": None})
        except Exception as error:  # surfaced through /api/status without exposing secrets
            job.update({"status": "error", "progress": 0, "phase": "error", "error": str(error)})

    Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id, "status": "transcribing"})


@app.route("/api/render", methods=["POST"])
def start_render():
    """Burn the current captions into a downloadable MP4."""
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Invalid job id."}), 400
    if job["status"] not in {"transcribed", "rendered", "error"}:
        return jsonify({"error": f"Cannot render while job is {job['status']}."}), 409
    if not job.get("captions", {}).get("segments"):
        return jsonify({"error": "Transcribe the video before rendering."}), 400

    requested_style = data.get("style")
    if isinstance(requested_style, dict):
        job["style"] = {**job.get("style", ECLIPSE_STYLE), **requested_style}
    job.update({
        "status": "rendering",
        "progress": 5,
        "phase": "preparing caption render",
        "error": None,
        "warning": None,
    })

    def worker():
        try:
            output_dir = OUTPUT_DIR / job_id
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "captioned.mp4"
            info = job["video_info"]
            def update_render_progress(progress: int, phase: str):
                job.update({"progress": progress, "phase": phase})

            render_result = render_captioned_video(
                video_path=job["video_path"],
                segments=job["captions"]["segments"],
                output_path=str(output_path),
                video_width=info["width"],
                video_height=info["height"],
                style=job.get("style"),
                matting_cache_path=str(output_dir / "person-mask.mkv"),
                progress=update_render_progress,
            )
            job.update({
                "output_path": str(output_path),
                "output_version": str(time.time_ns()),
                "status": "rendered",
                "progress": 100,
                "phase": "complete",
                "matting_applied": render_result.matting_applied,
                "matting_cached": render_result.matting_cached,
                "warning": render_result.warning,
                "error": None,
            })
        except Exception as error:
            job.update({"status": "error", "progress": 0, "error": str(error)})

    Thread(target=worker, daemon=True).start()
    return jsonify({"job_id": job_id, "status": "rendering"})


@app.route("/api/status/<job_id>", methods=["GET"])
def get_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    response = {
        "job_id": job_id,
        "status": job["status"],
        "progress": job.get("progress", 0),
        "phase": job.get("phase"),
        "video_info": job.get("video_info"),
        "filename": job.get("original_filename"),
        "error": job.get("error"),
        "warning": job.get("warning"),
        "matting_applied": job.get("matting_applied"),
        "matting_cached": job.get("matting_cached"),
        "output_version": job.get("output_version"),
        "has_captions": bool(job.get("captions")),
        "has_output": bool(job.get("output_path") and Path(job["output_path"]).exists()),
    }
    if job.get("captions"):
        response.update({
            "caption_count": len(job["captions"].get("segments", [])),
            "word_count": len(job["captions"].get("words", [])),
        })
    return jsonify(response)


@app.route("/api/captions/<job_id>", methods=["GET"])
def get_captions(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if not job.get("captions"):
        return jsonify({"error": "No captions available yet."}), 404
    return jsonify(job["captions"])


@app.route("/api/captions/<job_id>", methods=["PUT"])
def update_captions(job_id: str):
    """Save text edits while retaining each cue's original timing window."""
    job = jobs.get(job_id)
    data = request.get_json(silent=True) or {}
    if not job:
        return jsonify({"error": "Job not found."}), 404
    if not job.get("captions") or not isinstance(data.get("segments"), list):
        return jsonify({"error": "Send a captions object with a segments array."}), 400

    original = job["captions"].get("segments", [])
    updated = []
    for index, incoming in enumerate(data["segments"]):
        if not isinstance(incoming, dict):
            continue
        fallback = original[index] if index < len(original) else {}
        text = str(incoming.get("text", fallback.get("text", ""))).strip()
        if not text:
            continue
        start = _number(incoming.get("start", fallback.get("start", 0)))
        end = max(_number(incoming.get("end", fallback.get("end", start + 0.1))), start + 0.05)
        updated.append(retime_segment_text(text, start, end))

    if not updated:
        return jsonify({"error": "At least one caption cue is required."}), 400
    job["captions"]["segments"] = updated
    job["captions"]["words"] = [word for segment in updated for word in segment["words"]]
    job["captions"]["text"] = " ".join(segment["text"] for segment in updated)
    if job.get("output_path"):
        Path(job["output_path"]).unlink(missing_ok=True)
    job.update({"output_path": None, "status": "transcribed", "progress": 45, "error": None})
    return jsonify({"status": "updated", "job_id": job_id, "captions": job["captions"]})


@app.route("/api/preview/<job_id>", methods=["GET"])
def preview_video(job_id: str):
    job = jobs.get(job_id)
    if not job or not Path(job["video_path"]).exists():
        return jsonify({"error": "Video not found."}), 404
    return send_file(job["video_path"], mimetype=_video_mimetype(job["video_path"]), conditional=True)


@app.route("/api/output/<job_id>", methods=["GET"])
def output_video(job_id: str):
    job = jobs.get(job_id)
    if not job or not job.get("output_path") or not Path(job["output_path"]).exists():
        return jsonify({"error": "Rendered video is not available yet."}), 404
    return send_file(job["output_path"], mimetype="video/mp4", conditional=True)


@app.route("/api/download/<job_id>", methods=["GET"])
def download_video(job_id: str):
    job = jobs.get(job_id)
    if not job or not job.get("output_path") or not Path(job["output_path"]).exists():
        return jsonify({"error": "Rendered video is not available yet."}), 404
    stem = Path(job.get("original_filename", "video.mp4")).stem
    return send_file(job["output_path"], mimetype="video/mp4", as_attachment=True, download_name=f"{stem}_eclipse.mp4")


@app.route("/api/ass/<job_id>", methods=["GET"])
def get_ass_file(job_id: str):
    job = jobs.get(job_id)
    if not job or not job.get("captions"):
        return jsonify({"error": "No captions available."}), 404
    info = job["video_info"]
    ass = generate_eclipse_ass(
        job["captions"]["segments"], info["width"], info["height"], job.get("style")
    )
    return app.response_class(ass, mimetype="text/plain")


@app.route("/api/health", methods=["GET"])
def health():
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        ffmpeg_ok = result.returncode == 0
        version = result.stdout.splitlines()[0] if ffmpeg_ok else "not found"
    except Exception:
        ffmpeg_ok, version = False, "not found"
    return jsonify({
        "status": "ok",
        "ffmpeg": {"available": ffmpeg_ok, "version": version},
        "local_transcription": ffmpeg_ok,
        "transcription": "faster-whisper",
        "behind_person_rendering": "rvm-mobilenetv3-onnx",
        "active_jobs": len(jobs),
    })


def get_video_info(path: str) -> dict:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration,r_frame_rate,codec_name",
        "-show_entries", "format=duration", "-of", "json", path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ValueError("FFprobe could not read this video.")
    payload = json.loads(result.stdout)
    stream = (payload.get("streams") or [{}])[0]
    if not stream.get("width") or not stream.get("height"):
        return {"has_video": False}
    rate = str(stream.get("r_frame_rate", "30/1")).split("/")
    fps = _number(rate[0]) / max(_number(rate[1]) if len(rate) > 1 else 1, 1)
    duration = _number((payload.get("format") or {}).get("duration", stream.get("duration", 0)))
    return {
        "has_video": True,
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "duration": round(duration, 3),
        "fps": round(fps, 2),
        "codec": stream.get("codec_name", "unknown"),
    }


def extract_audio(video_path: str, job_id: str, suffix: str = ".wav") -> str:
    audio_path = str(UPLOAD_DIR / job_id / f"audio{suffix}")
    codec_args = ["-c:a", "pcm_s16le"]
    command = [
        "ffmpeg", "-hide_banner", "-y", "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000", *codec_args, audio_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"Could not extract audio: {result.stderr[-500:]}")
    return audio_path


def _video_mimetype(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "video/mp4"


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    print(f"\nAuto Caption Engine API: http://localhost:{port}")
    print("Local Faster-Whisper mode: free, offline after the model download")
    app.run(host="0.0.0.0", port=port, debug=debug)
