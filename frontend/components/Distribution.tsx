"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Content, DistributionPreview, GroupPlanPreview, GroupSchedule, ShopGroups } from "@/lib/types";
import { Txt } from "./Txt";

function tomorrow(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const day = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" });

/**
 * Send listings to shop groups and schedule them (v8 §B). Pick groups, then send
 * the chosen listings to one group, or split the approved listings evenly across
 * the groups in order. Every shop of a group gets the group's listings and no
 * other group's. Then the schedule: start date, listings per shop per day, the
 * daily window, spacing, and a stagger between the shops of a group. Nothing is
 * queued before the calendar and the Etsy requests per day are shown and
 * confirmed; a day that is full spills to the next.
 */
export function Distribution({ batchId, items, onDone }: { batchId: string; items: Content[]; onDone: () => void }) {
  const [groups, setGroups] = useState<ShopGroups | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [mode, setMode] = useState<"split" | "assign">("split");
  const [selected, setSelected] = useState<string[]>([]);
  const [preview, setPreview] = useState<DistributionPreview | null>(null);
  const [schedule, setSchedule] = useState<GroupSchedule>({
    start_date: tomorrow(),
    per_shop_per_day: 5,
    window_start: "09:00",
    window_end: "17:00",
    spacing_minutes: 30,
    stagger_minutes: 5,
  });
  const [plan, setPlan] = useState<GroupPlanPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  useEffect(() => {
    api.shopGroups().then(setGroups).catch(() => setGroups({ groups: [], ungrouped: [] }));
  }, []);

  const approved = useMemo(() => items.filter((c) => c.approved), [items]);
  const assignments = (preview?.rows ?? []).map((r) => ({ content_id: r.content_id, group_id: r.group_id }));

  async function run<T>(fn: () => Promise<T>, then: (v: T) => void) {
    setBusy(true);
    setError(null);
    try {
      then(await fn());
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  if (groups === null) return null;
  const usable = groups.groups.filter((g) => g.shops.length > 0);
  if (usable.length === 0) {
    return (
      <section className="card p-4 text-sm text-slate-600">
        <span>Group your shops to send listings to a whole group at once and schedule them per shop. </span>
        <Link href="/connect" className="text-brand-700 underline">Create shop groups</Link>
      </section>
    );
  }

  return (
    <section className="card space-y-4 p-4 sm:p-5" aria-labelledby="distribution-heading">
      <div>
        <h2 id="distribution-heading" className="text-sm font-medium text-slate-800">Send to shop groups</h2>
        <p className="mt-0.5 text-xs text-slate-500">
          Every shop in a group gets the group&apos;s listings, and no other group&apos;s. Only approved listings are sent.
        </p>
      </div>

      <fieldset className="space-y-1">
        <legend className="text-xs font-medium text-slate-600">Groups</legend>
        <div className="flex flex-wrap gap-x-4">
          {usable.map((g) => (
            <label key={g.id} className="flex min-h-[2.75rem] items-center gap-2 text-sm sm:min-h-0">
              <input type="checkbox" className="h-4 w-4" checked={chosen.includes(g.id)}
                onChange={(e) => {
                  setChosen((c) => (e.target.checked ? [...c, g.id] : c.filter((x) => x !== g.id)));
                  setPreview(null);
                  setPlan(null);
                }} />
              <span><span translate="no">{g.name}</span><span translate="no" className="text-xs text-slate-400">{` · ${g.shops.length} shop${g.shops.length === 1 ? "" : "s"}`}</span></span>
            </label>
          ))}
        </div>
      </fieldset>

      <fieldset className="space-y-1">
        <legend className="text-xs font-medium text-slate-600">How</legend>
        <label className="flex min-h-[2.75rem] items-center gap-2 text-sm sm:min-h-0">
          <input type="radio" name="mode" checked={mode === "split"} onChange={() => { setMode("split"); setPreview(null); setPlan(null); }} />
          <span translate="no">{`Split evenly: ${approved.length} approved listing${approved.length === 1 ? "" : "s"} divided across the groups, in order`}</span>
        </label>
        <label className="flex min-h-[2.75rem] items-center gap-2 text-sm sm:min-h-0">
          <input type="radio" name="mode" checked={mode === "assign"} onChange={() => { setMode("assign"); setPreview(null); setPlan(null); }} />
          <span>Send the listings I choose to one group</span>
        </label>
        {mode === "assign" && (
          <div key="pick" className="max-h-48 space-y-0.5 overflow-y-auto rounded border border-slate-200 p-2">
            {approved.map((c) => (
              <label key={c.id} className="flex min-h-[2.75rem] items-center gap-2 text-xs sm:min-h-0">
                <input type="checkbox" className="h-4 w-4" checked={selected.includes(c.id)}
                  onChange={(e) => setSelected((s) => (e.target.checked ? [...s, c.id] : s.filter((x) => x !== c.id)))} />
                <span className="truncate">{c.title || "Untitled"}</span>
              </label>
            ))}
          </div>
        )}
      </fieldset>

      <button type="button" className="btn-secondary" disabled={busy || chosen.length === 0 || (mode === "assign" && (selected.length === 0 || chosen.length !== 1))}
        onClick={() => run(() => api.previewDistribution(batchId, { mode, group_ids: chosen, content_ids: mode === "assign" ? selected : [] }), (p) => { setPreview(p); setPlan(null); })}>
        Preview who gets what
      </button>
      {mode === "assign" && chosen.length > 1 && <p key="one" className="text-xs text-amber-800">Choose one group for the listings you pick.</p>}

      {preview && (
        <div key="preview" className="space-y-2">
          <p translate="no" className="text-xs text-slate-600">
            {Object.entries(preview.per_group).map(([g, n]) => `${g}: ${n}`).join(" · ")}
          </p>
          <ul className="max-h-60 divide-y divide-slate-100 overflow-y-auto rounded border border-slate-200 text-xs">
            {preview.rows.map((r) => (
              <li key={r.content_id} className="space-y-0.5 p-2">
                <p className="truncate font-medium text-slate-800">{r.title}</p>
                <p className="flex flex-wrap gap-x-2">
                  <span translate="no" className="text-slate-500">{r.group_name}</span>
                  {r.shops.map((s) => (
                    <span key={s.shop_id} translate="no" className={s.ok ? "text-emerald-700" : "text-amber-800"} title={s.reason ?? undefined}>
                      {s.ok ? `✓ ${s.shop_name}` : `✕ ${s.shop_name}: ${s.reason}`}
                    </span>
                  ))}
                </p>
                {r.warning && <p key="warn" className="text-amber-800"><Txt>{r.warning}</Txt></p>}
              </li>
            ))}
          </ul>
          {preview.left_out.length > 0 && (
            <p key="left" translate="no" className="text-xs text-slate-500">
              {`Not sent: ${preview.left_out.map((l) => `${l.title || "a listing"} (${l.reason})`).join("; ")}`}
            </p>
          )}

          <fieldset className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
            <legend className="col-span-full mb-1 text-xs font-medium text-slate-600">Schedule (your time zone)</legend>
            <label className="space-y-0.5"><span>Start</span>
              <input type="date" className="field py-1" value={schedule.start_date} onChange={(e) => setSchedule({ ...schedule, start_date: e.target.value })} /></label>
            <label className="space-y-0.5"><span>Listings per shop per day</span>
              <input type="number" min={1} max={50} className="field py-1" value={schedule.per_shop_per_day}
                onChange={(e) => setSchedule({ ...schedule, per_shop_per_day: Number(e.target.value) })} /></label>
            <label className="space-y-0.5"><span>From</span>
              <input type="time" className="field py-1" value={schedule.window_start} onChange={(e) => setSchedule({ ...schedule, window_start: e.target.value })} /></label>
            <label className="space-y-0.5"><span>Until</span>
              <input type="time" className="field py-1" value={schedule.window_end} onChange={(e) => setSchedule({ ...schedule, window_end: e.target.value })} /></label>
            <label className="space-y-0.5"><span>Minutes between listings</span>
              <input type="number" min={1} className="field py-1" value={schedule.spacing_minutes}
                onChange={(e) => setSchedule({ ...schedule, spacing_minutes: Number(e.target.value) })} /></label>
            <label className="space-y-0.5"><span>Stagger between shops (min)</span>
              <input type="number" min={0} className="field py-1" value={schedule.stagger_minutes}
                onChange={(e) => setSchedule({ ...schedule, stagger_minutes: Number(e.target.value) })} /></label>
          </fieldset>
          <button type="button" className="btn-secondary" disabled={busy || assignments.length === 0}
            onClick={() => run(() => api.planDistribution(batchId, assignments, schedule), setPlan)}>
            Preview the schedule
          </button>
        </div>
      )}

      {plan && (
        <div key="plan" className="space-y-3 text-xs">
          <p translate="no" className="text-sm text-slate-800">
            {`${plan.drafts} draft${plan.drafts === 1 ? "" : "s"} · finishes on ${plan.finishes_on ? day(plan.finishes_on) : "—"} · each ≈ ${plan.requests_per_draft + plan.requests_per_publish} Etsy requests`}
          </p>
          {plan.notes.map((n) => <p key={n} className="text-amber-800">{n}</p>)}
          <div>
            <p className="font-medium text-slate-600">Etsy requests per day (UTC days), against what your account may spend</p>
            <ul className="mt-1 space-y-1">
              {plan.budget.map((b) => (
                <li key={b.date} className="flex items-center gap-2">
                  <span translate="no" className="w-24 shrink-0 tabular-nums">{day(b.date)}</span>
                  <span className="h-2 flex-1 overflow-hidden rounded bg-slate-100" aria-hidden>
                    <span className="block h-full bg-brand-500" style={{ width: `${Math.min(100, (100 * b.requests) / Math.max(1, b.capacity))}%` }} />
                  </span>
                  <span translate="no" className="w-28 shrink-0 text-right tabular-nums">{`${b.requests.toLocaleString()} of ${b.capacity.toLocaleString()}`}</span>
                </li>
              ))}
            </ul>
            <p translate="no" className="mt-1 text-slate-500">{`Your daily ceiling is ${plan.ceiling.toLocaleString()}; a day that is full moves to the next.`}</p>
          </div>
          <div className="space-y-2">
            {plan.shops.map((s) => (
              <details key={s.shop_id} className="rounded border border-slate-200 p-2">
                <summary className="tap cursor-pointer">
                  <span translate="no" className="font-medium">{s.shop_name}</span>
                  <span translate="no" className="text-slate-500">{` · ${s.group_name} · ${s.listings} listing${s.listings === 1 ? "" : "s"}`}</span>
                </summary>
                <ul className="mt-1 space-y-1">
                  {s.days.map((d) => (
                    <li key={d.date}>
                      <p translate="no" className="font-medium text-slate-600">{day(d.date)}</p>
                      <ul className="ml-3">
                        {d.slots.map((x) => (
                          <li key={`${x.content_id}-${x.time}`} className="truncate" translate="no">{`${x.time} ${x.zone} · ${x.title}`}</li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              </details>
            ))}
          </div>
          {plan.skipped.length > 0 && (
            <p key="skipped" translate="no" className="text-slate-500">
              {`Left out: ${plan.skipped.map((x) => `${x.shop} (${x.reason})`).join("; ")}`}
            </p>
          )}
          <button type="button" className="btn-primary" disabled={busy || plan.drafts === 0}
            onClick={() => run(() => api.confirmDistribution(batchId, assignments, schedule), (r) => {
              setDone(`Scheduled: ${r.plan.drafts} drafts, finishing ${r.plan.finishes_on ? day(r.plan.finishes_on) : "—"}.`);
              setPreview(null);
              setPlan(null);
              onDone();
            })}>
            Confirm the schedule
          </button>
          <p className="text-slate-500">
            Each draft is created about two hours before its time and goes live at its time, if the listing is still
            approved and passes the compliance check then.
          </p>
        </div>
      )}
      {done && (
        <p key="done" role="status" className="text-sm text-emerald-800">
          <span translate="no">{done}</span>{" "}
          <Link href="/scheduled" className="text-brand-700 underline">See scheduled</Link>
        </p>
      )}
      {error && <p key="error" className="text-sm text-rose-700">{error}</p>}
    </section>
  );
}
