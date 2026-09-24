"use client";

import type { AdminUsage } from "@/lib/types";

/**
 * The scarce resource: Etsy's app-wide budget of 5,000 requests a day, shared by
 * every tenant. Colour carries one job only — severity on the meters (accent,
 * then amber, then red as the budget runs out). Tenants are told apart by their
 * row label, never by colour, so no categorical palette is involved.
 */

type Severity = "ok" | "warn" | "danger";

/**
 * `pauseAt` is where new work stops being started (90% of Etsy's limit app-wide,
 * production-spec C). Reaching it is the red state; approaching it is amber.
 */
export function severity(used: number, limit: number, pauseAt: number = limit): Severity {
  if (limit <= 0 || used >= pauseAt) return "danger";
  return used >= pauseAt * 0.85 ? "warn" : "ok";
}

const FILL: Record<Severity, string> = {
  ok: "bg-brand-600",
  warn: "bg-amber-600",
  danger: "bg-rose-600",
};
// The unfilled track is a lighter step of the same ramp, so state reads across the bar.
const TRACK: Record<Severity, string> = {
  ok: "bg-brand-100",
  warn: "bg-amber-100",
  danger: "bg-rose-100",
};
const STATE_LABEL: Record<Severity, string | null> = {
  ok: null,
  warn: "nearing the pause",
  danger: "new work paused",
};

