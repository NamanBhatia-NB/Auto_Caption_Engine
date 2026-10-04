// Used by both the React preview and the downloaded video. No ASS or matting.
export function activeCaptionAt(segments, time) {
  return segments.find((segment) => time >= segment.start && time < segment.end) || null;
}

const fontLoads = new Map();
export async function loadCaptionFont(style) {
  const font = `700 ${style.font_size}px "${style.font_name}"`;
  if (!fontLoads.has(font)) fontLoads.set(font, document.fonts.load(font));
  let faces;
  try { faces = await fontLoads.get(font); } catch (error) {
    fontLoads.delete(font);
    throw error;
  }
  if (!faces.length) throw new Error("The caption font could not load. Refresh and try again.");
}

export function drawCaption(context, segment, time, style, width, height) {
  if (!segment) return;
  const size = width * style.font_size / style.reference_width;
  const lineHeight = size * style.line_height;
  const maxWidth = width * (1 - 2 * style.side_margin_ratio);
  context.save();
  context.font = `700 ${size}px "${style.font_name}"`;
  context.textBaseline = "alphabetic";
  context.textAlign = "left";
  const space = context.measureText(" ").width;
  const words = segment.words?.length ? segment.words : [{ word: segment.text }];
  const lines = [];
  let line = { words: [], width: 0 };
  for (const word of words) {
    const value = style.uppercase ? word.word.toUpperCase() : word.word;
    // Match overflow-wrap:anywhere for an individual word wider than the frame.
    const pieces = [];
    let piece = "";
    for (const letter of Array.from(value)) {
      if (piece && context.measureText(piece + letter).width > maxWidth) {
        pieces.push(piece);
        piece = "";
      }
      piece += letter;
    }
    if (piece) pieces.push(piece);
    pieces.forEach((text, index) => {
      const wordWidth = context.measureText(text).width;
      if (line.words.length && (index > 0 || line.width + space + wordWidth > maxWidth)) {
        lines.push(line);
        line = { words: [], width: 0 };
      }
      if (line.words.length) line.width += space;
      line.words.push({ ...word, text, x: line.width, width: wordWidth });
      line.width += wordWidth;
    });
  }
  if (line.words.length) lines.push(line);

  // Same Montserrat hhea ascent/descent and CSS line-height as the live DOM
  // treatment: 968 / -251 units at 1000 units/em.
  const baselineOffset = (lineHeight + size * (.968 - .251)) / 2;
  const top = height * (1 - style.bottom_ratio) - lines.length * lineHeight;
  lines.forEach((captionLine, index) => {
    const baseline = top + index * lineHeight + baselineOffset;
    const left = (width - captionLine.width) / 2;
    captionLine.words.forEach((word) => {
      const x = left + word.x;
      const active = time >= word.start && time < word.end;
      if (active) {
        const padding = size * .065;
        context.globalAlpha = style.background_alpha / 100;
        context.fillStyle = style.highlight_background;
        context.beginPath();
        context.roundRect(x - padding, baseline - size * .968 - padding,
          word.width + padding * 2, size * 1.219 + padding * 2, size * .1);
        context.fill();
        context.globalAlpha = 1;
      }
      context.fillStyle = active ? style.highlight_color : style.primary_color;
      context.fillText(word.text, x, baseline);
    });
  });
  context.restore();
}
