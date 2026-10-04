import { activeCaptionAt, drawCaption, loadCaptionFont } from "./caption-canvas";
import { addWebmDuration } from "./webm-metadata";

export function recordingFormat() {
  if (!globalThis.MediaRecorder || !HTMLCanvasElement.prototype.captureStream) {
    throw new Error("Browser video export is unavailable. Use a current Chrome, Edge, or Safari browser.");
  }
  const type = [
    "video/mp4;codecs=avc1,mp4a.40.2",
    "video/mp4",
    "video/webm;codecs=vp8,opus",
    "video/webm",
  ].find((candidate) => MediaRecorder.isTypeSupported(candidate));
  if (!type) throw new Error("This browser cannot record video. Try a current Chrome or Edge browser.");
  return { mimeType: type, extension: type.startsWith("video/mp4") ? "mp4" : "webm" };
}

function waitForMedia(video, event, signal) {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timeout);
      video.removeEventListener(event, done);
      video.removeEventListener("error", failed);
      signal.removeEventListener("abort", aborted);
    };
    const done = () => { cleanup(); resolve(); };
    const failed = () => { cleanup(); reject(new Error("Cannot decode this video in the browser. Try an H.264 MP4.")); };
    const aborted = () => { cleanup(); reject(new DOMException("Export cancelled", "AbortError")); };
    const timeout = setTimeout(() => { cleanup(); reject(new Error("Loading the source video timed out. Please retry.")); }, 30000);
    video.addEventListener(event, done, { once: true });
    video.addEventListener("error", failed, { once: true });
    signal.addEventListener("abort", aborted, { once: true });
    if (signal.aborted) aborted();
  });
}

