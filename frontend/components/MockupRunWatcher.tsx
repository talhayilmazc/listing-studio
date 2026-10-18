"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { MockupRun } from "@/lib/types";
import { finished, readRuns, readyLine, resultsHref, writeRuns, type WatchedRun } from "@/lib/mockupRuns";

/**
 * Watches the mockup runs the seller started (lib/mockupRuns.ts) from every signed-in page: when
 * one finishes, an in-app notification says how many mockups were made, and "Show" opens the
 * results scrolled to the first design and highlighted, even after moving to another page.
 * Started runs are kept in this browser only; nothing here calls Etsy or an AI model.
 */
export const RUN_STARTED_EVENT = "listyro:mockup-run";

export function MockupRunWatcher() {
  const [runs, setRuns] = useState<WatchedRun[]>([]);
  const [ready, setReady] = useState<MockupRun[]>([]);

  useEffect(() => {
    const load = () => {
      try {
        setRuns(readRuns(window.localStorage));
      } catch {
        setRuns([]);
      }
    };
    load();
    window.addEventListener(RUN_STARTED_EVENT, load);
    window.addEventListener("storage", load);
    return () => {
      window.removeEventListener(RUN_STARTED_EVENT, load);
      window.removeEventListener("storage", load);
    };
  }, []);

  useEffect(() => {
    if (!runs.length) return;
    const t = setTimeout(async () => {
      const left: WatchedRun[] = [];
      const done: MockupRun[] = [];
      for (const r of runs) {
        try {
          const run = await api.mockupRun(r.id);
          if (finished(run.status)) done.push(run);
          else left.push(r);
        } catch (e: any) {
          // A run that is gone (its batch deleted) is forgotten; anything else (signed out, offline) is tried again.
          if (!/not found/i.test(String(e?.message ?? ""))) left.push(r);
        }
      }
      if (done.length) setReady((now) => [...now.filter((x) => !done.some((d) => d.id === x.id)), ...done]);
      try {
        writeRuns(window.localStorage, left);
      } catch {
        // storage blocked
      }
      setRuns(left);
    }, 4000);
    return () => clearTimeout(t);
  }, [runs]);

  if (!ready.length) return null;
  return (
    <div className="fixed bottom-4 right-4 z-40 w-[min(22rem,calc(100vw-2rem))] space-y-2" aria-live="polite" data-testid="mockup-notifications">
      {ready.map((run) => (
        <div key={run.id} role="status" className="card flex flex-wrap items-center gap-2 border-emerald-200 bg-emerald-50 p-3 shadow-lg"
          data-testid="mockup-notification">
          <p className="min-w-0 flex-1 text-sm font-medium text-emerald-900" translate="no">{readyLine(run)}</p>
          <Link href={resultsHref(run.id, run.designs[0]?.design_id)} className="btn-primary min-h-11"
            onClick={() => setReady((now) => now.filter((x) => x.id !== run.id))} data-testid="mockup-notification-show">
            Show
          </Link>
          <button type="button" className="btn-secondary min-h-11" onClick={() => setReady((now) => now.filter((x) => x.id !== run.id))}>
            Dismiss
          </button>
        </div>
      ))}
    </div>
  );
}

/** Called by the page that starts a run: watch it from every page. */
export function announceRun(run: { id: string; batch_id: string }): void {
  try {
    const now = readRuns(window.localStorage);
    if (!now.some((r) => r.id === run.id)) writeRuns(window.localStorage, [...now, { id: run.id, batchId: run.batch_id, startedAt: Date.now() }]);
    window.dispatchEvent(new Event(RUN_STARTED_EVENT));
  } catch {
    // storage blocked: the page still shows its own progress
  }
}
