import assert from "node:assert/strict";
import test from "node:test";
import { addWebmDuration } from "./webm-metadata.js";

test("duration repair preserves streaming clusters byte-for-byte", async () => {
  const ebml = [0x1a, 0x45, 0xdf, 0xa3, 0x80];
  const segment = [0x18, 0x53, 0x80, 0x67, 0x01, ...Array(7).fill(255)];
  const info = [0x15, 0x49, 0xa9, 0x66, 0x80];
  const clusters = [0x1f, 0x43, 0xb6, 0x75, 0xff, 1, 2, 3, 0x1f, 0x43, 0xb6, 0x75, 0xff];
  const result = await addWebmDuration(new Blob([new Uint8Array([...ebml, ...segment, ...info, ...clusters])], { type: "video/webm" }), 3000);
  const bytes = new Uint8Array(await result.arrayBuffer());
  assert.equal(result.type, "video/webm");
  assert.deepEqual([...bytes.slice(-clusters.length)], clusters);
  const start = ebml.length + segment.length;
  assert.equal(bytes[start + 4], 0x8b); // Info now contains 11 bytes.
  assert.equal(new DataView(bytes.buffer).getFloat64(start + 8), 3000);
});
