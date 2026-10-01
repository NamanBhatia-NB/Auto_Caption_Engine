import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const API = import.meta.env.VITE_API_URL || '';
const ACCEPTED = '.mp4,.mov,.avi,.mkv,.webm,video/*';
const DEFAULT_STYLE = {
  font_name: 'Arial',
  font_size: 52,
  primary_color: '#FFFFFF',
  highlight_color: '#FFE000',
  margin_bottom: 300,
  max_words: 5,
  uppercase: false,
};

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function App() {
  const [file, setFile] = useState(null);
  const [job, setJob] = useState(null);
  const [captions, setCaptions] = useState(null);
  const style = DEFAULT_STYLE;
  const [language, setLanguage] = useState('auto');
  const [message, setMessage] = useState('Drop a video to begin.');
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [showRendered, setShowRendered] = useState(false);
  const [captionsDirty, setCaptionsDirty] = useState(false);
  const videoRef = useRef(null);
  const animationFrameRef = useRef(null);
  const inputRef = useRef(null);

  const busy = job && ['transcribing', 'rendering'].includes(job.status);
  const hasVideo = Boolean(job?.job_id);
  const hasCaptions = Boolean(captions?.segments?.length);
  const rendered = job?.status === 'rendered' && job?.has_output;
  const mediaSrc = rendered && showRendered
    ? `${API}/api/output/${job.job_id}`
    : hasVideo
      ? `${API}/api/preview/${job.job_id}`
      : '';

  const activeSegment = useMemo(() => {
    if (!captions?.segments) return null;
    return captions.segments.find((segment) => currentTime >= segment.start && currentTime < segment.end) || null;
  }, [captions, currentTime]);

  const syncJob = useCallback(async (jobId) => {
    try {
      const next = await request(`/api/status/${jobId}`);
      setJob((previous) => ({ ...(previous || {}), ...next, job_id: jobId }));
      if (next.has_captions && !captions) {
        const captionData = await request(`/api/captions/${jobId}`);
        setCaptions(captionData);
      }
      if (next.status === 'transcribed') {
        setMessage('Captions are ready. Review the transcript or render the Eclipse preview.');
      } else if (next.status === 'transcribing') {
        setMessage(`Whisper ${next.phase || 'is transcribing'}… ${next.progress || 0}%`);
      } else if (next.status === 'rendered') {
        setMessage('Export is ready. Preview it here or download the MP4.');
        setShowRendered(true);
      } else if (next.status === 'error') {
        setError(next.error || 'The job failed.');
      }
    } catch (requestError) {
      setError(requestError.message);
    }
  }, [captions]);

  useEffect(() => {
    if (!job?.job_id || !busy) return undefined;
    const timer = window.setInterval(() => syncJob(job.job_id), 2500);
    return () => window.clearInterval(timer);
  }, [busy, job?.job_id, syncJob]);

  function chooseFile(nextFile) {
    if (!nextFile) return;
    if (!nextFile.type.startsWith('video/') && !/\.(mp4|mov|avi|mkv|webm)$/i.test(nextFile.name)) {
      setError('Please choose an MP4, MOV, AVI, MKV, or WebM video.');
      return;
    }
    setFile(nextFile);
    setError('');
    setMessage(`${nextFile.name} is ready to upload.`);
  }

  async function uploadVideo(event) {
    event?.preventDefault();
    if (!file) return setError('Choose a video before uploading.');
    setError('');
    setMessage('Uploading video and reading its metadata…');
    const form = new FormData();
    form.append('video', file);
    try {
      const result = await request('/api/upload', { method: 'POST', body: form });
      setJob({ ...result, job_id: result.job_id });
      setCaptions(null);
      setCaptionsDirty(false);
      setShowRendered(false);
      setCurrentTime(0);
      setMessage('Upload complete. Whisper is ready to generate captions.');
    } catch (requestError) {
      setError(requestError.message);
      setMessage('Upload could not be completed.');
    }
  }

  async function transcribe() {
    if (!job?.job_id) return;
    setError('');
    setMessage('Starting Faster-Whisper. The first run downloads the local model once…');
    setJob((previous) => ({ ...previous, status: 'transcribing', phase: 'preparing audio', progress: 5 }));
    try {
      await request('/api/transcribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: job.job_id, mode: 'local', language }),
      });
    } catch (requestError) {
      setError(requestError.message);
      await syncJob(job.job_id);
    }
  }

  async function saveCaptionEdits() {
    if (!job?.job_id || !captions) return;
    setError('');
    try {
      const result = await request(`/api/captions/${job.job_id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ segments: captions.segments }),
      });
      setCaptions(result.captions);
      setCaptionsDirty(false);
      setShowRendered(false);
      setJob((previous) => ({ ...previous, status: 'transcribed', has_output: false }));
      setMessage('Transcript edits saved. Render again to update the video.');
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function renderVideo() {
    if (!job?.job_id || !hasCaptions) return;
    setError('');
    setMessage('Rendering the Eclipse overlay with FFmpeg…');
    setShowRendered(false);
    setJob((previous) => ({ ...previous, status: 'rendering' }));
    try {
      await request('/api/render', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: job.job_id, style }),
      });
    } catch (requestError) {
      setError(requestError.message);
      await syncJob(job.job_id);
    }
  }

  function updateSegment(index, value) {
    setCaptions((previous) => ({
      ...previous,
      segments: previous.segments.map((segment, segmentIndex) => segmentIndex === index ? { ...segment, text: value } : segment),
    }));
    setCaptionsDirty(true);
  }

  function reset() {
    setFile(null);
    setJob(null);
    setCaptions(null);
    setError('');
    setMessage('Drop a video to begin.');
    setShowRendered(false);
    setCaptionsDirty(false);
  }

  const step = !hasVideo ? 1 : !hasCaptions ? 2 : !rendered ? 3 : 4;

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark"><span /></div>
          <div><strong>ECLIPSE</strong><small>CAPTION STUDIO</small></div>
        </div>
        <div className="header-note"><span className="status-dot" /> 100% local workflow · no paid tools</div>
        <button className="ghost-button" onClick={reset}>New project <span>⌘ N</span></button>
      </header>

      <main className="workspace">
        <section className="hero-copy">
          <div className="eyebrow"><span>01</span> AI VIDEO TOOLKIT</div>
          <h1>Make every word<br /><em>land.</em></h1>
          <p>Upload a video. Generate precise word-level captions. Export them in the bold, kinetic Eclipse style.</p>
        </section>

        <nav className="stepper" aria-label="Project progress">
          {['Upload', 'Transcribe', 'Style & edit', 'Export'].map((label, index) => (
            <div className={`step ${step === index + 1 ? 'current' : ''} ${step > index + 1 ? 'complete' : ''}`} key={label}>
              <span>{step > index + 1 ? '✓' : `0${index + 1}`}</span>{label}
            </div>
          ))}
        </nav>

        {error && <div className="error-banner"><b>Something went wrong</b><span>{error}</span><button onClick={() => setError('')}>×</button></div>}

        <div className="studio-grid">
          <section className="main-column">
            <div className="panel preview-panel">
              <div className="panel-heading">
                <div><span className="panel-kicker">LIVE CANVAS</span><h2>{showRendered && rendered ? 'Rendered output' : 'Caption preview'}</h2></div>
                <div className="canvas-meta">{job?.video_info ? `${job.video_info.width} × ${job.video_info.height}` : '9:16 vertical'}</div>
              </div>
              <div className={`video-stage ${!mediaSrc ? 'empty-stage' : ''}`}>
                {mediaSrc ? (
                  <>
                    <video
                      ref={videoRef}
                      key={mediaSrc}
                      src={mediaSrc}
                      controls
                      playsInline
                      onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
                      onLoadedMetadata={(event) => {
                        setDuration(event.currentTarget.duration || job?.video_info?.duration || 0);
                        setCurrentTime(event.currentTarget.currentTime);
                      }}
                      onPlay={(event) => {
                        const video = event.currentTarget;
                        const updateTime = () => {
                          setCurrentTime(video.currentTime);
                          if (!video.paused && !video.ended) {
                            animationFrameRef.current = window.requestAnimationFrame(updateTime);
                          }
                        };
                        window.cancelAnimationFrame(animationFrameRef.current);
                        updateTime();
                      }}
                      onPause={(event) => {
                        window.cancelAnimationFrame(animationFrameRef.current);
                        setCurrentTime(event.currentTarget.currentTime);
                      }}
                      onSeeked={(event) => setCurrentTime(event.currentTarget.currentTime)}
                    />
                    {!showRendered && <CaptionOverlay segment={activeSegment} currentTime={currentTime} uppercase={style.uppercase} />}
                    <div className="timecode">{formatTime(currentTime)} / {formatTime(duration || job?.video_info?.duration || 0)}</div>
                  </>
                ) : (
                  <div className="stage-placeholder"><div className="play-orb">▶</div><span>Your 9:16 preview appears here</span></div>
                )}
              </div>
              {rendered && <div className="preview-switch"><button className={!showRendered ? 'selected' : ''} onClick={() => setShowRendered(false)}>Live overlay</button><button className={showRendered ? 'selected' : ''} onClick={() => setShowRendered(true)}>Rendered file</button></div>}
            </div>

            <div className="panel transcript-panel">
              <div className="panel-heading">
                <div><span className="panel-kicker">WORD TIMELINE</span><h2>Transcript <span className="count-pill">{captions?.segments?.length || 0} cues</span></h2></div>
                {captionsDirty && <button className="small-button" onClick={saveCaptionEdits}>Save edits</button>}
              </div>
              {!hasCaptions ? (
                <div className="empty-transcript"><div className="waveform"><i /><i /><i /><i /><i /><i /><i /><i /><i /></div><p>Your generated captions will appear here for review.</p><small>Every cue keeps its original timing.</small></div>
              ) : (
                <div className="transcript-list">
                  {captions.segments.map((segment, index) => (
                    <div className={`transcript-row ${activeSegment === segment ? 'active' : ''}`} key={`${segment.start}-${index}`} onClick={() => { if (videoRef.current) videoRef.current.currentTime = segment.start; }}>
                      <time>{formatTime(segment.start)}</time>
                      <input aria-label={`Caption ${index + 1}`} value={segment.text} onChange={(event) => updateSegment(index, event.target.value)} />
                      <span className="word-count">{segment.words?.length || segment.text.split(/\s+/).length} words</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </section>

          <aside className="side-column">
            <section className="panel action-panel">
              <span className="panel-kicker">PROJECT INPUT</span>
              <h2>Start with a video</h2>
              <div className={`dropzone ${dragging ? 'dragging' : ''} ${file ? 'has-file' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); chooseFile(event.dataTransfer.files[0]); }} onClick={() => inputRef.current?.click()}>
                <input ref={inputRef} type="file" accept={ACCEPTED} hidden onChange={(event) => chooseFile(event.target.files[0])} />
                <div className="upload-icon">↥</div>
                <strong>{file ? file.name : 'Drop your video here'}</strong>
                <span>{file ? formatBytes(file.size) : 'or click to browse · MP4, MOV, WebM'}</span>
              </div>
              <button className="primary-button full" disabled={!file || busy} onClick={uploadVideo}>{hasVideo ? 'Replace with this video' : 'Upload video'} <b>→</b></button>
              {job?.video_info && <div className="file-facts"><span><b>{formatTime(job.video_info.duration)}</b> duration</span><span><b>{job.video_info.width}×{job.video_info.height}</b> canvas</span></div>}
            </section>

            <section className="panel settings-panel">
              <div className="panel-heading compact"><div><span className="panel-kicker">CAPTION ENGINE</span><h2>Generate Whisper captions</h2></div><span className="free-badge">FREE</span></div>
              <label className="field-label" htmlFor="language">Spoken language</label>
              <select id="language" value={language} onChange={(event) => setLanguage(event.target.value)}><option value="auto">Auto detect</option><option value="en">English</option><option value="hi">Hindi</option><option value="es">Spanish</option><option value="fr">French</option><option value="de">German</option></select>
              <button className="primary-button full" disabled={!hasVideo || busy} onClick={transcribe}>{busy && job.status === 'transcribing' ? <Spinner /> : 'Generate captions'} <b>✦</b></button>
              <p className="microcopy">Uses Faster-Whisper locally with word-level timestamps. No account or key required.</p>
            </section>

            <button className="export-button" disabled={!hasCaptions || busy || captionsDirty} onClick={renderVideo}><span className="export-icon">↓</span><span><b>{busy && job.status === 'rendering' ? 'Rendering video…' : 'Render & export'}</b><small>{captionsDirty ? 'Save transcript edits first' : 'Burn Eclipse captions into MP4'}</small></span><strong>→</strong></button>
            {rendered && <a className="download-button" href={`${API}/api/download/${job.job_id}`}><span>↓</span> Download final MP4</a>}
          </aside>
        </div>
        <div className="toast-line"><span className={busy ? 'loader-dot' : 'status-dot'} /> {message}</div>
      </main>
      <footer><span>ECLIPSE CAPTION STUDIO</span><span>Built with FFmpeg · Whisper · React</span><span>Local-first by design</span></footer>
    </div>
  );
}

function CaptionOverlay({ segment, currentTime, uppercase }) {
  if (!segment) return null;
  return <div className="caption-overlay"><div className="caption-line">{(segment.words || []).map((word, index) => {
    const value = uppercase ? word.word.toUpperCase() : word.word;
    const active = currentTime >= word.start && currentTime < word.end;
    return <span className={active ? 'caption-word active' : 'caption-word'} key={`${word.start}-${index}`}>{value}</span>;
  })}</div></div>;
}

function Spinner() { return <span className="spinner" aria-label="Working" />; }
function formatTime(value) { if (!Number.isFinite(value)) return '00:00'; const seconds = Math.max(0, Math.floor(value)); return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`; }
function formatBytes(bytes) { if (!bytes) return '0 KB'; const units = ['B', 'KB', 'MB', 'GB']; const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1); return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`; }

export default App;

createRoot(document.getElementById('root')).render(<App />);
