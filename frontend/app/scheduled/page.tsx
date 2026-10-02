"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { addDays, dayKey, formatTime, formatWhen, scheduleLabel, toWallClock, wallToInstant } from "@/lib/schedule";
import { useSession } from "@/components/SessionProvider";
import { ZoneNote } from "@/components/ZoneNote";
import type { Schedule } from "@/lib/types";
import { ShopBadge } from "@/components/ShopPicker";
import { useShops } from "@/components/ShopProvider";

import { Txt } from "@/components/Txt";
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
  const { timeZone } = useSession();
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
      const k = dayKey(new Date(r.scheduled_for), timeZone);
      m.set(k, [...(m.get(k) ?? []), r]);
    }
    return m;
  }, [rows, timeZone]);

  const upcoming = (rows ?? []).filter((r) => r.status === "scheduled" || r.status === "waiting").length;
  const multiShop = (shops?.length ?? 0) > 1;

  return (
    <div className="space-y-6">
      <p className="max-w-2xl text-sm text-slate-500">
        Drafts you approved and chose a time for. Each goes live at its time in your account&apos;s
        time zone unless you cancel it; the budget scheduled go-lives need is kept for them. Schedule
        drafts from a batch&apos;s review page.
      </p>
      <ZoneNote />

      {error && <div key="div-64-6" className="card p-3 text-sm text-rose-700">{error}</div>}
      {rows === null && !error && <p key="p-65-6" className="text-sm text-slate-400">Loading…</p>}

      {rows !== null && (
        <>
          <DayStrip byDay={byDay} timeZone={timeZone} />
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
              <span translate="no" className="tabular-nums font-medium text-slate-700">{upcoming}</span> still to go
              live · finished ones stay here for a week
            </p>
          )}
          {[...byDay.entries()].map(([day, list]) => (
            <section key={day} className="space-y-2">
              <h2 className="border-b border-slate-200 pb-1 font-display text-lg text-slate-900">
                <span>{new Date(day + "T12:00:00Z").toLocaleDateString(undefined, {
                  weekday: "long",
                  day: "numeric",
                  month: "long",
                  timeZone: "UTC",
                })}</span>
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
function DayStrip({ byDay, timeZone }: { byDay: Map<string, Schedule[]>; timeZone: string }) {
  // Today and the next days on the account zone's calendar; each day is drawn
  // from its calendar date at noon UTC, so no zone can shift it a day.
  const today = toWallClock(new Date(), timeZone).slice(0, 10) + "T12:00";
  const days = Array.from({ length: DAYS_SHOWN }, (_, i) => new Date(addDays(today, i) + ":00Z"));
  return (
    <div className="grid grid-cols-7 gap-1.5" aria-label="Next two weeks">
      {days.map((d) => {
        const n = (byDay.get(d.toISOString().slice(0, 10)) ?? []).length;
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
              {d.toLocaleDateString(undefined, { weekday: "short", timeZone: "UTC" })}
            </div>
            <div className="text-sm text-slate-700">{d.getUTCDate()}</div>
            <div translate="no" className={"text-xs tabular-nums " + (n > 0 ? "font-medium text-brand-700" : "text-slate-300")}>
              {n || "–"}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Row({ row, showShop, onChanged }: { row: Schedule; showShop: boolean; onChanged: () => void }) {
  const { timeZone } = useSession();
  const at = new Date(row.scheduled_for);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(toWallClock(at, timeZone));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const changeable = row.status === "scheduled" || row.status === "not_published";

  async function save() {
    if (!value) return setError("Choose a date and time.");
    if (!wallToInstant(value, timeZone)) return setError("That time doesn't exist in your time zone (the clocks skip it).");
    setBusy(true);
    setError(null);
    try {
      const res = await api.schedule([
        { content_id: row.content_id, connection_id: row.connection_id, local_time: value },
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
      {/* On a phone: the time and the status on one line, the listing on the next. */}
      <span translate="no" className="w-28 shrink-0 whitespace-nowrap text-sm tabular-nums text-slate-800 max-sm:order-1 max-sm:w-auto max-sm:font-medium">
        {formatTime(at, timeZone)}
      </span>
      <span aria-hidden className="hidden max-sm:order-3 max-sm:block max-sm:h-0 max-sm:basis-full" />
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={api.assetImage(row.asset_id, 112)} alt="" className="h-10 w-10 shrink-0 rounded object-cover max-sm:order-4" />
      <span className="min-w-0 flex-1 max-sm:order-5">
        <span className="block truncate text-sm text-slate-800 max-sm:line-clamp-2 max-sm:whitespace-normal" title={row.title ?? undefined}>
          {row.title ?? `Listing ${row.etsy_listing_id}`}
        </span>
        <span className="block text-xs text-slate-400">
          <ShopBadge name={row.shop_name ?? "Shop"} className="mr-1.5" />
          <a href={row.listing_link} target="_blank" rel="noreferrer" className="tap hover:text-brand-700 hover:underline">
            {row.status === "published" ? "View on Etsy ↗" : "Edit draft ↗"}
          </a>
          <span>{" · "}</span>
          <Link href={`/batches/${row.batch_id}/review`} className="tap hover:text-brand-700 hover:underline">
            <span>review</span>
            <Txt>{row.batch_name ? ` in ${row.batch_name}` : ""}</Txt>
          </Link>
        </span>
        {row.status === "waiting" && row.resumes_at && (
          <span key="resumes" translate="no" className="block text-xs text-amber-800">
            {`The day's Etsy budget ran out; it goes live after the reset, at ${formatWhen(row.resumes_at, timeZone)}.`}
          </span>
        )}
        {row.note && row.status !== "waiting" && <span key="span-204-8" className="block text-xs text-amber-800">{row.note}</span>}
        {error && <span key="span-205-8" className="block text-xs text-rose-700">{error}</span>}
      </span>
      <span translate="no" className={"shrink-0 rounded-md border px-1.5 py-0.5 text-xs font-medium max-sm:order-2 max-sm:ml-auto " + (TONE[row.status] ?? TONE.scheduled)}>
        {scheduleLabel(row.status)}
      </span>
      {changeable && !editing && (
        <span key="span-210-6" className="flex shrink-0 gap-2 text-xs max-sm:order-6 max-sm:basis-full">
          <button type="button" className="text-slate-500 underline hover:text-slate-900 max-sm:min-h-[2.75rem] max-sm:rounded-md max-sm:border max-sm:border-slate-300 max-sm:px-3 max-sm:text-slate-700 max-sm:no-underline" onClick={() => setEditing(true)}>
            change
          </button>
          <button type="button" className="text-slate-500 underline hover:text-slate-900 max-sm:min-h-[2.75rem] max-sm:rounded-md max-sm:border max-sm:border-slate-300 max-sm:px-3 max-sm:text-slate-700 max-sm:no-underline" onClick={cancel} disabled={busy}>
            {busy ? "cancelling…" : "cancel"}
          </button>
        </span>
      )}
      {editing && (
        <span key="span-220-6" className="flex w-full flex-wrap items-center gap-2 pl-[7.75rem] text-xs max-sm:order-6 max-sm:pl-0">
          <input
            type="datetime-local"
            className="field w-auto py-1 text-xs"
            value={value}
            min={toWallClock(new Date(), timeZone)}
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
