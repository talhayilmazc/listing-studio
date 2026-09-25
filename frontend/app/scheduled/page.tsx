"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { dayKey, fromLocalInput, scheduleLabel, toLocalInput } from "@/lib/schedule";
import type { Schedule } from "@/lib/types";
import { useShops } from "@/components/ShopProvider";

const TONE: Record<string, string> = {
  scheduled: "border-brand-100 bg-brand-50 text-brand-700",
  publishing: "border-brand-100 bg-brand-50 text-brand-700",
  waiting: "border-amber-200 bg-amber-50 text-amber-800",
  published: "border-emerald-200 bg-emerald-50 text-emerald-700",
  failed: "border-rose-200 bg-rose-50 text-rose-700",
  not_published: "border-rose-200 bg-rose-50 text-rose-700",
};

const DAYS_SHOWN = 14;

/**
 * Every scheduled go-live (docs/duzeltmeler-v6.md §G): when, which shop, which
 * listing, and how it went. Times are in the seller's own time zone.
 */
export default function ScheduledPage() {
  const { shops } = useShops();
  const [rows, setRows] = useState<Schedule[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .listSchedules()
      .then(setRows)
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  useEffect(() => {
    load();
    // Due schedules turn into "Publishing" and then "Published" by themselves.
    const t = setInterval(load, 30_000);
    return () => clearInterval(t);
  }, [load]);

  const byDay = useMemo(() => {
    const m = new Map<string, Schedule[]>();
    for (const r of rows ?? []) {
      const k = dayKey(new Date(r.scheduled_for));
      m.set(k, [...(m.get(k) ?? []), r]);
    }
    return m;
  }, [rows]);

  const upcoming = (rows ?? []).filter((r) => r.status === "scheduled" || r.status === "waiting").length;
  const multiShop = (shops?.length ?? 0) > 1;

  return (
    <div className="space-y-6">
      <p className="max-w-2xl text-sm text-slate-500">
        Drafts you approved and chose a time for. Each goes live at its time (your time zone) unless
        you cancel it; if the day&apos;s Etsy budget is used up, it waits for the next day and says so
        here. Schedule drafts from a batch&apos;s review page.
      </p>

      {error && <div className="card p-3 text-sm text-rose-700">{error}</div>}
      {rows === null && !error && <p className="text-sm text-slate-400">Loading…</p>}

      {rows !== null && (
        <>
          <DayStrip byDay={byDay} />
          {rows.length === 0 ? (
            <div className="card p-6 text-sm text-slate-600">
              Nothing is scheduled. On a batch&apos;s review page, use <b>Schedule</b> on a draft, or{" "}
              <b>Schedule…</b> for all approved drafts at once.{" "}
              <Link href="/" className="text-brand-700 underline">
                Batches
              </Link>
            </div>
          ) : (
            <p className="text-xs text-slate-500">
              <span className="tabular-nums font-medium text-slate-700">{upcoming}</span> still to go
              live · finished ones stay here for a week
            </p>
          )}
          {[...byDay.entries()].map(([day, list]) => (
            <section key={day} className="space-y-2">
              <h2 className="border-b border-slate-200 pb-1 font-display text-lg text-slate-900">
                {new Date(day + "T00:00").toLocaleDateString(undefined, {
                  weekday: "long",
                  day: "numeric",
                  month: "long",
                })}
                <span className="ml-2 text-xs font-normal text-slate-400">{list.length}</span>
              </h2>
              <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
                {list.map((r) => (
                  <Row key={`${r.content_id}-${r.connection_id}`} row={r} showShop={multiShop} onChanged={load} />
                ))}
              </ul>
            </section>
          ))}
        </>
      )}
    </div>
  );
}

/** The next two weeks, with how many go live each day. */
function DayStrip({ byDay }: { byDay: Map<string, Schedule[]> }) {
  const days = Array.from({ length: DAYS_SHOWN }, (_, i) => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() + i);
    return d;
  });
  return (
    <div className="grid grid-cols-7 gap-1.5" aria-label="Next two weeks">
      {days.map((d) => {
        const n = (byDay.get(dayKey(d)) ?? []).length;
        return (
          <div
            key={d.toISOString()}
            className={
              "rounded-md border px-2 py-1.5 text-center " +
              (n > 0 ? "border-brand-100 bg-brand-50" : "border-slate-200 bg-white")
            }
            title={`${n} scheduled`}
          >
            <div className="text-[10px] uppercase tracking-wide text-slate-400">
              {d.toLocaleDateString(undefined, { weekday: "short" })}
            </div>
            <div className="text-sm text-slate-700">{d.getDate()}</div>
            <div className={"text-xs tabular-nums " + (n > 0 ? "font-medium text-brand-700" : "text-slate-300")}>
              {n || "–"}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Row({ row, showShop, onChanged }: { row: Schedule; showShop: boolean; onChanged: () => void }) {
  const at = new Date(row.scheduled_for);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(toLocalInput(at));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const changeable = row.status === "scheduled" || row.status === "not_published";

  async function save() {
    const when = fromLocalInput(value);
    if (!when) return setError("Choose a date and time.");
    setBusy(true);
    setError(null);
    try {
      const res = await api.schedule([
        { content_id: row.content_id, connection_id: row.connection_id, run_at: when.toISOString() },
      ]);
      if (res.skipped.length) setError(res.skipped[0].reason);
      else {
        setEditing(false);
        onChanged();
      }
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    setBusy(true);
    setError(null);
    try {
      await api.cancelSchedule(row.content_id, row.connection_id);
      onChanged();
    } catch (e: any) {
      setError(e.message ?? String(e));
      setBusy(false);
    }
  }

  return (
    <li className="flex flex-wrap items-center gap-3 px-3 py-2.5">
      <span className="w-20 shrink-0 whitespace-nowrap text-sm tabular-nums text-slate-800">
        {at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
      </span>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={api.assetImage(row.asset_id, 112)} alt="" className="h-10 w-10 shrink-0 rounded object-cover" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm text-slate-800" title={row.title ?? undefined}>
          {row.title ?? `Listing ${row.etsy_listing_id}`}
        </span>
        <span className="block text-xs text-slate-400">
          {showShop && <>{row.shop_name ?? "Shop"} · </>}
          <a href={row.listing_link} target="_blank" rel="noreferrer" className="hover:text-brand-700 hover:underline">
            {row.status === "published" ? "View on Etsy ↗" : "Edit draft ↗"}
          </a>
          {" · "}
          <Link href={`/batches/${row.batch_id}/review`} className="hover:text-brand-700 hover:underline">
            review
          </Link>
        </span>
        {row.note && <span className="block text-xs text-amber-800">{row.note}</span>}
        {error && <span className="block text-xs text-rose-700">{error}</span>}
      </span>
      <span className={"shrink-0 rounded-md border px-1.5 py-0.5 text-xs font-medium " + (TONE[row.status] ?? TONE.scheduled)}>
        {scheduleLabel(row.status)}
      </span>
      {changeable && !editing && (
        <span className="flex shrink-0 gap-2 text-xs">
          <button type="button" className="text-slate-500 underline hover:text-slate-900" onClick={() => setEditing(true)}>
            change
          </button>
          <button type="button" className="text-slate-500 underline hover:text-slate-900" onClick={cancel} disabled={busy}>
            {busy ? "cancelling…" : "cancel"}
          </button>
        </span>
      )}
      {editing && (
        <span className="flex w-full flex-wrap items-center gap-2 pl-[5.75rem] text-xs">
          <input
            type="datetime-local"
            className="field w-auto py-1 text-xs"
            value={value}
            min={toLocalInput(new Date())}
            onChange={(e) => setValue(e.target.value)}
            aria-label="New time"
          />
          <button type="button" className="btn-primary px-2.5 py-1 text-xs" onClick={save} disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </button>
          <button type="button" className="text-slate-500 hover:text-slate-800" onClick={() => setEditing(false)}>
            Cancel
          </button>
        </span>
      )}
    </li>
  );
}