export function Meter({
  used,
  limit,
  pauseAt,
  height = "h-2",
  label,
}: {
  used: number;
  limit: number;
  /** Draws the pause line and colours the meter against it. */
  pauseAt?: number;
  height?: string;
  label: string;
}) {
  const sev = severity(used, limit, pauseAt ?? limit);
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 100;
  const markPct = pauseAt !== undefined && limit > 0 ? (pauseAt / limit) * 100 : null;
  return (
    <div className="relative">
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={limit}
        aria-valuenow={used}
        className={`w-full overflow-hidden rounded-full ${height} ${TRACK[sev]}`}
      >
        <div
          className={`h-full rounded-full transition-all ${FILL[sev]}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      {markPct !== null && markPct < 100 && (
        // The pause line: a 2px tick standing proud of the track, in ink, not a series colour.
        <span
          aria-hidden
          className="absolute -inset-y-1 w-[2px] rounded-full bg-slate-700"
          style={{ left: `calc(${markPct}% - 1px)` }}
        />
      )}
    </div>
  );
}

/** Always visible at the top of /admin: today's app-wide budget, at a glance. */
export function UsageSummary({ usage }: { usage: AdminUsage | null }) {
  if (!usage) {
    return (
      <div className="card space-y-3 p-6">
        <div className="h-3 w-40 animate-pulse rounded bg-slate-100" />
        <div className="h-12 w-56 animate-pulse rounded bg-slate-100" />
        <div className="h-3 w-full animate-pulse rounded-full bg-slate-100" />
      </div>
    );
  }
  const sev = severity(usage.global_used, usage.global_limit, usage.pause_at);
  const busiest = usage.tenants.filter((t) => t.used_today > 0).slice(0, 3);
  return (
    <section className="card p-6" aria-labelledby="usage-summary">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h2 id="usage-summary" className="text-sm font-medium text-slate-600">
          Etsy API requests today, all tenants
        </h2>
        <span className="text-xs text-slate-400">resets 00:00 UTC · {usage.usage_date}</span>
      </div>
      <p className="mt-2 flex flex-wrap items-baseline gap-x-3">
        <span className="font-display text-5xl leading-none text-slate-900">
          {usage.global_used.toLocaleString()}
        </span>
        <span className="text-lg text-slate-400">of {usage.global_limit.toLocaleString()}</span>
        {STATE_LABEL[sev] && (
          <span
            className={
              "rounded-md border px-1.5 py-0.5 text-xs font-medium " +
              (sev === "danger"
                ? "border-rose-200 bg-rose-50 text-rose-700"
                : "border-amber-200 bg-amber-50 text-amber-700")
            }
          >
            {STATE_LABEL[sev]}
          </span>
        )}
      </p>
      <div className="mt-4">
        <Meter
          used={usage.global_used}
          limit={usage.global_limit}
          pauseAt={usage.pause_at}
          height="h-3"
          label="App-wide Etsy requests used today"
        />
      </div>
      <p className="mt-2 text-xs text-slate-500">
        {usage.global_used >= usage.pause_at
          ? `New jobs wait for the reset; the last ${(usage.global_limit - usage.pause_at).toLocaleString()} are held for work already running`
          : `${Math.max(0, usage.pause_at - usage.global_used).toLocaleString()} until new work pauses at ${usage.pause_at.toLocaleString()}`}
        {" · "}
        {usage.global_remaining.toLocaleString()} left today
        {" · "}
        <span className="text-slate-700">
          {usage.shops_used} of {usage.shops_limit} shops connected
        </span>
        {usage.shops_used < usage.shops_limit
          ? ` (${usage.shops_limit - usage.shops_used} slots left)`
          : " (no slots left)"}
        {busiest.length > 0 && (
          <>
            {" · most used by "}
            {busiest.map((t, i) => (
              <span key={t.id}>
                {i > 0 && ", "}
                <span className="text-slate-700">{t.email}</span>{" "}
                <span className="tabular-nums">({t.used_today.toLocaleString()})</span>
              </span>
            ))}
          </>
        )}
      </p>
    </section>
  );
}

/** Seven days, app-wide. One series, so no legend: the heading names it. */
function HistoryChart({ history }: { history: AdminUsage["history"] }) {
  const max = Math.max(1, ...history.map((d) => d.count));
  const peak = history.reduce((a, b) => (b.count > a.count ? b : a), history[0]);
  const today = history[history.length - 1];
  const H = 140;
  const label = (d: string) =>
    new Date(d + "T00:00:00Z").toLocaleDateString(undefined, { weekday: "short", timeZone: "UTC" });

  return (
    <figure>
      <div className="flex items-end gap-[2px]" style={{ height: H + 22 }}>
        {history.map((d) => {
          const isToday = d === today;
          // Label only the story: today and the peak. Every value is in the table.
          const labelled = isToday || (d === peak && d.count > 0);
          const h = d.count > 0 ? Math.max(4, Math.round((d.count / max) * H)) : 0;
          return (
            <div
              key={d.date}
              // The hit target is the whole column, not just the bar.
              className="group relative flex h-full flex-1 flex-col items-center justify-end"
              title={`${d.date}: ${d.count.toLocaleString()} requests`}
            >
              {labelled && (
                <span className="mb-1 text-xs tabular-nums text-slate-600">
                  {d.count.toLocaleString()}
                </span>
              )}
              <div
                className={
                  "w-full max-w-[24px] rounded-t-[4px] transition-opacity group-hover:opacity-80 " +
                  (isToday ? "bg-brand-600" : "bg-slate-300")
                }
                style={{ height: h }}
              />
            </div>
          );
        })}
      </div>
      <div className="border-t border-slate-200" />
      <div className="mt-1.5 flex gap-[2px]">
        {history.map((d, i) => (
          <span key={d.date} className="flex-1 text-center text-xs text-slate-400">
            {i === history.length - 1 ? "Today" : label(d.date)}
          </span>
        ))}
      </div>
      <details className="mt-3 text-xs text-slate-500">
        <summary className="cursor-pointer select-none hover:text-slate-800">Show as table</summary>
        <table className="mt-2 w-full max-w-sm text-left">
          <thead>
            <tr className="text-slate-400">
              <th className="py-1 font-medium">Date</th>
              <th className="py-1 text-right font-medium">Requests</th>
            </tr>
          </thead>
          <tbody>
            {history.map((d) => (
              <tr key={d.date} className="border-t border-slate-100">
                <td className="py-1 tabular-nums">{d.date}</td>
                <td className="py-1 text-right tabular-nums text-slate-700">
                  {d.count.toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

/** Per-tenant sparkline in the de-emphasis hue, today in the accent. */
function Spark({ history }: { history: AdminUsage["history"] }) {
  const max = Math.max(1, ...history.map((d) => d.count));
  return (
    <span className="flex h-6 items-end gap-[2px]" aria-hidden>
      {history.map((d, i) => (
        <span
          key={d.date}
          title={`${d.date}: ${d.count.toLocaleString()}`}
          className={
            "w-1.5 rounded-t-sm " +
            (i === history.length - 1 ? "bg-brand-600" : d.count ? "bg-slate-300" : "bg-slate-200")
          }
          style={{ height: Math.max(2, Math.round((d.count / max) * 24)) }}
        />
      ))}
    </span>
  );
}

export function UsageTab({ usage }: { usage: AdminUsage | null }) {
  if (!usage) return <div className="card h-64 animate-pulse bg-slate-50" />;
  const week = usage.history.reduce((n, d) => n + d.count, 0);
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <section className="card min-w-0 p-6" aria-labelledby="usage-history">
        <div className="flex items-baseline justify-between">
          <h2 id="usage-history" className="text-sm font-medium text-slate-600">
            App-wide requests, last 7 days
          </h2>
          <span className="text-xs tabular-nums text-slate-400">
            {week.toLocaleString()} this week
          </span>
        </div>
        <div className="mt-5">
          <HistoryChart history={usage.history} />
        </div>
      </section>

      <section className="card min-w-0 p-6" aria-labelledby="usage-tenants">
        <h2 id="usage-tenants" className="text-sm font-medium text-slate-600">
          By tenant, today
        </h2>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[520px] text-left text-sm">
            <thead>
              <tr className="text-xs text-slate-400">
                <th className="pb-2 font-medium">Tenant</th>
                <th className="pb-2 font-medium">Today, of their ceiling</th>
                <th className="pb-2 text-right font-medium">Share</th>
                <th className="pb-2 pl-4 font-medium">7 days</th>
              </tr>
            </thead>
            <tbody>
              {usage.tenants.map((t) => {
                const share = usage.global_used ? t.used_today / usage.global_used : 0;
                return (
                  <tr key={t.id} className="border-t border-slate-100 align-middle">
                    <td className="max-w-[240px] py-2.5 pr-4">
                      <span className="block truncate text-slate-800" title={t.email}>
                        {t.email}
                      </span>
                      {t.paused_reason && (
                        <span className="text-xs text-amber-700">
                          work waiting ·{" "}
                          {t.paused_reason === "global_quota" ? "app-wide pause" : "own allowance used"}
                        </span>
                      )}
                    </td>
                    <td className="w-[40%] py-2.5 pr-4">
                      <div className="flex items-center gap-3">
                        <Meter used={t.used_today} limit={t.daily_quota} label={`${t.email} requests today`} />
                        <span className="shrink-0 text-xs tabular-nums text-slate-500">
                          {t.used_today.toLocaleString()} / {t.daily_quota.toLocaleString()}
                        </span>
                      </div>
                    </td>
                    <td className="py-2.5 text-right text-xs tabular-nums text-slate-500">
                      {Math.round(share * 100)}%
                    </td>
                    <td className="py-2.5 pl-4">
                      <Spark history={t.history} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
