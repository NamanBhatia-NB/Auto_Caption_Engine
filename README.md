# Eclipse Caption Studio

A local-first AI caption engine for vertical video. Upload a clip, generate word-timed captions, review/edit the transcript, preview the Eclipse treatment, and export a burned-in MP4.

The included reference (`Eclipse.mp4`) was used to match the caption treatment:

- heavy white, bottom-centred typography;
- 2–5 words per cue with readable line wrapping;
- the currently spoken word turns yellow and receives a translucent yellow word-shaped bar;
- word changes follow transcription timestamps, with a short fade/pop animation;
- placement is around the lower third of a 9:16 canvas rather than hard-coded pixel coordinates for one video.

## Why these tools

The assignment suggested HyperFrames and a hosted transcription API. This implementation uses free, locally runnable alternatives so a reviewer can run the complete flow without a paid account:

| Layer | Choice | Reason |
| --- | --- | --- |
| UI | React + Vite | Small, fast local development server with an editable transcript and live browser preview. |
| Transcription (default) | Faster-Whisper | Free/offline after the local CTranslate2 model is downloaded; it produces real word-level timestamps without an API key. CPU defaults use the faster `base` model, `int8` compute, and beam size 1. |
| Optional hosted transcription | Groq free tier + Whisper | Retained as a server-side fallback for machines that need hosted inference. The key is read from `.env` or passed for the current request only. |
| Composition/rendering | FFmpeg + libass ASS subtitles | Free, deterministic, supports word timing, styling, outlines, per-word override tags, and MP4 output. |

No OpenAI or ElevenLabs calls are required. Free-tier quotas and hosted-provider terms can change, so **Local Whisper is the default and recommended path**.

## Prerequisites

- Python 3.11+ (tested with Python 3.14)
- Node.js 18+
- FFmpeg and FFprobe on `PATH`
- FFmpeg and FFprobe on `PATH` for audio extraction and rendering.
- A machine with enough RAM for the selected Faster-Whisper model. CPU defaults are intentionally optimized for responsiveness; set `WHISPER_MODEL_SIZE=small` or `medium` only when accuracy is worth the extra time.

## Setup

### Windows PowerShell

```powershell
# From the repository root
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r server\requirements.txt

cd client
npm install
```

### macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r server/requirements.txt
cd client && npm install
```

Optional server configuration:

```bash
# Windows: copy server\.env.example server\.env
# macOS/Linux: cp server/.env.example server/.env
```

Put a Groq key only in `server/.env` if you choose the Groq mode. Never put it in source code, the client bundle, a screenshot, or a committed `.env` file. `.env` and downloaded models are ignored by Git.

## Run

Open two terminals from the repository root.

**Terminal 1 — API**

```bash
# Activate .venv first, then:
cd server
python app.py
```

API: <http://localhost:5000>

**Terminal 2 — UI**

```bash
cd client
npm run dev
```

Open the Vite URL printed in the terminal (normally <http://localhost:3000>).

The first Faster-Whisper transcription downloads the selected CTranslate2 model into the Hugging Face cache. With the default `base` model and CPU `int8` settings it is much faster than `medium`/`large-v3-turbo`; the model is reused by subsequent jobs. Configure `WHISPER_MODEL_SIZE`, `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE`, and `WHISPER_BEAM_SIZE` in `server/.env`.

## User flow

1. Drop an MP4/MOV/AVI/MKV/WebM file into the input panel.
2. Choose a spoken language, then choose **Generate captions**. The app always uses local Faster-Whisper; auto detect is available.
3. Scrub the live video preview. The browser overlay follows the current word. Edit any transcript cue and choose **Save edits**; the original timing window is retained and words are re-timed across the edited text.
4. Review the fixed Eclipse caption treatment in the live preview.
5. Choose **Render & export**. FFmpeg burns the captions into `captioned.mp4`.
6. Switch between the live overlay and rendered file, then download **final MP4**.

The server retains Groq support for optional API-level fallback use, but the UI intentionally uses local Faster-Whisper only.

## Architecture

```text
React/Vite
   │  POST /api/upload
   ▼
Flask job registry + server/uploads/<job-id>/input.*
   │
   ├─ Local: FFmpeg audio extraction → Faster-Whisper → real word timings
   └─ Groq: audio extraction → verbose JSON → word timings
           │
           ▼
        normalized captions { text, segments[], words[] }
           │  editable via PUT /api/captions/:id
           ▼
        reusable Eclipse style → ASS → FFmpeg/libass
           │
           ├─ GET /api/output/:id (preview)
           └─ GET /api/download/:id (attachment)
```

Jobs are deliberately in memory for this assignment-sized local app; media remains on disk until the server is restarted or the folders are cleaned. A production deployment would replace the registry with a database/queue and add authentication, quotas, and cleanup.

## Eclipse style implementation

`server/render.py` contains `ECLIPSE_STYLE` and `generate_eclipse_ass()`. The renderer accepts a style object on every render, so the look is not tied to `Eclipse.mp4`:

- coordinates and font size scale from a 1080×1920 authoring canvas;
- default `margin_bottom=300` places the caption around the reference's lower third;
- one ASS event is generated for each active word, so the yellow state tracks timestamps rather than a fixed number of frames;
- libass wraps captions against the actual video margins, keeping each cue on one line whenever it fits without clipping at the frame edges;
- `\fad`, outline, shadow, and the thick translucent `\3c` border create the reference's pop/highlight treatment;
- all user text is escaped before insertion into ASS.

The browser preview mirrors the same word state with CSS; the downloaded video is rendered by the authoritative FFmpeg path.

## Included media

- `Eclipse.mp4` — the supplied reference video used for visual comparison.
- `samples/sample_input.mp4` — a small generated vertical test clip with a free local synthetic voice.
- `samples/sample_output.mp4` — the same clip rendered through the Eclipse renderer, useful for checking the pipeline without downloading Whisper first.

The sample output is a deterministic renderer demonstration; upload your own spoken clip and use Local Whisper for the AI transcription deliverable.

## Checks

```bash
python -m unittest discover server -p "test_*.py"
cd client && npm run build
```

The renderer can also be smoke-tested without a transcription model because `samples/sample_input.mp4` is already included.

## Assignment submission checklist

- [x] Complete source code for upload, transcription, caption edit, preview, render, and download.
- [x] Reusable Eclipse style configuration.
- [x] Free/local default transcription and rendering path.
- [x] API keys excluded from source control.
- [x] Sample input and final output videos included.
- [ ] Record the Loom walkthrough: `LOOM_SCRIPT.md` contains the exact 3–5 minute demo/architecture script.
