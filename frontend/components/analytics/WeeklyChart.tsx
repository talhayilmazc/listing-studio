"use client";

import { useState } from "react";
import { money } from "@/lib/analytics";

type Week = { start: string; units: number; revenue: number };

/** Clean ticks for the y axis: 0 and up to three round steps above the max. */
function ticks(max: number): number[] {
  if (max <= 0) return [0];
  const raw = max / 3;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const out = [];
  for (let v = 0; v <= max + step * 0.001; v += step) out.push(v);
  if (out[out.length - 1] < max) out.push(out[out.length - 1] + step);
  return out;
}

function day(iso: string): Date {
  return new Date(`${iso}T00:00:00Z`);
}

/**
 * A listing's weekly revenue over the 13 months kept (one series, so no legend:
 * the heading names it). Weeks inside the chosen period are in the accent; the
 * rest recede. Every column is its own hover and focus target, and the same
 * figures are in the table below it.
 */
export function WeeklyChart({ weeks, currency, periodStart }: { weeks: Week[]; currency: string | null; periodStart: string | null }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(0, ...weeks.map((w) => w.revenue));
  const ys = ticks(max);
  const top = ys[ys.length - 1] || 1;
  const from = periodStart ? day(periodStart).getTime() : Infinity;
  const inPeriod = (w: Week) => day(w.start).getTime() + 6 * 86400000 >= from;
  const label = (w: Week) => day(w.start).toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });

  return (
    <div>
      <div className="relative mt-3 flex h-44 gap-2">
        {/* y axis */}
        <div className="relative w-14 shrink-0 text-right text-[11px] tabular-nums text-slate-400">
          {ys.map((v) => (
            <span key={v} className="absolute right-1 translate-y-1/2" style={{ bottom: `${(v / top) * 100}%` }}>
              {money(v, currency).replace(/\.00$/, "")}
            </span>
          ))}
        </div>
        <div className="relative flex-1">
          {ys.map((v) => (
            <div key={v} className="absolute inset-x-0 border-t border-slate-200" style={{ bottom: `${(v / top) * 100}%` }} aria-hidden />
          ))}
          <div className="absolute inset-0 flex items-end gap-[2px]">
            {weeks.map((w, i) => (
              <button
                key={w.start}
                type="button"
                className="group relative flex h-full flex-1 items-end justify-center outline-none"
                onPointerEnter={() => setHover(i)}
                onPointerLeave={() => setHover(null)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
                aria-label={`Week of ${label(w)}: ${money(w.revenue, currency)}, ${w.units} sold`}
              >
                <span
                  className={
                    "block w-full max-w-[24px] rounded-t-[4px] transition-opacity " +
                    (inPeriod(w) ? "bg-brand-600" : "bg-slate-300") +
                    (hover === i ? " opacity-75" : "") +
                    " group-focus-visible:ring-2 group-focus-visible:ring-brand-500"
                  }
                  style={{ height: w.revenue ? `max(2px, ${(w.revenue / top) * 100}%)` : 0 }}
                />
              </button>
            ))}
          </div>
          {hover !== null && weeks[hover] && (
            <div
              className="pointer-events-none absolute -top-2 z-10 -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md border border-slate-200 bg-white px-2 py-1 text-xs shadow-card"
              style={{ left: `${((hover + 0.5) / weeks.length) * 100}%` }}
              role="status"
            >
              <span className="font-semibold tabular-nums text-slate-900">{money(weeks[hover].revenue, currency)}</span>
              <span className="text-slate-500">
                {" "}
                · {weeks[hover].units} sold · week of {label(weeks[hover])}
              </span>
            </div>
          )}
        </div>
      </div>
      {/* x axis: the first week of each month (every third month on a phone) */}
      <div className="ml-16 mt-1 flex gap-[2px] text-[11px] text-slate-400">
        {(() => {
          let month = -1;
          return weeks.map((w, i) => {
            const d = day(w.start);
            const first = i === 0 || d.getUTCMonth() !== day(weeks[i - 1].start).getUTCMonth();
            if (first) month += 1;
            return (
              <span key={w.start} className="relative flex-1">
                {first && i < weeks.length - 2 ? (
                  <span className={"absolute left-0 whitespace-nowrap " + (month % 3 ? "hidden sm:inline" : "")}>
                    {d.toLocaleDateString(undefined, { month: "short", timeZone: "UTC" })}
                    {d.getUTCMonth() === 0 || i === 0 ? ` ${String(d.getUTCFullYear()).slice(2)}` : ""}
                  </span>
                ) : null}
              </span>
            );
          });
        })()}
      </div>
      <details className="mt-6 text-sm">
        <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-800">Show as a table</summary>
        <div className="mt-2 max-h-64 overflow-y-auto rounded-lg border border-slate-100">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-slate-50 text-left text-slate-500">
              <tr>
                <th className="px-2 py-1 font-medium">Week of</th>
                <th className="px-2 py-1 text-right font-medium">Sold</th>
                <th className="px-2 py-1 text-right font-medium">Revenue</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {[...weeks].reverse().map((w) => (
                <tr key={w.start}>
                  <td className="px-2 py-1 text-slate-700">{w.start}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{w.units}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{money(w.revenue, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
