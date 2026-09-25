"use client";

import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { fromLocalInput, nextHour, spreadTimes, toLocalInput } from "@/lib/schedule";
import type { Content, ScheduleItem, ScheduleResult } from "@/lib/types";

/** The approved listings' drafts that are not live and not yet scheduled, in page order. */
export function schedulableDrafts(items: Content[]): { content: Content; connectionId: string; shop: string }[] {
  const out: { content: Content; connectionId: string; shop: string }[] = [];
  for (const c of items) {
    if (!c.approved) continue;
    for (const p of c.publications) {
      if (p.state === "active" || p.scheduled_for) continue;
      out.push({ content: c, connectionId: p.connection_id, shop: p.shop_name ?? "Shop" });
    }
  }
  return out;
}

const fmt = (d: Date) => d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });

/**
 * Schedule many approved drafts at once (v6 §G): one start time, optionally so
 * many a day, optionally spaced apart. The times are worked out here in the
 * seller's time zone and shown before anything is saved.
 */
export function BulkSchedule({ items, onDone }: { items: Content[]; onDone: () => void }) {
  const drafts = useMemo(() => schedulableDrafts(items), [items]);
  const [start, setStart] = useState(() => toLocalInput(nextHour()));
  const [perDay, setPerDay] = useState("");
  const [spacing, setSpacing] = useState("0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ScheduleResult | null>(null);

  const startAt = fromLocalInput(start);
  const daily = perDay ? Math.max(1, parseInt(perDay, 10) || 0) : null;
  const gap = Math.max(0, parseInt(spacing, 10) || 0);
  const times = startAt ? spreadTimes(startAt, drafts.length, daily, gap) : [];
  const past = startAt !== null && startAt.getTime() < Date.now() - 60_000;

  async function confirm() {
    if (!startAt || past) return;
    setBusy(true);
    setError(null);
    try {
      const payload: ScheduleItem[] = drafts.map((d, i) => ({
        content_id: d.content.id,
        connection_id: d.connectionId,
        run_at: times[i].toISOString(),
      }));
      // Never moves a draft that already has a time (set on its card meanwhile).
      setResult(await api.schedule(payload, false));
      onDone();
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    return (
      <div role="status" className="card p-4 text-sm text-slate-700">
        <p>
          Scheduled {result.scheduled.length} draft{result.scheduled.length === 1 ? "" : "s"}.{" "}
          <a href="/scheduled" className="font-medium text-brand-700 underline">
            See the schedule
          </a>
        </p>
        {result.skipped.length > 0 && (
          <ul className="mt-1.5 space-y-0.5 text-xs text-amber-800">
            {result.skipped.map((s, i) => {
              const title = items.find((c) => c.id === s.content_id)?.original_filename ?? s.content_id;
              return (
                <li key={i}>
                  <span className="font-mono">{title}</span>: {s.reason}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    );
  }

  if (drafts.length === 0) {
    return (
      <div className="card p-4 text-sm text-slate-600">
        Nothing to schedule: approve listings and create their drafts first. Drafts already live or
        scheduled are left as they are.
      </div>
    );
  }

  return (
    <div className="card space-y-3 p-4 text-sm">
      <p className="text-slate-700">
        Schedule {drafts.length} approved draft{drafts.length === 1 ? "" : "s"} to go live. Times
        are in your time zone; each can still be changed or cancelled on its own.
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-slate-500">
          First one at
          <input
            type="datetime-local"
            className="field mt-1 block w-auto py-1 text-sm"
            value={start}
            min={toLocalInput(new Date())}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label className="text-xs text-slate-500">
          Per day (optional)
          <input
            type="number"
            min={1}
            className="field mt-1 block w-24 py-1 text-sm"
            value={perDay}
            placeholder="all"
            onChange={(e) => setPerDay(e.target.value)}
          />
        </label>
        <label className="text-xs text-slate-500">
          Minutes apart
          <input
            type="number"
            min={0}
            step={5}
            className="field mt-1 block w-24 py-1 text-sm"
            value={spacing}
            onChange={(e) => setSpacing(e.target.value)}
          />
        </label>
        <button type="button" className="btn-primary" onClick={confirm} disabled={busy || !startAt || past}>
          {busy ? "Scheduling…" : `Schedule ${drafts.length}`}
        </button>
      </div>
      {past && <p className="text-xs text-rose-700">That time has already passed.</p>}
      {times.length > 0 && !past && (
        <ol className="max-h-48 space-y-0.5 overflow-y-auto text-xs text-slate-600">
          {drafts.map((d, i) => (
            <li key={`${d.content.id}-${d.connectionId}`} className="flex gap-3">
              <span className="w-44 shrink-0 tabular-nums text-slate-800">{fmt(times[i])}</span>
              <span className="truncate">
                {d.content.title ?? d.content.original_filename}
                <span className="text-slate-400"> · {d.shop}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
      {error && <p className="text-xs text-rose-700">{error}</p>}
    </div>
  );
}
