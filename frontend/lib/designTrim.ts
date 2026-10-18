/**
 * A design's transparent padding, found the way the server finds it (pipeline/mockups.py
 * ``trim_box_of``): the box of its pixels with alpha above TRIM_ALPHA, grown by TRIM_EDGE
 * and kept inside the image. The editor previews a design the seller picks in the browser
 * cropped to this box, so the preview and the mockup place it identically. Nothing is sent.
 */

export const TRIM_ALPHA = 8;
export const TRIM_EDGE = 2;

export type Box = [number, number, number, number]; // left, top, right, bottom (exclusive)

/** ``rgba``: the image's pixels, 4 bytes each, row by row. Null when nothing is visible. */
export function trimBox(rgba: ArrayLike<number>, width: number, height: number): Box | null {
  let left = width;
  let top = height;
  let right = -1;
  let bottom = -1;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (rgba[(y * width + x) * 4 + 3] > TRIM_ALPHA) {
        if (x < left) left = x;
        if (x > right) right = x;
        if (y < top) top = y;
        if (y > bottom) bottom = y;
      }
    }
  }
  if (right < 0) return null;
  return [Math.max(0, left - TRIM_EDGE), Math.max(0, top - TRIM_EDGE), Math.min(width, right + 1 + TRIM_EDGE), Math.min(height, bottom + 1 + TRIM_EDGE)];
}

/** A picked design file, cropped to its trim box, as a URL and its size (browser only). */
export async function trimmedDesign(file: Blob): Promise<{ url: string; width: number; height: number }> {
  const bitmap = await createImageBitmap(file);
  const canvas = document.createElement("canvas");
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return { url: URL.createObjectURL(file), width: bitmap.width, height: bitmap.height };
  ctx.drawImage(bitmap, 0, 0);
  const box = trimBox(ctx.getImageData(0, 0, bitmap.width, bitmap.height).data, bitmap.width, bitmap.height);
  if (!box) return { url: URL.createObjectURL(file), width: bitmap.width, height: bitmap.height };
  const [l, t, r, b] = box;
  const out = document.createElement("canvas");
  out.width = r - l;
  out.height = b - t;
  out.getContext("2d")?.drawImage(canvas, l, t, r - l, b - t, 0, 0, r - l, b - t);
  const blob: Blob = await new Promise((resolve) => out.toBlob((x) => resolve(x ?? file), "image/png"));
  return { url: URL.createObjectURL(blob), width: out.width, height: out.height };
}
