"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { resumeTime } from "@/lib/format";
import type { Pause } from "@/lib/types";

/**
 * Says so when this seller's Etsy work is paused for the day (production-spec C):
 * the app has used 90% of Etsy's shared daily limit, or this shop its own
 * allowance. Nothing is lost. Queued drafts, updates and refreshes wait and then
 * run after the reset. Without this, paused work would look like work that
 * silently stopped.
 */

// Re-checked on every navigation, and periodically on a page left open.
const REFRESH_MS = 60_000;

export function PauseNotice() {
  const pathname = usePathname();
  const [pause, setPause] = useState<Pause | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .quota()
        .then((q) => !cancelled && setPause(q.pause))
        .catch(() => {});
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [pathname]);

  if (!pause) return null;
  return (
    <div role="status" className="border-b border-amber-200 bg-amber-50 px-6 py-3 lg:px-8">
      <p className="mx-auto flex w-full max-w-[1800px] items-start gap-2.5 text-sm text-amber-800">
        <svg
          aria-hidden
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          className="mt-[3px] shrink-0"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M10 9v6M14 9v6" />
        </svg>
        <span>
          <span className="font-medium">Etsy work is paused.</span>{" "}
          {pause.message} That is around {resumeTime(pause.resumes_at)} your time.
        </span>
      </p>
    </div>
  );
}
