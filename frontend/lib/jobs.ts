import { api } from "./api";
import type { JobStatus } from "./types";

const POLL_MS = 1500;
// Paced at 3 Etsy requests a second, a queue of publishes can take minutes. The old
// 90-second limit gave up on jobs that were still running and left the screen
// stale until a reload (docs/duzeltmeler-v5.md §C).
const GIVE_UP_MS = 15 * 60 * 1000;

/** Waits of seconds to minutes after which the job runs again by itself. */
const SHORT_WAITS = new Set(["etsy_rate_limit", "etsy_unavailable", "interrupted"]);

/** The job is waiting to run again shortly: still worth watching, and worth saying. */
export function isWaitingBriefly(job: JobStatus): boolean {
  return job.pause !== null && SHORT_WAITS.has(job.pause.reason);
}

/** Finished, failed, cancelled, or waiting for the daily reset: nothing more to watch. */
export function isSettled(job: JobStatus): boolean {
  return (
    job.status === "succeeded" ||
    job.status === "failed" ||
    job.status === "cancelled" ||
    (job.pause !== null && !isWaitingBriefly(job))
  );
}

/**
 * Poll a queued Etsy job until it settles. Resolves with the final status, or
 * null if it is still running when we stop watching. ``onWaiting`` is told
 * while the job waits to run again by itself (Etsy asked for a pause, or did
 * not answer), and with null when it is running again.
 */
export async function waitForJob(
  jobId: string,
  onWaiting?: (message: string | null) => void,
): Promise<JobStatus | null> {
  const deadline = Date.now() + GIVE_UP_MS;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, POLL_MS));
    try {
      const job = await api.jobStatus(jobId);
      if (isSettled(job)) return job;
      onWaiting?.(isWaitingBriefly(job) ? job.pause!.message : null);
    } catch {
      // A transient error while polling is not the job failing; keep watching.
    }
  }
  return null;
}
