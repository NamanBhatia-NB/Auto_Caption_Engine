# Loom walkthrough script

Target length: 3–5 minutes. Show the running app in the browser and, briefly, the repository files or terminal. Speak naturally; the wording below is a ready-to-read script.

## 0:00–0:25 — Introduce the result

“Hi, this is my Eclipse Caption Studio. It is a local-first captioning tool for vertical video. The user uploads a video, the app transcribes it locally with Faster-Whisper, shows a word-timed live preview, lets the transcript be reviewed or edited, and finally exports an MP4 with the captions burned in.”

## 0:25–0:55 — Explain the design reference

“The visual treatment is based on the supplied Eclipse reference. The caption is positioned in the lower third of the vertical canvas. Normal words are clean white text. The currently spoken word changes to solid yellow. I intentionally removed black shadows, black outlines, white strokes, translucent yellow boxes, and fade animations so the live preview and rendered output use the same simple design.”

## 0:55–1:25 — Upload and transcription

“I’ll start by selecting a vertical video. After upload, the API reads the video metadata and keeps the source file locally. I select the spoken language, or I can leave auto detection enabled, and click Generate captions. There is no transcription-engine selector because the current workflow always uses local Faster-Whisper.”

“Before transcription, FFmpeg extracts mono 16 kilohertz WAV audio. Faster-Whisper uses a cached local CTranslate2 model. The default CPU configuration uses the base model, int8 computation, beam size one, and word timestamps. The first run can download the model, while later runs reuse it. The status message reports the current phase, such as audio extraction or model loading.”

## 1:25–2:05 — Word timing and transcript review

“Faster-Whisper returns word-level start and end timestamps. The app normalizes those into caption segments of a few words. In the browser, a word is highlighted only while the video time is greater than or equal to that word’s start and less than its end. The renderer uses the same timestamps for the ASS events, so pauses and changes in speaking speed do not cause the highlight to continue into the next word.”

“This transcript is editable. If I change a cue and save it, the server keeps the cue’s original start and end window and retimes the edited words inside that window. That lets the user correct a recognition mistake without changing the surrounding video timing.”

## 2:05–2:40 — Live preview

“Here is the live overlay. The browser preview uses the same intended style as the renderer: Arial-based white text, no outline, no shadow, and no background box. Only the active word becomes yellow. The caption is constrained to the video frame and wraps at word boundaries so words are not cut off at the left or right edge.”

“I can scrub the video and observe the active word changing according to its timestamp.”

## 2:40–3:20 — Render and export

“Now I’ll render the video. The server generates ASS subtitles with one event per word. Each event uses the word’s own start and end time. FFmpeg and libass burn those subtitles into the output MP4. The rendered file is the authoritative export, while the live overlay is the browser equivalent used for quick review.”

“After rendering, I can switch between the live source preview and the rendered output, then download the final MP4.”

## 3:20–3:55 — Architecture and tools

“The frontend is React and Vite. The backend is Flask. FFmpeg handles metadata, audio extraction, and final video encoding. Faster-Whisper performs local speech recognition and returns word timing data. ASS and libass provide deterministic subtitle rendering. Jobs are kept in an in-memory Flask registry for this assignment-sized local application, with uploaded and rendered files stored locally.”

“HyperFrames is not used. It was part of the suggested assignment tooling, but I implemented the complete workflow with free local tools instead, so no paid account or hosted API is needed.”

## 3:55–4:15 — Close

“The main deliverable is a working local caption pipeline: upload, transcribe, inspect and correct, preview, render, and download. The checks include the Python unit tests for timestamp and ASS behavior and the Vite production build. Thank you.”
