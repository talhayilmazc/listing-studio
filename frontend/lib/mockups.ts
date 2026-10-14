/**
 * Mockup templates: the words and limits the pages share (backend api/mockups.py,
 * pipeline/mockups.py). lib/mockups.test.ts fails if a figure or the rights
 * sentence drifts from the backend's.
 */

/** What the seller confirms when uploading a blank: the same words the server checks for. */
export const RIGHTS_STATEMENT = "This photo is my own, or I have the right to use it to show my products.";

/** The size setting (share of the print area) and fold strength, as the server allows them. */
export const SIZE_RANGE: [number, number] = [0.2, 1.0];
export const FOLD_RANGE: [number, number] = [0.0, 1.0];
/** The most templates one upload prints on (Etsy keeps 20 photos per listing). */
export const MAX_TEMPLATES_PER_RUN = 10;

export const TEMPLATES_INTRO =
  "Upload a blank garment photo once; your designs are placed on it as if printed on the fabric. Only your design is used: it is fitted, warped to the fabric and shaded by it, never redrawn. Mockups do not count toward Listings generated.";

export const ACCURACY_NOTE =
  "Etsy asks for photos that show the item accurately: use a blank of the garment you actually sell, in its real colour.";

export type Corner = [number, number];

/** A corner moved to (x, y) (shares of the photo), kept inside the photo. */
export function moveCorner(corners: Corner[], index: number, x: number, y: number): Corner[] {
  const clamp = (v: number) => Math.round(Math.min(1, Math.max(0, v)) * 10000) / 10000;
  return corners.map((c, i) => (i === index ? [clamp(x), clamp(y)] : c));
}

/** "73%": a setting as the seller reads it. */
export const percent = (v: number) => `${Math.round(v * 100)}%`;

/** Progress of a run, as one line. */
export function runLine(run: { status: string; done: number; total: number; failed: number }): string {
  if (run.status === "queued") return "Waiting to start…";
  if (run.status === "running") return `Making mockups: ${run.done} of ${run.total}`;
  const made = `${run.done} ${run.done === 1 ? "mockup" : "mockups"} made`;
  return run.failed ? `${made}, ${run.failed} could not be made` : made;
}
