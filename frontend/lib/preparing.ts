/**
 * Photos the worker is still preparing (fix/perf-and-memory): an upload is stored as it came and the worker
 * makes the processed copy, so a batch's photos can be "uploaded" for a few seconds after the upload. The
 * page says how many and looks again until none is left; writing waits for them (the server refuses it too).
 */
export const LOOK_AGAIN_MS = 2000;

/** How many of these photos are still being prepared. */
export function stillPreparing(assets: { status: string }[]): number {
  return assets.filter((a) => a.status === "uploaded").length;
}

/** "Preparing photos: 3 of 40 ready." or null when every photo is ready. */
export function preparingLine(assets: { status: string }[]): string | null {
  const waiting = stillPreparing(assets);
  if (!waiting) return null;
  return `Preparing photos: ${assets.length - waiting} of ${assets.length} ready. Writing can start when they are all ready.`;
}
