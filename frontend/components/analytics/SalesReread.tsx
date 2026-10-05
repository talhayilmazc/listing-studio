"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { SalesReread as Report, SalesRereadAdmin, SalesRereadShop } from "@/lib/types";
import { ShopBadge } from "@/components/ShopPicker";
import { useSession } from "@/components/SessionProvider";

import { Txt } from "@/components/Txt";

/**
 * The one-time second read of each shop's sales (it adds the order lines that
 * tie an order to its listings). Shops are read one at a time over as many
 * days as it takes; this shows where each one stands.
 *
 * Seller: their own shops, on Analytics. Admin: every shop, with the nightly
 * share of the app's budget (which a seller is never shown).
 */

const n = (v: number | null | undefined) => (v ?? 0).toLocaleString("en-US");
const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { timeZone: "UTC", month: "short", day: "numeric" });
/**
 * The day a shop is expected to finish, in the reader's own time zone. The
 * server plans in UTC days: a later day's share opens at 00:00 UTC, which is
 * the evening before in the Americas.
 */
function finishDay(iso: string, zone: string): string {
  const today = new Date().toISOString().slice(0, 10);
  const at = iso <= today ? new Date() : new Date(`${iso}T00:30:00Z`);
  return at.toLocaleDateString("en-US", { timeZone: zone, month: "short", day: "numeric" });
}

const WORDS: Record<string, string> = {
  queued: "Waiting for its turn",
  reading: "Reading now",
  done: "Done",
  failed: "Stopped",
};

function statusText(s: SalesRereadShop): string {
  // Begun, and held: for the shop before it, or for the next day's share.
  if (s.status === "waiting") return s.note?.startsWith("waiting for its turn") ? "Waiting for its turn" : "Carries on tomorrow";
  return WORDS[s.status] ?? s.status;
}

function Progress({ shop }: { shop: SalesRereadShop }) {
  const total = shop.window_count ?? 0;
  const pct = shop.status === "done" ? 100 : total ? Math.min(100, (shop.read_count / total) * 100) : 0;
  return (
    <div className="min-w-0">
      <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
        <div className="progress-fill" style={{ width: pct + "%" }} />
      </div>
      <p className="mt-1 text-[11px] tabular-nums text-slate-500">
        <span>
          {shop.status === "queued"
            ? `Not started · about ${n(shop.requests_left)} requests`
            : shop.status === "done"
              ? `${n(shop.read_count)} sales read`
              : `${n(shop.read_count)} of about ${n(total)} sales · ${n(shop.requests_used)} requests so far`}
        </span>
      </p>
    </div>
  );
}

