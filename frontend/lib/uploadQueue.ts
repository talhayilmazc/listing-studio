/**
 * How the pages send files (page weight and uploads, 2026-10-23): at most three at a
 * time, each with its own progress; a file that fails on the way (no answer, a timeout,
 * a busy or restarting server) is tried again by itself, so one dropped connection does
 * not cost the rest. A file the server refused (not an image, too large, storage full) is
 * not tried again: it would be refused again. Files are sent exactly as chosen: never
 * resized or recompressed in the browser (a design's pixels are the seller's).
 */

/** Files sent at the same time. */
export const UPLOAD_PARALLEL = 3;
/** Tries per file, the first included. */
export const UPLOAD_TRIES = 3;

/** HTTP statuses worth another try: no answer (0), timeout, rate limited, the server or its proxy. */
const AGAIN = new Set([0, 408, 425, 429, 500, 502, 503, 504]);

export function retryable(error: unknown): boolean {
  const status = (error as { status?: unknown } | null)?.status;
  return typeof status === "number" && AGAIN.has(status);
}

/** Wait before try ``n`` (2, 3, …): 1 s, then 3 s, then 9 s. */
export function backoff(n: number): number {
  return 1000 * 3 ** Math.max(0, n - 2);
}

/**
 * ``send`` again after a failure that may pass (``retryable``), up to ``tries`` in all.
 * ``onRetry(n)`` is told before try ``n``. The last failure is what it throws.
 */
export async function withRetry<T>(
  send: () => Promise<T>,
  { tries = UPLOAD_TRIES, onRetry, wait = backoff }: { tries?: number; onRetry?: (n: number) => void; wait?: (n: number) => number } = {},
): Promise<T> {
  for (let n = 1; ; n += 1) {
    try {
      return await send();
    } catch (e) {
      if (n >= tries || !retryable(e)) throw e;
      onRetry?.(n + 1);
      await new Promise((r) => setTimeout(r, wait(n + 1)));
    }
  }
}

/** Run ``work`` for every item, ``limit`` at a time, in order of starting; it resolves when all have finished. */
export async function runLimited<T>(items: T[], limit: number, work: (item: T, index: number) => Promise<void>): Promise<void> {
  let next = 0;
  const lane = async () => {
    while (next < items.length) {
      const i = next;
      next += 1;
      await work(items[i], i);
    }
  };
  await Promise.all(Array.from({ length: Math.min(Math.max(1, limit), items.length) }, lane));
}