// Records a separate playback from the beginning, leaving preview seek/mute
// settings alone. Audio goes straight to the recording, never the speakers.
export async function exportCaptionedVideo({ src, segments, style, fps = 30, signal, onProgress }) {
  const format = recordingFormat();
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) throw new Error("This browser cannot capture the video's audio.");
  const audio = new AudioContextClass();
  const video = document.createElement("video");
  let source;
  let destination;
  let stream;
  let recorder;
  let videoFrame;
  let animationFrame;
  let timer;
  let endTimer;
  let visibilityHandler;
  let abortHandler;
  try {
    // Resume while we still have the button's user activation.
    await audio.resume();
    await loadCaptionFont(style);
    signal.throwIfAborted();
    video.crossOrigin = "anonymous";
    video.playsInline = true;
    video.preload = "auto";
    video.style.cssText = "position:fixed;left:-10000px;width:1px;height:1px;pointer-events:none";
    document.body.append(video);
    const loaded = waitForMedia(video, "loadeddata", signal);
    video.src = src;
    video.load();
    await loaded;
    if (!Number.isFinite(video.duration) || !video.videoWidth) throw new Error("The video duration or size could not be read.");

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) throw new Error("Cannot create the video export canvas.");
    source = audio.createMediaElementSource(video);
    destination = audio.createMediaStreamDestination();
    source.connect(destination);
    stream = canvas.captureStream(Math.max(1, Math.min(fps || 30, 60)));
    destination.stream.getAudioTracks().forEach((track) => stream.addTrack(track));
    recorder = new MediaRecorder(stream, {
      mimeType: format.mimeType,
      videoBitsPerSecond: Math.min(20000000, Math.max(4000000, canvas.width * canvas.height * 6)),
      audioBitsPerSecond: 192000,
    });
    const chunks = [];
    let failure;
    let finished = false;
    let recordingStarted = 0;
    let recordingEnded = 0;
    let pausedAt = null;
    let pausedMs = 0;
    let lastTime = 0;
    let lastAdvanced = performance.now();
    const draw = (time) => {
      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      drawCaption(context, activeCaptionAt(segments, time), time, style, canvas.width, canvas.height);
    };
    // A painted first frame exists before the recording clock starts.
    draw(0);
    const stopped = new Promise((resolve) => { recorder.onstop = resolve; });
    const finish = (error) => {
      if (finished) return;
      finished = true;
      failure = error;
      recordingEnded = performance.now();
      if (pausedAt !== null) pausedMs += recordingEnded - pausedAt;
      if (recorder.state !== "inactive") recorder.stop();
    };
    abortHandler = () => finish(new DOMException("Export cancelled", "AbortError"));
    visibilityHandler = () => {
      if (document.hidden) finish(new Error("Export stopped because the tab was hidden. Keep this tab visible and retry."));
    };
    signal.addEventListener("abort", abortHandler, { once: true });
    document.addEventListener("visibilitychange", visibilityHandler);
    recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
    recorder.onerror = (event) => finish(event.error || new Error("Browser video recording failed."));
    video.onerror = () => finish(new Error("Video playback failed during export."));
    video.onended = () => {
      // Flush the last decoded frame before stopping the recorder. Otherwise
      // some encoders end the video track before the audio track finishes.
      draw(Math.max(0, video.duration - 1 / (fps || 30)));
      stream.getVideoTracks()[0].requestFrame?.();
      endTimer = setTimeout(() => finish(), 100);
    };
    video.onwaiting = () => {
      if (recorder.state === "recording") {
        pausedAt = performance.now();
        recorder.pause();
      }
    };
    video.onplaying = () => {
      if (recorder.state === "paused") {
        pausedMs += performance.now() - pausedAt;
        pausedAt = null;
        recorder.resume();
      }
    };
    const onFrame = (_now, metadata) => {
      try {
        draw(metadata.mediaTime);
        videoFrame = video.requestVideoFrameCallback(onFrame);
      } catch (error) { finish(error); }
    };
    const onAnimation = () => {
      try {
        draw(video.currentTime);
        animationFrame = requestAnimationFrame(onAnimation);
      } catch (error) { finish(error); }
    };
    if (video.requestVideoFrameCallback) videoFrame = video.requestVideoFrameCallback(onFrame);
    else animationFrame = requestAnimationFrame(onAnimation);
    timer = setInterval(() => {
      onProgress?.(Math.min(99, Math.floor(video.currentTime / video.duration * 100)));
      if (video.currentTime !== lastTime) {
        lastTime = video.currentTime;
        lastAdvanced = performance.now();
      } else if (performance.now() - lastAdvanced > 30000) {
        finish(new Error("Video playback stalled during export. Please retry."));
      }
    }, 250);
    signal.throwIfAborted();
    if (document.hidden) throw new Error("Export stopped because the tab was hidden. Keep this tab visible and retry.");
    recordingStarted = performance.now();
    recorder.start(1000);
    try { await video.play(); } catch (error) { finish(error); }
    await stopped;
    if (failure) throw failure;
    if (!chunks.length) throw new Error("The browser produced an empty recording.");
    let blob = new Blob(chunks, { type: recorder.mimeType });
    if (format.extension === "webm") {
      // MediaRecorder omits WebM duration; fix metadata so downloaded files seek.
      blob = await addWebmDuration(blob, recordingEnded - recordingStarted - pausedMs);
    }
    signal.throwIfAborted();
    onProgress?.(100);
    return { blob, extension: format.extension };
  } finally {
    clearInterval(timer);
    clearTimeout(endTimer);
    cancelAnimationFrame(animationFrame);
    if (videoFrame !== undefined) video.cancelVideoFrameCallback(videoFrame);
    if (abortHandler) signal.removeEventListener("abort", abortHandler);
    if (visibilityHandler) document.removeEventListener("visibilitychange", visibilityHandler);
    if (recorder && recorder.state !== "inactive") recorder.stop();
    video.pause();
    source?.disconnect();
    stream?.getTracks().forEach((track) => track.stop());
    destination?.stream.getTracks().forEach((track) => track.stop());
    video.removeAttribute("src");
    video.load();
    video.remove();
    await audio.close();
  }
}