function Rows({ shops, zone, withAccount }: { shops: (SalesRereadShop & { email?: string | null })[]; zone: string; withAccount?: boolean }) {
  return (
    <ul className="divide-y divide-slate-100">
      {shops.map((s) => (
        <li key={s.connection_id} className="grid grid-cols-1 gap-x-4 gap-y-1.5 py-2.5 sm:grid-cols-[minmax(0,14rem)_minmax(0,1fr)_11rem] sm:items-center">
          <div className="min-w-0">
            <ShopBadge name={s.shop_name} />
            {withAccount && <p key="email" className="mt-0.5 truncate text-[11px] text-slate-500">{s.email ?? "—"}</p>}
          </div>
          <Progress shop={s} />
          <div className="text-xs sm:text-right">
            <p className={s.status === "failed" ? "font-medium text-rose-700" : s.status === "done" ? "font-medium text-emerald-700" : "font-medium text-slate-700"}>
              {statusText(s)}
            </p>
            <p className="text-[11px] text-slate-500">
              <Txt>
                {s.status === "failed"
                  ? (s.note ?? "")
                  : s.status === "done"
                    ? ""
                    : s.finishes_on
                      ? `Expected to finish ${finishDay(s.finishes_on, zone)}`
                      : ""}
              </Txt>
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}

/** Analytics: the account's own shops. Hidden once every shop is done. */
export function SalesReread({ onProgress }: { onProgress?: () => void }) {
  const { timeZone } = useSession();
  const [report, setReport] = useState<Report | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.salesReread();
      setReport((prev) => {
        // A shop finished since the last look: the figures below have changed.
        const doneBefore = prev?.shops.filter((s) => s.status === "done").length ?? null;
        const doneNow = next.shops.filter((s) => s.status === "done").length;
        if (doneBefore !== null && doneNow > doneBefore) onProgress?.();
        return next;
      });
    } catch {
      /* the panel is extra: Analytics works without it */
    }
  }, [onProgress]);

  useEffect(() => {
    load();
  }, [load]);

  const reading = report?.shops.some((s) => s.status === "reading") ?? false;
  useEffect(() => {
    if (!report?.active) return;
    const t = setInterval(load, reading ? 5000 : 60000);
    return () => clearInterval(t);
  }, [report?.active, reading, load]);

  if (!report || !report.active) return null;
  const open = report.shops.filter((s) => s.status !== "done").length;

  return (
    <section className="card space-y-2 p-4 text-sm text-slate-700" translate="no" aria-labelledby="reread-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="reread-title" className="font-medium text-slate-900">Reading your sales once more</h2>
        <p className="text-xs text-slate-500">
          <span>
            {`${open} of ${report.shops.length} ${report.shops.length === 1 ? "shop" : "shops"} to go`}
          </span>
          <Txt>{report.finishes_on ? ` · expected to finish ${finishDay(report.finishes_on, timeZone)}` : ""}</Txt>
        </p>
      </div>
      <p className="text-xs text-slate-500">
        <span>
          {`This is a one-time update: each shop's sales since ${day(report.reads_back_to)} (13 months) are read again so that every order can be tied to its listings. Shops are read one at a time, a limited number each day. A shop that is waiting keeps its figures; while a shop is being read, its figures fill in again as it goes. Nothing about buyers is read or kept.`}
        </span>
      </p>
      <Rows shops={report.shops} zone={timeZone} />
    </section>
  );
}

/** Admin → Usage: every shop, and tonight's share of the app's budget. */
export function SalesRereadAdminPanel() {
  const [report, setReport] = useState<SalesRereadAdmin | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => api.admin.salesReread().then((r) => alive && setReport(r)).catch(() => {});
    load();
    const t = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  if (!report || report.shops.length === 0) return null;
  const pct = report.nightly_cap ? Math.min(100, (report.used_tonight / report.nightly_cap) * 100) : 0;

  return (
    <section className="card p-5" translate="no" aria-labelledby="reread-admin-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="reread-admin-title" className="font-display text-2xl text-slate-900">Sales re-read</h2>
        <p className="text-xs text-slate-500">
          <span>{`One time, for shops read before order lines were kept · at most ${report.budget_percent}% of the app's Etsy budget a day · counts only`}</span>
        </p>
      </div>
      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4">
        <Stat label="Used today (UTC)" value={`${n(report.used_tonight)} / ${n(report.nightly_cap)}`} note={`${Math.round(pct)}% of today's share`} />
        <Stat label="Shops to go" value={n(report.queued + report.reading)} note={`${n(report.reading)} begun · ${n(report.queued)} not started`} />
        <Stat label="Requests still needed" value={`about ${n(report.requests_left)}`} note="One per 100 sales" />
        <Stat
          label="Expected to finish"
          value={report.active ? (report.finishes_on ? finishDay(report.finishes_on, "Europe/Istanbul") : "—") : "Done"}
          note={`Istanbul time · ${n(report.done)} done${report.failed ? ` · ${n(report.failed)} stopped` : ""}`}
        />
      </dl>
      <div className="mt-4">
        <Rows shops={report.shops} zone="Europe/Istanbul" withAccount />
      </div>
    </section>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div>
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-0.5 text-lg font-medium tabular-nums text-slate-900">{value}</dd>
      <dd className="text-[11px] text-slate-500">{note}</dd>
    </div>
  );
}
