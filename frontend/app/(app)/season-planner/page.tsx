"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Help } from "@/components/Help";
import { StatusChip } from "@/components/StatusChip";
import {
  chosenShops, dayTitle, dayTone, firstChoice, monthHeads, planBody, toggled, windowLine, windowState, type PlanPreview, type PlannerChoice, type PlannerState, type Season,
} from "@/lib/seasonPlan";
import { shortDay } from "@/lib/whereWhen";

import { Txt } from "@/components/Txt";
const TONES = { empty: "bg-slate-50 text-slate-400", taken: "bg-slate-200 text-slate-700", some: "bg-brand-100 text-brand-900", full: "bg-brand-700 text-white" };

/**
 * A holiday, batches and shops in; a spread of go-lives across the window out, shop by shop and day by day. Limits,
 * slots already taken and what does not fit are all on screen before the one button saves it. Saved go-lives are
 * ordinary slots on the Scheduled page, and only approved listings are planned.
 */
export default function SeasonPlannerPage() {
  const [state, setState] = useState<PlannerState | null>(null);
  const [choice, setChoice] = useState<PlannerChoice | null>(null);
  const [preview, setPreview] = useState<PlanPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const latest = useRef(0);

  useEffect(() => {
    api.seasonPlanner().then((out) => { setState(out); setChoice(firstChoice(out)); }).catch((e) => setError(e instanceof Error ? e.message : "This page could not be read."));
  }, []);

  useEffect(() => {
    if (!choice || !choice.batch_ids.length) { setPreview(null); return; }
    const call = ++latest.current;
    const timer = setTimeout(() => {
      api.seasonPreview(planBody(choice)).then((out) => { if (call === latest.current) { setPreview(out); setError(null); } })
        .catch((e) => { if (call === latest.current) setError(e instanceof Error ? e.message : "The preview could not be made."); });
    }, 350);
    return () => clearTimeout(timer);
  }, [choice]);

  const change = (next: Partial<PlannerChoice>) => { setSaved(null); setChoice((was) => (was ? { ...was, ...next } : was)); };

  const save = async () => {
    if (!choice || !preview?.fingerprint) return;
    setBusy(true);
    setError(null);
    try {
      const out = await api.seasonSave({ ...planBody(choice), fingerprint: preview.fingerprint });
      setSaved(out.message);
      setPreview(await api.seasonPreview(planBody(choice)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "This could not be saved.");
      setPreview(await api.seasonPreview(planBody(choice)).catch(() => null));
    } finally {
      setBusy(false);
    }
  };

  const holiday = state?.seasons.find((s) => s.key === choice?.holiday);
  const reached = state && choice ? chosenShops(state, choice) : new Set<string>();

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-slate-600">Spread approved listings across a holiday&apos;s launch window, shop by shop. Nothing is saved until you press the button.</p>
        <Help label="About the seasonal planner">
          <span>Saved go-lives appear on the Scheduled page, where you can move them. Going live checks the approval again. A shop&apos;s daily limit and the minutes between its listings hold across every schedule.</span>
        </Help>
      </div>

      {error && <p key="p-72-6" role="alert" className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800"><span translate="no">{error}</span></p>}

      {state && choice && (
        <>
          <section className="card space-y-3 p-4" data-testid="season-calendar">
            <h2 className="font-semibold text-slate-900">Holiday</h2>
            <p className="text-sm text-slate-700" data-testid="season-about"><span translate="no">{state.about}</span></p>
            <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {state.seasons.map((season) => (
                <SeasonCard key={season.key} season={season} today={state.today} chosen={season.key === choice.holiday}
                  onChoose={() => change({ holiday: season.key, first_day: null, last_day: null })} />
              ))}
            </div>
          </section>

          <section className="card space-y-2 p-4">
            <h2 className="font-semibold text-slate-900">Batches</h2>
            {state.batches.length === 0 && <p key="p-89-12" className="text-sm text-slate-700">No listings are written yet. Write and approve some first.</p>}
            <ul className="space-y-1">
              {state.batches.map((batch) => (
                <li key={batch.id}>
                  <label className="tap-row flex flex-wrap items-center gap-2 text-sm text-slate-800">
                    <input type="checkbox" checked={choice.batch_ids.includes(batch.id)} disabled={batch.approved === 0}
                      onChange={() => change({ batch_ids: toggled(choice.batch_ids, batch.id) })} />
                    <span className="min-w-0 break-words font-medium" translate="no">{batch.name}</span>
                    <span className="text-xs text-slate-600" translate="no"><span><span>{batch.approved}</span><span> approved</span><Txt>{batch.not_approved ? ` · ${batch.not_approved} not approved yet` : ""}</Txt></span></span>
                  </label>
                </li>
              ))}
            </ul>
            <p className="text-xs text-slate-600">Only approved listings are planned. The others are listed below with why.</p>
          </section>

          <section className="card space-y-2 p-4">
            <h2 className="font-semibold text-slate-900">Shops</h2>
            {state.groups.length > 0 && (
              <ul key="ul-107-12" className="space-y-1">
                {state.groups.map((group) => (
                  <li key={group.id}>
                    <label className="tap-row flex flex-wrap items-center gap-2 text-sm text-slate-800">
                      <input type="checkbox" checked={choice.group_ids.includes(group.id)} onChange={() => change({ group_ids: toggled(choice.group_ids, group.id) })} />
                      <span className="min-w-0 break-words font-medium" translate="no">{group.name}</span>
                      <span className="text-xs text-slate-600" translate="no"><span><span>{group.shop_ids.length}</span> shops</span></span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
            <ul className="grid gap-1 sm:grid-cols-2 xl:grid-cols-3">
              {state.shops.map((shop) => (
                <li key={shop.id}>
                  <label className="tap-row flex items-center gap-2 text-sm text-slate-800">
                    <input type="checkbox" checked={reached.has(shop.id)} disabled={!choice.connection_ids.includes(shop.id) && reached.has(shop.id)}
                      onChange={() => change({ connection_ids: toggled(choice.connection_ids, shop.id) })} />
                    <span className="min-w-0 break-words" translate="no">{shop.name}</span>
                  </label>
                </li>
              ))}
            </ul>
          </section>

          <section className="card space-y-3 p-4">
            <h2 className="font-semibold text-slate-900">When</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <label className="text-sm"><span className="label block">First day</span>
                <input type="date" className="field w-full" min={state.today} value={choice.first_day ?? holiday?.first_left ?? ""} onChange={(e) => change({ first_day: e.target.value || null })} /></label>
              <label className="text-sm"><span className="label block">Last day</span>
                <input type="date" className="field w-full" min={state.today} value={choice.last_day ?? holiday?.closes ?? ""} onChange={(e) => change({ last_day: e.target.value || null })} /></label>
              <label className="text-sm"><span className="label block">Listings a day, per shop</span>
                <input type="number" className="field w-full" min={1} max={50} value={choice.per_shop_per_day} onChange={(e) => change({ per_shop_per_day: Math.min(50, Math.max(1, Number(e.target.value) || 1)) })} /></label>
              <label className="text-sm"><span className="label block">Hours from</span>
                <input type="time" className="field w-full" value={choice.window_start.slice(0, 5)} onChange={(e) => change({ window_start: e.target.value || choice.window_start })} /></label>
              <label className="text-sm"><span className="label block">Hours to</span>
                <input type="time" className="field w-full" value={choice.window_end.slice(0, 5)} onChange={(e) => change({ window_end: e.target.value || choice.window_end })} /></label>
              <label className="text-sm"><span className="label block">Minutes between listings</span>
                <input type="number" className="field w-full" min={1} value={choice.spacing_minutes} onChange={(e) => change({ spacing_minutes: Math.max(1, Number(e.target.value) || 1) })} /></label>
            </div>
            <p className="text-xs text-slate-600" translate="no"><span>Times are in <span>{state.time_zone}</span>. Shops are staggered by <span>{choice.stagger_minutes}</span> minutes, so a design never goes live in two at once.</span></p>
          </section>

          {preview && (
            <section key="section-152-10" className="card space-y-3 p-4" data-testid="season-preview">
              <div>
                <h2 className="font-semibold text-slate-900">Before you save</h2>
                <p className="text-sm font-medium text-slate-800" data-testid="season-sentence"><span translate="no">{preview.sentence}</span></p>
              </div>
              {preview.rows.length > 0 && <Timeline key="timeline-158-14" preview={preview} />}
              {preview.conflicts.length > 0 && (
                <ul key="ul-159-14" className="space-y-1 text-sm text-amber-900" data-testid="season-conflicts">
                  {preview.conflicts.map((line) => <li key={line} className="rounded border border-amber-200 bg-amber-50 p-2"><span translate="no">{line}</span></li>)}
                </ul>
              )}
              {preview.did_not_fit.length > 0 && (
                <details key="details-164-14" className="text-sm text-slate-700" data-testid="season-not-fitting">
                  <summary className="tap cursor-pointer font-medium"><span translate="no"><span><span>{preview.did_not_fit.length}</span> will not fit</span></span></summary>
                  <ul className="mt-1 space-y-1">{preview.did_not_fit.map((m, i) => <li key={i} className="break-words"><span translate="no"><span><span>{m.title}</span> in <span>{m.shop_name}</span>: <span>{m.reason}</span></span></span></li>)}</ul>
                </details>
              )}
              {preview.left_out.length > 0 && (
                <details key="details-170-14" className="text-sm text-slate-700" data-testid="season-left-out">
                  <summary className="tap cursor-pointer font-medium"><span translate="no"><span><span>{preview.left_out.length}</span> left out</span></span></summary>
                  <ul className="mt-1 space-y-1">{preview.left_out.map((m, i) => <li key={i} className="break-words"><span translate="no"><span><span>{m.title || "Untitled"}</span> (<span>{m.batch}</span>): <span>{m.reason}</span></span></span></li>)}</ul>
                </details>
              )}
              {preview.disabled && <p key="p-176-14" className="text-sm font-medium text-slate-800" data-testid="season-disabled"><span translate="no">{preview.disabled}</span></p>}
              {saved && <p key="p-177-14" role="status" className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900"><span translate="no">{saved}</span> <a className="tap underline" href="/scheduled">Open Scheduled</a></p>}
              <button type="button" className="btn-primary" disabled={busy || !!preview.disabled || !preview.fingerprint} onClick={() => void save()} data-testid="season-save">
                <span translate="no">{preview.button}</span>
              </button>
            </section>
          )}
        </>
      )}
    </div>
  );
}

function SeasonCard({ season, today, chosen, onChoose }: { season: Season; today: string; chosen: boolean; onChoose: () => void }) {
  return (
    <button type="button" aria-pressed={chosen} onClick={onChoose} data-testid="season-card"
      className={"rounded-md border p-3 text-left " + (chosen ? "border-brand-700 bg-brand-50" : "border-slate-200 bg-white")}>
      <span className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium text-slate-900">{season.name}</span>
        <span className="text-xs text-slate-600" translate="no">{shortDay(season.on)}</span>
      </span>
      <span className="mt-1 block text-sm text-slate-700" translate="no">{windowLine(season)}</span>
      <span className="mt-1 flex flex-wrap items-center gap-2">
        <StatusChip tone={season.first_left === null ? "neutral" : season.opens > today ? "neutral" : "working"}>{windowState(season, today)}</StatusChip>
        <span className="text-xs text-slate-600">Our advice</span>
      </span>
      <span className="mt-1 block text-xs text-slate-600">{season.why}</span>
    </button>
  );
}

function Timeline({ preview }: { preview: PlanPreview }) {
  const heads = monthHeads(preview.days);
  return (
    <div className="overflow-x-auto" data-testid="season-timeline">
      <table className="border-separate border-spacing-0.5 text-xs">
        <thead>
          <tr>
            <th className="sticky left-0 bg-white" />
            {heads.map((m, i) => <th key={i} colSpan={m.span} className="text-left font-medium text-slate-700" translate="no">{m.label}</th>)}
            <th />
          </tr>
          <tr>
            <th className="sticky left-0 bg-white pr-2 text-left font-medium text-slate-700">Shop</th>
            {preview.days.map((day) => <th key={day} className="w-6 min-w-6 text-center font-normal text-slate-600" translate="no">{Number(day.slice(8))}</th>)}
            <th className="pl-2 text-left font-medium text-slate-700">New</th>
          </tr>
        </thead>
        <tbody>
          {preview.rows.map((row) => (
            <tr key={row.shop_id} data-testid="season-row">
              <th className="sticky left-0 max-w-32 truncate bg-white pr-2 text-left font-medium text-slate-800" title={row.name} translate="no">{row.name}</th>
              {row.days.map((day) => (
                <td key={day.day} title={dayTitle(day, row.name)} className={"h-6 w-6 min-w-6 rounded text-center " + TONES[dayTone(day, row)]} translate="no">
                  {day.new + day.taken || ""}
                </td>
              ))}
              <td className="whitespace-nowrap pl-2 text-slate-800" translate="no"><span><span>{row.total}</span><Txt>{row.limit ? ` · limit ${row.limit} a day` : ""}</Txt></span></td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 flex flex-wrap items-center gap-3 text-xs text-slate-600">
        <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-3 rounded bg-brand-100" /><span>New go-lives</span></span>
        <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-3 rounded bg-slate-200" /><span>Already scheduled</span></span>
        <span className="inline-flex items-center gap-1"><span className="inline-block h-3 w-3 rounded bg-brand-700" /><span>Day full</span></span>
      </p>
    </div>
  );
}
