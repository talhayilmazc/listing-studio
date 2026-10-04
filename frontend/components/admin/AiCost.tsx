"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { AiCost, AiPrice, AiTotals } from "@/lib/types";

/**
 * What the AI provider's work is costing us, live. Admin only: calls, tokens,
 * models and dollars are our cost of goods, and no screen or API response a
 * seller can reach carries them.
 *
 * Every call is one row on the server (worked, refused, unusable, retried), so
 * a day's total here should match the provider's console for the same UTC day.
 */

const usd = (v: string | null, digits = 2) => (v === null ? "—" : `$${Number(v).toFixed(digits)}`);
const n = (v: number) => v.toLocaleString();
const time = (iso: string) =>
  new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });

type View = "sellers" | "purposes" | "days" | "calls" | "prices";
const VIEWS: { id: View; label: string }[] = [
  { id: "sellers", label: "By seller" },
  { id: "purposes", label: "By purpose" },
  { id: "days", label: "By day" },
  { id: "calls", label: "Latest calls" },
  { id: "prices", label: "Prices" },
];

export function AiCostPanel({ cost, onChanged, onError }: { cost: AiCost | null; onChanged: () => void; onError: (m: string) => void }) {
  const [view, setView] = useState<View>("sellers");
  if (!cost) return <p className="text-sm text-slate-400">Loading AI cost…</p>;

  return (
    <section className="card p-5" translate="no" aria-labelledby="ai-cost-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="ai-cost-title" className="font-display text-2xl text-slate-900">AI cost</h2>
        <p className="text-xs text-slate-500">
          <span>Every call to the AI provider · days are UTC · updated <span>{time(cost.as_of)}</span> · sellers never see this</span>
        </p>
      </div>

      {cost.unpriced_models.length > 0 && (
        <p key="unpriced" className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <span>No price is set for <span>{cost.unpriced_models.join(", ")}</span>, so its calls are counted but not costed and the totals are too low. Add it under Prices.</span>
        </p>
      )}

      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4">
        <Stat label="Today" value={usd(cost.today.cost_usd)} note={`${n(cost.today.calls)} calls · ${n(cost.today.failed)} failed`} flag={cost.today.unpriced} />
        <Stat label="This month" value={usd(cost.this_month.cost_usd)} note={`${n(cost.this_month.calls)} calls · ${n(cost.this_month.failed)} failed`} flag={cost.this_month.unpriced} />
        <Stat label="Per listing, this month" value={usd(cost.this_month.cost_per_listing_usd, 4)} note={`${n(cost.this_month.listings)} listings written`} />
        <Stat label="Per listing, today" value={usd(cost.today.cost_per_listing_usd, 4)} note={`${n(cost.today.listings)} listings written`} />
      </dl>

      <div className="mt-5 flex flex-wrap gap-1 border-b border-slate-200" role="tablist" aria-label="AI cost view">
        {VIEWS.map((v) => (
          <button
            key={v.id}
            type="button"
            role="tab"
            aria-selected={view === v.id}
            onClick={() => setView(v.id)}
            className={
              "-mb-px border-b-2 px-3 py-2 text-sm max-sm:min-h-[2.75rem] " +
              (view === v.id ? "border-brand-600 font-medium text-slate-900" : "border-transparent text-slate-500 hover:text-slate-800")
            }
          >
            {v.label}
          </button>
        ))}
      </div>

      <div className="mt-4 overflow-x-auto">
        {view === "sellers" && (
          <Breakdown key="sellers" first="Seller" rows={cost.sellers.map((s) => ({ key: s.id ?? "none", name: s.email ?? "No seller (our own runs, deleted accounts)", today: s.today, month: s.this_month }))} />
        )}
        {view === "purposes" && (
          <Breakdown key="purposes" first="Purpose" rows={cost.purposes.map((p) => ({ key: p.purpose, name: p.label, today: p.today, month: p.this_month }))} />
        )}
        {view === "days" && <Days key="days" cost={cost} />}
        {view === "calls" && <Calls key="calls" cost={cost} />}
        {view === "prices" && <Prices key="prices" prices={cost.prices} onChanged={onChanged} onError={onError} />}
      </div>
    </section>
  );
}

function Stat({ label, value, note, flag }: { label: string; value: string; note: string; flag?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 font-display text-3xl leading-none tabular-nums text-slate-900">
        <span>{value}</span>
        {flag && <span key="flag" className="ml-1 align-top text-sm text-amber-700" title="A model has no price set: this is a floor">+</span>}
      </dd>
      <p className="mt-1 text-[11px] text-slate-500">{note}</p>
    </div>
  );
}

