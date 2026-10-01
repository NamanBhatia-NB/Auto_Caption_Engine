# Loom walkthrough (3–5 minutes)

Use this as the recording checklist for the assignment submission. The codebase cannot create a Loom recording itself, so record this short flow after starting the two local processes in `README.md`.

## 1. Product demo (about 90 seconds)

1. Open `http://localhost:3000` and say: “This is Eclipse Caption Studio, a local-first caption engine for vertical short-form video.”
2. Drop in `samples/sample_input.mp4` or a spoken MP4.
3. Select **Local Whisper**, click **Generate captions**, and explain that the first local run downloads the open `ggml-base.bin` model; no API key or paid tool is required.
4. Show the preview overlay and scrub the video. Point out that the current word changes to yellow and receives a soft yellow bar.
5. Click a transcript row, change a word, click **Save edits**, then adjust type scale/position if useful.
6. Click **Render & export**, switch to **Rendered file**, and click **Download final MP4**.

## 2. Repository / architecture (about 90 seconds)

Show these files:

- `client/src/main.jsx` — upload state, polling, live word preview, transcript editing, style controls, and download action.
- `client/src/styles.css` — the dark studio interface and responsive layout.
- `server/app.py` — upload, status, caption update, rendering, preview, download, and health routes.
- `server/transcribe.py` — local FFmpeg Whisper path, optional Groq path, SRT parsing, and word timing normalization.
- `server/render.py` — reusable `ECLIPSE_STYLE`, ASS generation, escaping, word-by-word events, and FFmpeg burn-in.
- `server/test_caption_engine.py` — timestamp parsing, line wrapping/highlight generation, and input safety checks.

Explain the flow: React uploads to Flask; local FFmpeg/whisper.cpp produces timestamped SRT; the server normalizes captions to `{segments, words}`; ASS events switch the highlighted word at its timestamp; FFmpeg/libass burns the result to MP4.

## 3. Technical decision and free-tool constraint (about 60 seconds)

Say:

> “The prompt suggested HyperFrames and ElevenLabs Scribe/OpenAI Whisper. I chose React/Vite, Flask, FFmpeg/libass, and FFmpeg’s whisper.cpp filter because they are free and locally runnable. This removes paid API dependency and keeps the normal workflow private. Groq Whisper is included only as an optional free-tier speed-up; its key is read from `.env` or the current request and never committed.”

Show `server/.env.example` and `.gitignore`. Do not show or type a real API key during the recording.

## 4. Verification (about 30 seconds)

Show the terminal commands and their successful output:

```bash
python -m unittest discover server -p "test_*.py"
cd client && npm run build
```

Mention that `samples/sample_input.mp4` and `samples/sample_output.mp4` are included for a repeatable render demonstration, while the supplied `Eclipse.mp4` is retained as the visual reference.
