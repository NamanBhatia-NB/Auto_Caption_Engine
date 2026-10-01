# Eclipse Caption Studio

A local-first AI caption engine for vertical video. Upload a clip, generate word-timed captions, review the transcript, preview the fixed Eclipse treatment, and export a burned-in MP4.

The caption design is based on the supplied `eclipse.png` and `Eclipse.mp4` references:

- clean white, lower-third typography;
- no black outline, drop shadow, glow, or white stroke;
- the currently spoken word changes to solid yellow only;
- no translucent yellow background or word-shaped box;
- word changes follow the word's own start/end timestamps;
- captions are grouped into short cues and wrapped by libass at the real video margins.

## Current approach

| Layer | Current implementation | Purpose |
| --- | --- | --- |
| UI | React + Vite | Upload, transcript review, live preview, and export controls. |
| Transcription | Faster-Whisper | Local CTranslate2 Whisper model with real word-level timestamps. |
| Default local model | `base`, CPU `int8`, beam size `1` | Faster local processing while retaining word timestamps. |
| Audio preparation | FFmpeg | Converts the uploaded video to mono 16 kHz WAV before transcription. |
| Caption rendering | FFmpeg + libass ASS | Burns deterministic word-timed captions into the MP4. |
| Storage | Flask in-memory job registry plus local upload/output folders | Simple assignment-sized local workflow. |

The UI always uses local Faster-Whisper. There is no transcription-engine selector and no hosted transcription dependency in the current flow. API keys are not required.

## Is HyperFrames used?

No. HyperFrames is **not used** in this implementation. The assignment suggested it as one possible tool, but this project uses React/Vite, Flask, Faster-Whisper, FFmpeg, and libass so the complete workflow can run locally without a paid service.

## Prerequisites

- Python 3.11+
- Node.js 18+
- FFmpeg and FFprobe on `PATH`
- Enough RAM for the selected Faster-Whisper model

The project uses the `base` model by default on CPU. For better accuracy at the cost of speed, set `WHISPER_MODEL_SIZE=small` or `medium` in `server/.env`. If you have a compatible NVIDIA setup, configure Faster-Whisper with `WHISPER_DEVICE=cuda` and an appropriate compute type.

## Setup

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r server\requirements.txt

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
5. Use **Live overlay** to preview the same clean caption treatment that the renderer uses.
6. Choose **Render & export** to burn the captions into an MP4.
7. Preview the rendered file or download the final MP4.

## Word timing and caption appearance

Faster-Whisper is called with `word_timestamps=True`. Each normalized word contains `start` and `end` seconds. The browser highlights a word only while:

```text
word.start <= video.currentTime < word.end
```

The ASS renderer creates one event for each word using the same boundaries. It changes only the active word's fill color to yellow. It deliberately does not use `\\bord`, `\\shad`, `\\3c`, a translucent background, or fade animation for the active word. The default ASS style also has zero outline and zero shadow.

The browser preview mirrors the same design with plain white text and solid yellow active text. It is constrained to the video frame and wraps at word boundaries to avoid clipping.

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
ASS word events → FFmpeg/libass → captioned.mp4
   ├─ GET /api/preview/:id
   ├─ GET /api/output/:id
   └─ GET /api/download/:id
```

## Important files

- `client/src/main.jsx` — upload flow, transcript editor, video preview, and status polling.
- `client/src/styles.css` — UI and clean live caption styling.
- `server/transcribe.py` — Faster-Whisper model cache, transcription, and word normalization.
- `server/render.py` — fixed Eclipse ASS style and word-timed subtitle generation.
- `server/app.py` — Flask API and background jobs.
- `server/test_caption_engine.py` — renderer and transcription normalization tests.
- `LOOM_SCRIPT.md` — exact walkthrough script for the requested recording.

## Checks

```bash
python -m unittest discover server -p "test_*.py"
cd client && npm run build
```

## Assignment status

- [x] Upload, metadata, and local preview.
- [x] Local Faster-Whisper transcription with word-level timestamps.
- [x] Editable transcript with retimed edits.
- [x] Clean Eclipse-style live and rendered captions.
- [x] FFmpeg/libass MP4 export and download.
- [x] Local-only workflow without HyperFrames or paid APIs.
- [ ] Record the Loom walkthrough using `LOOM_SCRIPT.md`.
