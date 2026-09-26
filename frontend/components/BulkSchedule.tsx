"use client";

import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { formatWhen, nextHour, spreadTimes, toWallClock, wallToInstant } from "@/lib/schedule";
import { useSession } from "./SessionProvider";
import { ZoneNote } from "./ZoneNote";
import type { Content, ScheduleItem, ScheduleResult } from "@/lib/types";

import { Txt } from "@/components/Txt";
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

/**
 * Schedule many approved drafts at once (v6 §G): one start time, optionally so
 * many a day, optionally spaced apart. The times are worked out here in the
 * seller's time zone and shown before anything is saved.
 */
export function BulkSchedule({ items, onDone }: { items: Content[]; onDone: () => void }) {
  const drafts = useMemo(() => schedulableDrafts(items), [items]);
  const { timeZone } = useSession();
  const [start, setStart] = useState(() => nextHour(timeZone));
  const [perDay, setPerDay] = useState("");
  const [spacing, setSpacing] = useState("0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ScheduleResult | null>(null);

  // Worked out on the account zone's calendar: "the next day at 17:00" stays 17:00
  // across a daylight-saving change. The server converts each time once.
  const startAt = start ? wallToInstant(start, timeZone) : null;
  const daily = perDay ? Math.max(1, parseInt(perDay, 10) || 0) : null;
  const gap = Math.max(0, parseInt(spacing, 10) || 0);
  const times = startAt ? spreadTimes(start, drafts.length, daily, gap) : [];
  const instants = times.map((w) => wallToInstant(w, timeZone));
  const past = startAt !== null && startAt.getTime() < Date.now() - 60_000;

  async function confirm() {
    if (!startAt || past) return;
    setBusy(true);
    setError(null);
    try {
      const payload: ScheduleItem[] = drafts.map((d, i) => ({
        content_id: d.content.id,
        connection_id: d.connectionId,
        local_time: times[i],
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
      <div role="status" translate="no" className="card p-4 text-sm text-slate-700">
        <p>
          <span><span>Scheduled </span><span>{result.scheduled.length}</span><span> draft</span><Txt>{result.scheduled.length === 1 ? "" : "s"}</Txt><span>.</span>{" "}</span>
          <a href="/scheduled" className="font-medium text-brand-700 underline">
            See the schedule
          </a>
        </p>
        {result.skipped.length > 0 && (
          <ul key="ul-72-8" className="mt-1.5 space-y-0.5 text-xs text-amber-800">
            {result.skipped.map((s, i) => {
              const title = items.find((c) => c.id === s.content_id)?.original_filename ?? s.content_id;
              return (
                <li key={i}>
                  <span className="font-mono">{title}</span><span>: <span>{s.reason}</span></span>
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
        <span><span>Schedule </span><span>{drafts.length}</span><span> approved draft</span><Txt>{drafts.length === 1 ? "" : "s"}</Txt><span> to go live. Times
        are in your account&apos;s time zone; each can still be changed or cancelled on its own.</span></span>
      </p>
      <ZoneNote />
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-slate-500">
          First one at
          <input
            type="datetime-local"
            className="field mt-1 block w-auto py-1 text-sm"
            value={start}
            min={toWallClock(new Date(), timeZone)}
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
      {past && <p key="p-140-6" className="text-xs text-rose-700">That time has already passed.</p>}
      {times.length > 0 && !past && (
        <ol key="ol-141-6" className="max-h-48 space-y-0.5 overflow-y-auto text-xs text-slate-600">
          {drafts.map((d, i) => (
            <li key={`${d.content.id}-${d.connectionId}`} className="flex gap-3">
              <span translate="no" className="w-52 shrink-0 tabular-nums text-slate-800">
                {instants[i] ? formatWhen(instants[i]!, timeZone) : "skipped by the clock change"}
              </span>
              <span className="truncate">
                <Txt>{d.content.title ?? d.content.original_filename}</Txt>
                <span className="text-slate-400"><span> · <span>{d.shop}</span></span></span>
              </span>
            </li>
          ))}
        </ol>
      )}
      {error && <p key="p-154-6" className="text-xs text-rose-700">{error}</p>}
    </div>
  );
}
