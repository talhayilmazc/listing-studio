"use client";

import { useState } from "react";
import { money } from "@/lib/analytics";

/** This period's line, and the one it is compared with (validated as a pair). */
export const LINE_COLORS = { current: "#2a78d6", comparison: "#eb6834" } as const;
const BAR = "#e7e5e4";
const BAR_STRONG = "#a8a29e";

export interface ChartPoint {
  key: string;
  /** Shown in the readout and the table ("Sep 14", "Week of Sep 14"). */
  label: string;
  /** Short axis label, only on the points that carry one. */
  tick?: string;
  bar: number;
  lines: (number | null)[];
  strong?: boolean;
}

export interface ChartLine {
  label: string;
  color: string;
  dashed?: boolean;
}

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

/**
 * Bars for the raw values (they recede) with lines for what to read: the
 * rolling average and the comparison. One money axis. Every column is a hover
 * and focus target; the legend names each mark and the same figures are in the
 * table below.
 */
export function Chart({
  points,
  barLabel,
  lines,
  currency,
}: {
  points: ChartPoint[];
  barLabel: string;
  lines: ChartLine[];
  currency: string | null;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const n = points.length;
  const max = Math.max(0, ...points.map((p) => Math.max(p.bar, ...p.lines.map((v) => v ?? 0))));
  const ys = ticks(max);
  const top = ys[ys.length - 1] || 1;
  const W = 1000;
  const H = 100;
  const x = (i: number) => ((i + 0.5) / n) * W;
  const y = (v: number) => H - (v / top) * H;
  const path = (k: number) => {
    let d = "";
    let pen = false;
    points.forEach((p, i) => {
      const v = p.lines[k];
      if (v === null || v === undefined) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(2)}`;
      pen = true;
    });
    return d;
  };
  const anyStrong = points.some((p) => p.strong);
  const bw = Math.max(1, (W / n) * 0.7);
  const short = (v: number) => money(v, currency).replace(/\.00$/, "");

  if (n === 0) return <p className="text-sm text-slate-400">Nothing to chart in this period.</p>;

  return (
    <div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-600">
        <li className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: anyStrong ? BAR_STRONG : BAR }} aria-hidden />
          <span>{barLabel}</span>
        </li>
        {lines.map((l) => (
          <li key={l.label} className="flex items-center gap-1.5">
            <svg width="18" height="6" aria-hidden>
              <line x1="0" y1="3" x2="18" y2="3" stroke={l.color} strokeWidth="2" strokeDasharray={l.dashed ? "4 3" : undefined} />
            </svg>
            <span>{l.label}</span>
          </li>
        ))}
      </ul>
      <div className="relative mt-3 flex h-48 gap-2">
        <div translate="no" className="relative w-14 shrink-0 text-right text-[11px] tabular-nums text-slate-400">
          {ys.map((v) => (
            <span key={v} className="absolute right-1 translate-y-1/2" style={{ bottom: `${(v / top) * 100}%` }}>
              {short(v)}
            </span>
          ))}
        </div>
        <div className="relative min-w-0 flex-1">
          {ys.map((v) => (
            <div key={v} className="absolute inset-x-0 border-t border-slate-100" style={{ bottom: `${(v / top) * 100}%` }} aria-hidden />
          ))}
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible" aria-hidden>
            {points.map((p, i) =>
              p.bar > 0 ? (
                <rect key={p.key} x={x(i) - bw / 2} y={y(p.bar)} width={bw} height={H - y(p.bar)} fill={p.strong ? BAR_STRONG : BAR} />
              ) : null,
            )}
            {hover !== null && (
              <line key="cross" x1={x(hover)} x2={x(hover)} y1={0} y2={H} stroke="#94a3b8" strokeWidth="1" vectorEffect="non-scaling-stroke" />
            )}
            {lines.map((l, k) => (
              <path
                key={l.label}
                d={path(k)}
                fill="none"
                stroke={l.color}
                strokeWidth="2"
                strokeLinejoin="round"
                strokeLinecap="round"
                strokeDasharray={l.dashed ? "5 4" : undefined}
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </svg>
          <div className="absolute inset-0 flex">
            {points.map((p, i) => (
              <button
                key={p.key}
                type="button"
                className="h-full flex-1 outline-none focus-visible:bg-brand-500/10"
                onPointerEnter={() => setHover(i)}
                onPointerLeave={() => setHover(null)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
                aria-label={`${p.label}: ${barLabel} ${money(p.bar, currency)}${lines
                  .map((l, k) => (p.lines[k] === null || p.lines[k] === undefined ? "" : `, ${l.label} ${money(p.lines[k], currency)}`))
                  .join("")}`}
              />
            ))}
          </div>
          {hover !== null && points[hover] && (
            <div
              key="readout"
              className={
                "pointer-events-none absolute top-0 z-10 w-max max-w-[16rem] rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-xs shadow-card " +
                (hover > n / 2 ? "-translate-x-full" : "")
              }
              style={{ left: `calc(${((hover + 0.5) / n) * 100}% + ${hover > n / 2 ? -8 : 8}px)` }}
              role="status"
              translate="no"
            >
              <p className="font-medium text-slate-700">{points[hover].label}</p>
              <p className="mt-0.5 flex items-center justify-between gap-4">
                <span className="text-slate-500">{barLabel}</span>
                <span className="font-semibold tabular-nums text-slate-900">{money(points[hover].bar, currency)}</span>
              </p>
              {lines.map((l, k) => (
                <p key={l.label} className="flex items-center justify-between gap-4">
                  <span className="flex items-center gap-1.5 text-slate-500">
                    <span className="inline-block h-0.5 w-3" style={{ background: l.color }} aria-hidden />
                    <span>{l.label}</span>
                  </span>
                  <span className="tabular-nums text-slate-900">{money(points[hover].lines[k], currency)}</span>
                </p>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="ml-16 mt-1 flex text-[11px] text-slate-400" translate="no">
        {points.map((p) => (
          <span key={p.key} className="relative h-4 flex-1">
            {p.tick ? <span className="absolute left-0 whitespace-nowrap">{p.tick}</span> : null}
          </span>
        ))}
      </div>
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-800">Show as a table</summary>
        <div className="mt-2 max-h-64 overflow-auto rounded-lg border border-slate-100">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-slate-50 text-left text-slate-500">
              <tr>
                <th className="px-2 py-1 font-medium" />
                <th className="px-2 py-1 text-right font-medium">{barLabel}</th>
                {lines.map((l) => (
                  <th key={l.label} className="px-2 py-1 text-right font-medium">{l.label}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {[...points].reverse().map((p) => (
                <tr key={p.key}>
                  <td className="whitespace-nowrap px-2 py-1 text-slate-700">{p.label}</td>
                  <td translate="no" className="px-2 py-1 text-right tabular-nums">{money(p.bar, currency)}</td>
                  {lines.map((l, k) => (
                    <td key={l.label} translate="no" className="px-2 py-1 text-right tabular-nums">{money(p.lines[k], currency)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
