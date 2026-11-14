/**
 * Writing in the worker (fix/onboarding-followups Part D; fix/perf-and-memory): "Write listings", Generate,
 * Regenerate and "Generate the empty ones" each queue one run and answer at once; the page follows the run
 * (`GET /batches/{id}/writing`) instead of waiting on the request.
 */
import type { Writing } from "./types";

export const POLL_MS = 2000;

/** Still going: the page looks again. */
export const going = (w: Pick<Writing, "state">): boolean => w.state === "queued" || w.state === "running";

/** The run's outcome in one line, with why each left-alone group was left alone. */
export function outcomeLine(w: Writing, written: (n: number, failed: number, skipped: number) => string): string | null {
  if (w.state === "idle") return null;
  if (w.state === "done" && !w.total && w.message) return w.message;
  const reasons = (w.skipped_groups ?? []).map((s) => s.reason);
  if (!w.written && !w.failed && reasons.length) return Array.from(new Set(reasons)).join("; ");
  return written(w.written, w.failed, w.skipped);
}

/** Follow a run until it stops; `onProgress` sees every state on the way. A failed look is tried again. */
export async function waitForWriting(
  read: () => Promise<Writing>,
  first: Writing,
  onProgress: (w: Writing) => void = () => {},
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
): Promise<Writing> {
  let w = first;
  onProgress(w);
  while (going(w)) {
    await sleep(POLL_MS);
    try {
      w = await read();
    } catch {
      continue; // a dropped connection: look again
    }
    onProgress(w);
  }
  return w;
}
