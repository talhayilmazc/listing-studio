// Mockup runs the seller started, watched from any page (components/MockupRunWatcher.tsx; seller
// feedback D4): when one finishes, an in-app notification; "Show" opens its results, scrolled to
// the design and highlighted. Kept in this browser only (a convenience; the run itself is on the server).

export const STORE_KEY = "listyro.mockupRuns";
/** A run older than this is forgotten without a notification. */
export const FORGET_AFTER_MS = 24 * 60 * 60 * 1000;

export type WatchedRun = { id: string; batchId: string; startedAt: number };

export function readRuns(storage: Pick<Storage, "getItem"> | null): WatchedRun[] {
  try {
    const raw = JSON.parse(storage?.getItem(STORE_KEY) ?? "[]");
    const now = Date.now();
    return Array.isArray(raw)
      ? raw.filter((r) => r && typeof r.id === "string" && typeof r.batchId === "string" && now - Number(r.startedAt) < FORGET_AFTER_MS)
      : [];
  } catch {
    return [];
  }
}

export function writeRuns(storage: Pick<Storage, "setItem"> | null, runs: WatchedRun[]): void {
  try {
    storage?.setItem(STORE_KEY, JSON.stringify(runs.slice(-20)));
  } catch {
    // blocked storage: nothing is watched across pages, the page still shows its own progress
  }
}

/** Start watching a run (a run already watched keeps its place). */
export function watch(runs: WatchedRun[], run: { id: string; batch_id: string }, now = Date.now()): WatchedRun[] {
  return runs.some((r) => r.id === run.id) ? runs : [...runs, { id: run.id, batchId: run.batch_id, startedAt: now }];
}

export const finished = (status: string) => status === "succeeded" || status === "failed";

/** The notification's words. */
export function readyLine(run: { status: string; done: number; failed: number; designs: unknown[] }): string {
  if (run.status === "failed" && !run.done) return "The mockups could not be made.";
  const made = `${run.done} ${run.done === 1 ? "mockup" : "mockups"} for ${run.designs.length} ${run.designs.length === 1 ? "design" : "designs"}`;
  return run.failed ? `Mockups ready: ${made}; ${run.failed} could not be made.` : `Mockups ready: ${made}.`;
}

/** Where "Show" goes: the designs page with that run, scrolled to the design. */
export const resultsHref = (runId: string, designId?: string | null) =>
  `/templates/designs?run=${encodeURIComponent(runId)}${designId ? `&design=${encodeURIComponent(designId)}` : ""}`;

/** The results anchor of one design (scrolled to and highlighted). */
export const designAnchor = (designId: string) => `design-${designId}`;
