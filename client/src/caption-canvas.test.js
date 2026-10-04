import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { activeCaptionAt, drawCaption } from "./caption-canvas.js";

const style = JSON.parse(readFileSync(new URL("./caption-style.json", import.meta.url)));

function canvas() {
  return {
    text: [], boxes: [], globalAlpha: 1,
    save() {}, restore() {}, beginPath() {}, fill() {},
    measureText(value) { return { width: value.length * parseFloat(this.font.split(" ")[1]) / 2 }; },
    fillText(text, x, y) { this.text.push({ text, x, y, color: this.fillStyle, alpha: this.globalAlpha }); },
    roundRect(x, y, width, height) { this.boxes.push({ x, y, width, height, alpha: this.globalAlpha }); },
  };
}

const segment = { text: "Maine life mein", start: 0, end: 3, words: [
  { word: "Maine", start: 0, end: 1 },
  { word: "life", start: 1.2, end: 2 },
  { word: "mein", start: 2, end: 3 },
] };

test("highlight respects word boundaries and silence without shifting the line", () => {
  let positions;
  for (const [time, highlighted] of [[0, "Maine"], [1, null], [1.2, "life"], [2, "mein"]]) {
    const ctx = canvas();
    drawCaption(ctx, segment, time, style, 720, 1280);
    assert.deepEqual(ctx.text.filter(w => w.color === style.highlight_color).map(w => w.text), highlighted ? [highlighted] : []);
    assert.ok(ctx.text.every(w => w.alpha === 1), "only background boxes may be translucent");
    assert.equal(ctx.boxes.length, highlighted ? 1 : 0);
    if (highlighted) assert.equal(ctx.boxes[0].alpha, .2);
    const layout = ctx.text.map(({ x, y }) => [x, y]);
    if (positions) assert.deepEqual(layout, positions);
    positions = layout;
  }
  assert.equal(activeCaptionAt([segment], 3), null);
});

test("preview and native export use proportionally identical geometry", () => {
  const preview = canvas(), exported = canvas();
  drawCaption(preview, segment, 1.5, style, 360, 640);
  drawCaption(exported, segment, 1.5, style, 1080, 1920);
  exported.text.forEach((word, i) => {
    assert.ok(Math.abs(word.x - preview.text[i].x * 3) < .001);
    assert.ok(Math.abs(word.y - preview.text[i].y * 3) < .001);
    assert.equal(word.color, preview.text[i].color);
  });
});

test("long cues and unbroken words wrap within the video's safe margins", () => {
  const ctx = canvas();
  drawCaption(ctx, { words: [{ word: "longword".repeat(12), start: 0, end: 2 }] }, 1, style, 360, 640);
  assert.ok(new Set(ctx.text.map(w => w.y)).size > 1);
  for (const word of ctx.text) {
    assert.ok(word.x >= 360 * style.side_margin_ratio - .001);
    assert.ok(word.x + ctx.measureText(word.text).width <= 360 * (1 - style.side_margin_ratio) + .001);
  }
});
