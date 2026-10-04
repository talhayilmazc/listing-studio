"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Allowance } from "@/lib/types";

import { Txt } from "@/components/Txt";
const PERIOD_WORD: Record<string, string> = { daily: "today", weekly: "this week", monthly: "this month" };

export function periodWord(period: string): string {
  return PERIOD_WORD[period] ?? "this period";
}

/** The seller's own product allowance, fresh on every page and every minute. */
export function useAllowance(): Allowance | null {
  const pathname = usePathname();
  const [allowance, setAllowance] = useState<Allowance | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .myAllowance()
        .then((a) => !cancelled && setAllowance(a))
        .catch(() => {});
    load();
    const t = setInterval(load, 60_000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [pathname]);
  return allowance;
}

/**
 * "Listings generated" in the rail: designs written this period. Drafts and
 * publishing do not count. A different number from "Etsy requests today"
 * beneath it, with a different reset: this one at the seller's own midnight.
 */
export function RailAllowance() {
  const a = useAllowance();
  if (!a) return null;
  const share = a.amount ? Math.min(1, a.used / a.amount) : 1;
  const low = a.remaining <= Math.max(1, a.amount * 0.1);
  return (
    <div className="px-2 pb-2" title={`Listings generated ${periodWord(a.period)}: each design counts once, however many shops it goes to. Drafts and publishing do not count. Resets ${a.resets_label}.`}>
      <div className="flex items-baseline justify-between text-[11px]">
        <span className="text-[var(--rail-text)]">Listings generated</span>
        <span translate="no" className="tabular-nums text-[var(--rail-text)]">
          <span className={low ? "font-medium text-amber-400" : "font-medium text-[var(--rail-active)]"}>
            {a.remaining.toLocaleString()}
          </span>
          <span>{` / ${a.amount.toLocaleString()} left`}</span>
        </span>
      </div>
      <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-white/[0.08]">
        <div
          className={"h-full rounded-full transition-all " + (low ? "bg-amber-500" : "bg-emerald-500")}
          style={{ width: share * 100 + "%" }}
        />
      </div>
      <p translate="no" className="mt-1 text-[11px] text-[var(--rail-text)]">
        {`${a.used.toLocaleString()} used ${periodWord(a.period)} · resets ${a.resets_label.replace(/^\w+, /, "")}`}
      </p>
    </div>
  );
}

/** The allowance in Settings: what counts, what's used, what's left, when it resets. */
export function AllowanceSection() {
  const a = useAllowance();
  return (
    <section className="card space-y-3 p-5">
      <div>
        <h2 className="text-sm font-semibold text-slate-800">Listings generated</h2>
        <p className="mt-1 text-sm text-slate-500">
          <span>How many listings you can generate <span>{a ? periodWord(a.period) : "each period"}</span>. Each design written counts once,
          however many shops it is sent to (regenerating one, or replacing a listing&apos;s photos and text, counts again).
          Creating drafts and publishing do not count. This is a different number from &quot;Etsy requests today&quot; in the
          menu, which counts the requests your drafts and publishing make to Etsy and resets at 00:00 UTC.</span>
        </p>
      </div>
      {!a ? (
        <p key="loading" className="text-sm text-slate-400">Loading…</p>
      ) : (
        <div key="figures" translate="no" className="space-y-2">
          <div className="flex items-baseline justify-between text-sm">
            <span>
              <span className="text-2xl font-semibold text-slate-900">{a.remaining.toLocaleString()}</span>
              <span className="text-slate-500">{` of ${a.amount.toLocaleString()} left ${periodWord(a.period)}`}</span>
            </span>
            <span className="text-xs text-slate-500">{`Resets ${a.resets_label}`}</span>
          </div>
          <div className="progress">
            <div className="progress-fill" style={{ width: (a.amount ? Math.min(100, (a.used / a.amount) * 100) : 100) + "%" }} />
          </div>
          <p className="text-xs text-slate-500">
            <span><span>{`${a.generations} listings generated`}</span>
            <Txt>{a.pending ? ` · ${a.pending} being rewritten` : ""}</Txt>
            <Txt>{a.custom ? "" : " · the standard allowance"}</Txt></span>
          </p>
        </div>
      )}
    </section>
  );
}
