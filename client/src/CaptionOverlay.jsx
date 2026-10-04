import { useEffect, useRef } from "react";
import { drawCaption, loadCaptionFont } from "./caption-canvas";

export default function CaptionOverlay({ segment, currentTime, style, videoContentBounds }) {
  const canvasRef = useRef(null);
  useEffect(() => {
    let disposed = false;
    const canvas = canvasRef.current;
    if (!canvas || !videoContentBounds) return undefined;
    const { width, height } = videoContentBounds;
    const paint = () => {
      if (disposed) return;
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      const context = canvas.getContext("2d");
      context.scale(ratio, ratio);
      drawCaption(context, segment, currentTime, style, width, height);
    };
    paint();
    // The initial paint keeps playback responsive; repaint once the bundled
    // font is ready. Export treats a font-load failure as a visible error.
    loadCaptionFont(style).then(paint).catch(() => {});
    return () => { disposed = true; };
  }, [segment, currentTime, style, videoContentBounds]);

  if (!videoContentBounds) return null;
  const { left, top, width, height } = videoContentBounds;
  return <canvas ref={canvasRef} className="caption-overlay"
    aria-label={segment?.text || "Captions"}
    style={{ left, top, width, height }} />;
}