const TH = "py-1.5 pr-4 text-right font-medium";
const TD = "py-1.5 pr-4 text-right tabular-nums";

function Breakdown({ first, rows }: { first: string; rows: { key: string; name: string; today: AiTotals; month: AiTotals }[] }) {
  if (rows.length === 0) return <p className="text-sm text-slate-500">No calls this month.</p>;
  return (
    <table className="w-full min-w-[44rem] text-left text-xs">
      <thead>
        <tr className="border-b border-slate-200 text-slate-400">
          <th className="py-1.5 pr-4 font-medium">{first}</th>
          <th className={TH}>Today</th>
          <th className={TH}>Calls today</th>
          <th className={TH}>This month</th>
          <th className={TH}>Calls</th>
          <th className={TH}>Failed</th>
          <th className={TH}>Listings</th>
          <th className="py-1.5 text-right font-medium">Per listing</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key} className="border-b border-slate-100 last:border-0">
            <td className="max-w-[18rem] truncate py-1.5 pr-4 text-slate-800">{r.name}</td>
            <td className={TD + " text-slate-900"}>{usd(r.today.cost_usd, 4)}</td>
            <td className={TD}>{n(r.today.calls)}</td>
            <td className={TD + " font-medium text-slate-900"}>{usd(r.month.cost_usd, 4)}</td>
            <td className={TD}>{n(r.month.calls)}</td>
            <td className={TD}>{n(r.month.failed)}</td>
            <td className={TD}>{n(r.month.listings)}</td>
            <td className="py-1.5 text-right tabular-nums">{usd(r.month.cost_per_listing_usd, 4)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Days({ cost }: { cost: AiCost }) {
  if (cost.days.length === 0) return <p className="text-sm text-slate-500">No calls in the last 31 days.</p>;
  return (
    <div>
      <p className="mb-2 text-xs text-slate-500">
        To check against the provider&apos;s console, compare a finished UTC day: the same model, the same five token counts, the same total.
      </p>
      <table className="w-full min-w-[52rem] text-left text-xs">
        <thead>
          <tr className="border-b border-slate-200 text-slate-400">
            <th className="py-1.5 pr-4 font-medium">Day (UTC) · model</th>
            <th className={TH}>Calls</th>
            <th className={TH}>Input</th>
            <th className={TH}>Cache write 5m</th>
            <th className={TH}>Cache write 1h</th>
            <th className={TH}>Cache read</th>
            <th className={TH}>Output</th>
            <th className="py-1.5 text-right font-medium">Cost</th>
          </tr>
        </thead>
        <tbody>
          {cost.days.flatMap((d) => [
            <tr key={d.day} className="border-b border-slate-100 bg-stone-50">
              <td className="py-1.5 pr-4 font-medium text-slate-800">{d.day}</td>
              <td className={TD}>{n(d.calls)}</td>
              <td className={TD}>{n(d.input_tokens)}</td>
              <td className={TD}>{n(d.cache_write_tokens)}</td>
              <td className={TD}>{n(d.cache_write_1h_tokens)}</td>
              <td className={TD}>{n(d.cache_read_tokens)}</td>
              <td className={TD}>{n(d.output_tokens)}</td>
              <td className="py-1.5 text-right font-medium tabular-nums text-slate-900">{usd(d.cost_usd, 4)}</td>
            </tr>,
            ...d.models.map((m) => (
              <tr key={d.day + m.model} className="border-b border-slate-100">
                <td className="py-1.5 pl-4 pr-4 text-slate-600">{m.model}</td>
                <td className={TD}>{n(m.calls)}</td>
                <td className={TD}>{n(m.input_tokens)}</td>
                <td className={TD}>{n(m.cache_write_tokens)}</td>
                <td className={TD}>{n(m.cache_write_1h_tokens)}</td>
                <td className={TD}>{n(m.cache_read_tokens)}</td>
                <td className={TD}>{n(m.output_tokens)}</td>
                <td className="py-1.5 text-right tabular-nums">{m.unpriced ? "no price" : usd(m.cost_usd, 4)}</td>
              </tr>
            )),
          ])}
        </tbody>
      </table>
    </div>
  );
}

function Calls({ cost }: { cost: AiCost }) {
  if (cost.recent.length === 0) return <p className="text-sm text-slate-500">No calls yet.</p>;
  return (
    <table className="w-full min-w-[52rem] text-left text-xs">
      <thead>
        <tr className="border-b border-slate-200 text-slate-400">
          <th className="py-1.5 pr-4 font-medium">When</th>
          <th className="py-1.5 pr-4 font-medium">Seller</th>
          <th className="py-1.5 pr-4 font-medium">Purpose</th>
          <th className="py-1.5 pr-4 font-medium">Model</th>
          <th className={TH}>In</th>
          <th className={TH}>Cached</th>
          <th className={TH}>Out</th>
          <th className={TH}>Cost</th>
          <th className="py-1.5 font-medium">Result</th>
        </tr>
      </thead>
      <tbody>
        {cost.recent.map((c, i) => (
          <tr key={c.at + i} className="border-b border-slate-100 last:border-0">
            <td className="whitespace-nowrap py-1.5 pr-4 text-slate-600">{time(c.at)}</td>
            <td className="max-w-[12rem] truncate py-1.5 pr-4 text-slate-800">{c.email ?? "—"}</td>
            <td className="py-1.5 pr-4">{c.purpose}</td>
            <td className="py-1.5 pr-4 text-slate-600">{c.model}</td>
            <td className={TD}>{n(c.input_tokens)}</td>
            <td className={TD}>{n(c.cache_read_tokens + c.cache_write_tokens)}</td>
            <td className={TD}>{n(c.output_tokens)}</td>
            <td className={TD}>{usd(c.cost_usd, 5)}</td>
            <td className={"py-1.5 " + (c.ok ? "text-emerald-700" : "text-rose-700")}>{c.ok ? "ok" : (c.error ?? "failed")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const RATES = ["input", "output", "cache_write", "cache_write_1h", "cache_read"] as const;
const RATE_LABEL: Record<(typeof RATES)[number], string> = {
  input: "Input",
  output: "Output",
  cache_write: "Cache write 5m",
  cache_write_1h: "Cache write 1h",
  cache_read: "Cache read",
};

function Prices({ prices, onChanged, onError }: { prices: AiPrice[]; onChanged: () => void; onError: (m: string) => void }) {
  const blank = { model: "", input: "", output: "", cache_write: "", cache_write_1h: "", cache_read: "" };
  const [draft, setDraft] = useState<Omit<AiPrice, "custom">>(blank);
  const [busy, setBusy] = useState(false);

  async function run(work: () => Promise<unknown>) {
    setBusy(true);
    try {
      await work();
      setDraft(blank);
      onChanged();
    } catch (e: any) {
      onError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        USD per million tokens. A new price applies to calls from now on; calls of a model that had no price are costed when one is set.
      </p>
      <table className="w-full min-w-[44rem] text-left text-xs">
        <thead>
          <tr className="border-b border-slate-200 text-slate-400">
            <th className="py-1.5 pr-4 font-medium">Model</th>
            {RATES.map((r) => (
              <th key={r} className={TH}>{RATE_LABEL[r]}</th>
            ))}
            <th className="py-1.5 font-medium" />
          </tr>
        </thead>
        <tbody>
          {prices.map((p) => (
            <tr key={p.model} className="border-b border-slate-100">
              <td className="py-1.5 pr-4 text-slate-800">{p.model}</td>
              {RATES.map((r) => (
                <td key={r} className={TD}>{`$${Number(p[r])}`}</td>
              ))}
              <td className="py-1.5 text-right">
                <button type="button" className="tap mr-3 text-brand-700 underline decoration-dotted underline-offset-2" disabled={busy}
                  onClick={() => setDraft({ model: p.model, input: p.input, output: p.output, cache_write: p.cache_write, cache_write_1h: p.cache_write_1h, cache_read: p.cache_read })}>
                  Edit
                </button>
                {p.custom && (
                  <button key="reset" type="button" className="tap text-slate-500 underline decoration-dotted underline-offset-2" disabled={busy}
                    onClick={() => run(() => api.admin.resetAiPrice(p.model))}>
                    Reset
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form
        className="flex flex-wrap items-end gap-2 border-t border-slate-100 pt-3"
        onSubmit={(e) => {
          e.preventDefault();
          run(() => api.admin.setAiPrice(draft));
        }}
      >
        <label className="block">
          <span className="text-[11px] text-slate-500">Model</span>
          <input className="field mt-0.5 w-52 py-1 text-xs" value={draft.model} required placeholder="claude-…" onChange={(e) => setDraft({ ...draft, model: e.target.value })} />
        </label>
        {RATES.map((r) => (
          <label key={r} className="block">
            <span className="text-[11px] text-slate-500">{RATE_LABEL[r]}</span>
            <input className="field mt-0.5 w-24 py-1 text-xs tabular-nums" inputMode="decimal" required value={draft[r]} onChange={(e) => setDraft({ ...draft, [r]: e.target.value })} />
          </label>
        ))}
        <button type="submit" className="btn-primary px-3 py-1.5 text-xs" disabled={busy}>Save price</button>
      </form>
    </div>
  );
}
