// MediaRecorder emits streaming WebM without Duration. Edit only the small
// Segment/Info header, leaving encoded clusters untouched (they may themselves
// have unknown lengths). No video transcoding or server work is involved.
export async function addWebmDuration(blob, milliseconds) {
  const header = new Uint8Array(await blob.slice(0, 65536).arrayBuffer());
  function element(offset) {
    function length(first) {
      for (let n = 1; n <= 8; n++) if (first & (1 << (8 - n))) return n;
      throw new Error("Invalid WebM header");
    }
    const idLength = length(header[offset]);
    const sizeOffset = offset + idLength;
    const sizeLength = length(header[sizeOffset]);
    let id = 0;
    for (let i = offset; i < sizeOffset; i++) id = id * 256 + header[i];
    let size = header[sizeOffset] & ((1 << (8 - sizeLength)) - 1);
    for (let i = 1; i < sizeLength; i++) size = size * 256 + header[sizeOffset + i];
    const data = sizeOffset + sizeLength;
    return { id, offset, sizeOffset, sizeLength, data, end: data + size };
  }
  function sizeBytes(size) {
    let count = 1;
    while (size >= 2 ** (7 * count) - 1) count++;
    const bytes = new Uint8Array(count);
    for (let i = count - 1; i >= 0; i--) {
      bytes[i] = size % 256;
      size = Math.floor(size / 256);
    }
    bytes[0] |= 1 << (8 - count);
    return bytes;
  }
  let segment;
  for (let offset = 0; offset < header.length;) {
    const item = element(offset);
    if (item.id === 0x18538067) { segment = item; break; }
    offset = item.end;
  }
  if (!segment) throw new Error("Cannot finalize the recorded WebM header.");
  let info;
  for (let offset = segment.data; offset < header.length;) {
    const item = element(offset);
    if (item.id === 0x1549a966) { info = item; break; }
    offset = item.end;
  }
  if (!info || info.end > header.length) throw new Error("Cannot find the recorded WebM timing header.");
  let scale = 1000000;
  for (let offset = info.data; offset < info.end;) {
    const item = element(offset);
    if (item.id === 0x4489) return blob; // Already finalized by the browser.
    if (item.id === 0x2ad7b1) {
      scale = 0;
      for (let i = item.data; i < item.end; i++) scale = scale * 256 + header[i];
    }
    offset = item.end;
  }
  const duration = new Uint8Array(11);
  duration.set([0x44, 0x89, 0x88]); // Duration ID, eight-byte float payload.
  new DataView(duration.buffer).setFloat64(3, milliseconds * 1000000 / scale);
  const unknown = new Uint8Array(segment.sizeLength).fill(255);
  unknown[0] = (1 << (9 - segment.sizeLength)) - 1;
  return new Blob([
    blob.slice(0, segment.sizeOffset), unknown,
    blob.slice(segment.data, info.sizeOffset), sizeBytes(info.end - info.data + duration.length),
    blob.slice(info.data, info.end), duration, blob.slice(info.end),
  ], { type: blob.type });
}
