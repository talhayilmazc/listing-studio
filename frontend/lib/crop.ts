// The cover thumbnail adjuster: pan and zoom an image inside a square frame.
//
// A crop is a square on the image, in the image's pixels. A view is how the
// image sits in a V x V frame on screen: its zoom (1 = the short side exactly
// fills the frame) and where its top-left corner is, in frame pixels. The image
// always covers the whole frame, so the square never shows anything outside it.

export interface Crop {
  x: number;
  y: number;
  size: number;
}

export interface View {
  zoom: number;
  ox: number;
  oy: number;
}

export const MAX_ZOOM = 5; // the server refuses more; the photo would be too soft

/** Screen pixels per image pixel. */
export function scaleOf(zoom: number, w: number, h: number, frame: number): number {
  return (frame / Math.min(w, h)) * zoom;
}

/** Keep the zoom in range and the image covering the frame. */
export function clampView(v: View, w: number, h: number, frame: number): View {
  const zoom = Math.min(MAX_ZOOM, Math.max(1, v.zoom));
  const s = scaleOf(zoom, w, h, frame);
  const clamp = (o: number, extent: number) => Math.min(0, Math.max(frame - extent * s, o));
  return { zoom, ox: clamp(v.ox, w), oy: clamp(v.oy, h) };
}

/** The square the frame shows, in image pixels. */
export function cropOf(v: View, w: number, h: number, frame: number): Crop {
  const s = scaleOf(v.zoom, w, h, frame);
  return { x: -v.ox / s, y: -v.oy / s, size: frame / s };
}

/** The centred square of the whole short side: where the frame starts. */
export function centredCrop(w: number, h: number): Crop {
  const size = Math.min(w, h);
  return { x: (w - size) / 2, y: (h - size) / 2, size };
}

/** How the image sits in the frame to show this crop. */
export function viewOf(crop: Crop | null, w: number, h: number, frame: number): View {
  const c = crop ?? centredCrop(w, h);
  const zoom = Math.min(w, h) / c.size;
  const s = scaleOf(zoom, w, h, frame);
  return clampView({ zoom, ox: -c.x * s, oy: -c.y * s }, w, h, frame);
}

/** Zoom keeping the image point under (px, py), in frame pixels, where it is. */
export function zoomAround(v: View, zoom: number, px: number, py: number, w: number, h: number, frame: number): View {
  const s = scaleOf(v.zoom, w, h, frame);
  const ix = (px - v.ox) / s;
  const iy = (py - v.oy) / s;
  const z = Math.min(MAX_ZOOM, Math.max(1, zoom));
  const s2 = scaleOf(z, w, h, frame);
  return clampView({ zoom: z, ox: px - ix * s2, oy: py - iy * s2 }, w, h, frame);
}

/** Drag by (dx, dy) frame pixels. */
export function pan(v: View, dx: number, dy: number, w: number, h: number, frame: number): View {
  return clampView({ ...v, ox: v.ox + dx, oy: v.oy + dy }, w, h, frame);
}

/** CSS for an image drawn at `box` px showing exactly this crop (e.g. a thumbnail). */
export function cropStyle(crop: Crop, w: number, h: number, box: number) {
  const t = box / crop.size;
  return { width: w * t, height: h * t, left: -crop.x * t, top: -crop.y * t };
}
