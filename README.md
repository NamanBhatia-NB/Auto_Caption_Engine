# Eclipse Caption Studio

A local-first AI caption engine for vertical video. Upload a clip, generate word-timed captions, review the live overlay, and download a video with those captions permanently recorded into it **directly from the browser**.

The caption design is based on the supplied `eclipse.png` and `Eclipse.mp4` references:

- clean white, lower-third typography;
- no black outline, drop shadow, glow, or white stroke;
- the currently spoken word changes to solid yellow only;
- a translucent yellow word-shaped box behind the active word;
- word changes follow the word's own start/end timestamps;
- captions are grouped into short cues and wrapped by the shared browser caption painter at the video margins.

## Current approach

| Layer | Current implementation | Purpose |
| --- | --- | --- |
| UI | React + Vite | Upload, transcript review, live preview, and export controls. |
| Transcription | Faster-Whisper | Local CTranslate2 Whisper model with real word-level timestamps. |
| Default local model | `base`, CPU `int8`, beam size `1` | Faster local processing while retaining word timestamps. |
| Audio preparation | FFmpeg | Converts the uploaded video to mono 16 kHz WAV before transcription. |
| Live captions and export | React + Canvas 2D | The same painter, font, layout, and timestamps are used for preview and download. |
| Video encoding and audio | Canvas captureStream + MediaRecorder + Web Audio | Records video frames with captions and source audio into a downloadable MP4 or WebM. |
| Storage | Flask in-memory job registry plus local upload/output folders | Simple assignment-sized local workflow. |

The UI always uses local Faster-Whisper. API keys are not required. **The UI does not call Python's render, output, or download endpoints.** Browser exports contain no masks, foreground segmentation, or behind-person hiding, regardless of server RVM settings. The older server renderer is retained for development, but has no UI controls.

## Prerequisites

- Python 3.11+
- Node.js 18+
- FFmpeg and FFprobe on `PATH`
- Enough RAM for the selected Faster-Whisper model
- A current browser with Canvas captureStream, MediaRecorder, and Web Audio support (Chrome/Edge recommended)

The project uses the `base` model by default on CPU. For better accuracy at the cost of speed, set `WHISPER_MODEL_SIZE=small` or `medium` in `server/.env`. If you have a compatible NVIDIA setup, configure Faster-Whisper with `WHISPER_DEVICE=cuda` and an appropriate compute type.

## Setup

### Windows PowerShell

```powershell
# Terminal 1
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r server\requirements.txt
```

```powershell
# Terminal 2
cd client
npm install
```

Copy the server configuration if desired:

```powershell
Copy-Item server\.env.example server\.env
```

### macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r server/requirements.txt
cd client && npm install
cp ../server/.env.example ../server/.env
```

The first Faster-Whisper run downloads the selected CTranslate2 model into the local Hugging Face cache. Subsequent jobs reuse the cached model. The API reports preparation, audio extraction, model loading, and completion phases in `/api/status/:job_id`.

## Browser video download

Click **Download captioned video** after reviewing/saving the transcript. The browser:

1. Loads the bundled font and plays a separate copy of the original video from the beginning.
2. Draws each video frame followed by the same caption painter used in React's live preview.
3. Captures that canvas and routes the source audio through Web Audio into MediaRecorder.
4. Automatically downloads the recorded file and offers a **Download again** link.

Captions are part of the downloaded video pixels; opening the file outside this app still shows them. Browser controls, the UI timecode, and editor panels are not recorded. Preview seeking, playback speed, volume, and mute settings do not change the export. The original source file is not modified.

- Export takes roughly the clip's duration. Keep the tab visible; hiding it stops export with a retry message to avoid a frozen/partial recording.
- A progress indicator and **Cancel export** button are available. Cancelled recordings are discarded.
- MP4 is selected when MediaRecorder supports it, otherwise WebM is used. The extension matches the recorded container. Exact codecs depend on the browser; MP4 does not necessarily imply H.264. Both formats include audio.
- A small browser-side header repair fills missing WebM duration metadata without rewriting the encoded streaming clusters, so players can seek the download.
- Encoding runs locally in real time and depends on device performance. Slow machines can drop frames; long recordings consume browser memory. This path is intended for short clips.
- If the API is hosted on a different origin, its video endpoint must permit CORS (the provided Flask configuration does). Unsupported video formats or recording APIs produce an actionable error.

### Retained server code

`server/render.py` (ASS/libass) and `server/matting.py` (RVM) remain for later development; **neither is used for UI downloads**. The retained RVM path uses the official GPL-3.0 MobileNetV3 FP32 ONNX model, downloaded and checksum-verified from:

```text
https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx
```

For direct server development only, `RVM_ENABLED` defaults to false, `RVM_MODEL_PATH` selects a local model, and `RVM_AUTO_DOWNLOAD` controls model acquisition. The browser download never consults these settings.

## Font selection

No font file or font name was provided by the company. Visual inspection of several frames from `Eclipse.mp4` selected **Montserrat Bold** as the closest legally usable match based on its geometric construction, broad rounded counters, high x-height, heavy weight, and letter spacing. This is documented as a closest match, not as the verified original font.

The font is bundled at `client/public/fonts/Montserrat-Bold.ttf` under the SIL Open Font License, with the license text beside it. The browser loads it with `@font-face` and waits for it before export. Preview and download use the very same Canvas font, eliminating a second renderer's font fallback or sizing differences.

### Larger, matching preview/export text

`client/src/caption-style.json` is the style source. The default is **78 CSS-em pixels per 1080 pixels of video width**, or approximately **30 px on a 416 px-wide player**. Size scales with actual video content, including letterboxed previews. `client/src/caption-canvas.js` draws both the live overlay and exported captions with the same wrapping, baseline, colors, and highlight background.

Both browser paths keep the 15.6% bottom anchor and 3.9% side margins, use normal font spaces, and paint active-word padding without changing text advances. The background is 20% yellow while letters remain fully opaque. No ASS font-size conversion is involved in the UI download.

After changing the caption style or transcript, **download again**. An existing saved video retains its old captions. Downloads are invalidated when the transcript or input changes, and their in-memory object URLs are cleaned up on replacement/reset.

## Run

Open two terminals from the repository root.

Terminal 1 — API:

```powershell
# Activate .venv first
cd server
python app.py
```

API: <http://localhost:5000>

Terminal 2 — UI:

```powershell
cd client
npm run dev
```

Open the Vite URL, normally <http://localhost:3000>.

## User flow

1. Upload an MP4, MOV, AVI, MKV, or WebM file.
2. Choose the spoken language or leave **Auto detect** selected.
3. Click **Generate captions**. The UI always runs local Faster-Whisper.
4. Review and optionally edit the transcript. Saving edits retimes the edited words inside each cue's original timing window.
5. Play or scrub the live preview to check captions.
6. Choose **Download captioned video** and keep the tab visible while it records.
7. Open the automatically downloaded MP4/WebM, or click **Download again** to save another copy.

## Word timing and caption appearance

Faster-Whisper is called with `word_timestamps=True`. Each normalized word contains `start` and `end` seconds. The browser highlights a word only while:

```text
word.start <= video.currentTime < word.end
```

The shared browser painter highlights only words whose start/end interval contains the current media time. Export follows presented video-frame timestamps (`requestVideoFrameCallback`, with a requestAnimationFrame fallback). Pauses between words leave the cue visible with no active highlight. Transcription timestamps are not retimed for download.

Preview and export use the same bundled font, larger size, white text, and translucent yellow active-word box. There is no masking or person occlusion. Minor video-codec compression differences are expected in saved files.

## Architecture

```text
React/Vite
   │ POST /api/upload
   ▼
Flask job registry + local upload folder
   │ POST /api/transcribe
   ▼
FFmpeg mono 16 kHz WAV → Faster-Whisper word timestamps
   │
   ▼
normalized captions { text, segments[], words[] }
   │ PUT /api/captions/:id
   ▼
React preview + shared Canvas caption painter
   │
original video frames + captions + source audio
   │ Canvas captureStream + Web Audio + MediaRecorder
   ▼
local Blob → browser download (MP4/WebM)
```

## Important files

- `client/src/main.jsx` — upload flow, transcript editor, video preview, and status polling.
- `client/src/CaptionOverlay.jsx` — live Canvas overlay mounted by React.
- `client/src/caption-canvas.js` — shared preview/export layout and word highlighting.
- `client/src/browser-export.js` — browser recording, audio capture, cancellation, progress, and cleanup.
- `client/src/webm-metadata.js` — streaming WebM header compatibility after duration repair.
- `client/src/styles.css` — UI styling and bundled font declaration.
- `client/src/caption-style.json` — shared caption size, font, colors, and placement.
- `server/transcribe.py` — Faster-Whisper model cache, transcription, and word normalization.
- `server/render.py` — retained server renderer; not called by the UI.
- `server/matting.py` — retained RVM implementation; not called by browser exports.
- `server/app.py` — Flask API and background jobs.
- `server/test_caption_engine.py` — renderer and transcription normalization tests.

## Checks

```bash
python -m unittest discover server -p "test_*.py"
cd client && npm test && npm run build
```

Browser smoke check: use a short clip with audio, scrub the preview away from the beginning, click download, then inspect the file in a separate player. Confirm full-clip export, visible captions, active-word changes, and audible source audio. Also cancel/retry an export. Network requests must include no `/api/render`, `/api/output`, or `/api/download` calls.
