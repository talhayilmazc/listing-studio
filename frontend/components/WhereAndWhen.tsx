"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { GroupSchedule, PublishJob, PublishSkipped, SendPreview, SendResult, SendState, SendWhen } from "@/lib/types";
import {
  SETTINGS_LINE,
  WHEN_LABELS,
  cellLine,
  rememberWhen,
  rememberedWhen,
  scheduleLine,
  startingToday,
} from "@/lib/whereWhen";
import { DestinationPicker } from "./DestinationPicker";
import { Txt } from "./Txt";
import { Help } from "./Help";

/** Today in the seller's time zone, "YYYY-MM-DD". */
function today(timeZone: string): string {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}

/**
 * "Where and when" (B4): one panel at the top of the review page, three lines and one
 * button. Send to: shop groups (or single shops); every approved listing goes to every one
 * of their shops, each shop once (the owner, 2026-10-22: no splitting). When: now as drafts,
 * publish now, or spread out. The server works out the sentence, the button's label and why
 * it cannot be pressed (api/send.py); nothing is counted here.
 */
export function WhereAndWhen({
  batchId,
  timeZone,
  refreshKey,
  disabled,
  onState,
  onPreview,
  onDrafts,
  onDone,
  primary = true,
}: {
  /** The page's one primary button (B9): false while the page's next step is elsewhere (Approve all). */
  primary?: boolean;
  batchId: string;
  timeZone: string;
  /** Changes when listings are approved or drafted: the preview is asked again. */
  refreshKey: string;
  disabled: boolean;
  onState: (state: SendState) => void;
  onPreview: (preview: SendPreview | null) => void;
  /** "Now as drafts": the jobs to follow, as "Create drafts" did. */
  onDrafts: (call: () => Promise<{ jobs: PublishJob[]; skipped: PublishSkipped[] }>) => Promise<void>;
  /** Publish now or spread out was confirmed: reload the listings. */
  onDone: () => void;
}) {
  const [state, setState] = useState<SendState | null>(null);
  const [remembered] = useState(rememberedWhen);
  const [when, setWhen] = useState<SendWhen>(remembered.when);
  const [schedule, setSchedule] = useState<GroupSchedule>(() => startingToday(today(timeZone), remembered.schedule));
  const [editing, setEditing] = useState(false);
  const [preview, setPreview] = useState<SendPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SendResult | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    api.sendState(batchId).then((s) => {
      setState(s);
      onState(s);
    }).catch((e) => setError(String(e.message ?? e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId]);

  const destKey = state ? JSON.stringify(state.send_to) : "";
  useEffect(() => {
    if (!state) return;
    let cancelled = false;
    const t = setTimeout(() => {
      api.sendPreview(batchId, when, when === "spread" ? schedule : null)
        .then((p) => {
          if (cancelled) return;
          setPreview(p);
          onPreview(p);
        })
        .catch((e) => !cancelled && setError(String(e.message ?? e)));
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId, when, JSON.stringify(schedule), destKey, refreshKey, tick]);

  async function choose(next: SendState["send_to"]) {
    setError(null);
    try {
      const s = await api.setSendTo(batchId, next);
      setState(s);
      onState(s);
    } catch (e: any) {
      setError(e.message ?? String(e));
    }
  }

  async function allShops() {
    setError(null);
    try {
      const s = await api.sendToAllShops(batchId);
      setState(s);
      onState(s);
    } catch (e: any) {
      setError(e.message ?? String(e));
    }
  }

  async function go() {
    if (!preview || preview.disabled) return;
    setBusy(true);
    setError(null);
    setResult(null);
    rememberWhen(when, when === "spread" ? schedule : null);
    try {
      if (when === "drafts") {
        await onDrafts(async () => {
          const res = await api.send(batchId, "drafts");
          return { jobs: res.jobs, skipped: res.skipped };
        });
      } else {
        setResult(await api.send(batchId, when, when === "spread" ? schedule : null));
        onDone();
      }
      setTick((n) => n + 1);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  const oneShop = state && state.shops.length === 1 ? state.shops[0] : null;
  const set = (patch: Partial<GroupSchedule>) => setSchedule((s) => ({ ...s, ...patch }));

  return (
    <section className="card space-y-3 p-4" data-testid="where-and-when" aria-labelledby="where-and-when">
      <h2 id="where-and-when" className="text-sm font-semibold text-slate-900">
        <span>Where and when</span>
        <Help label="Where and when">
          Every approved listing goes to every shop you choose; several groups give all their shops, each once (6 listings
          and 2 shops are 12 drafts). A listing already in a shop, or already scheduled there, is not sent again. One
          listing can go elsewhere with &ldquo;change&rdquo; next to it.
        </Help>
      </h2>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="label w-16 shrink-0">Send to</span>
        {state === null && <span key="loading" className="text-slate-500">Loading…</span>}
        {oneShop && <span key="one" translate="no" className="font-medium text-slate-900" data-testid="send-to-one">{oneShop.name}</span>}
        {state && !oneShop && (
          <DestinationPicker key="picker" state={state} value={state.send_to} label={state.label} onChange={choose} disabled={busy} />
        )}
        {state?.offer_all_shops && (
          <button key="all" type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={allShops} disabled={busy}
            data-testid="all-shops">
            Make a group of all my shops
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="label w-16 shrink-0">When</span>
        <div role="radiogroup" aria-label="When" className="flex flex-wrap gap-1">
          {(Object.keys(WHEN_LABELS) as SendWhen[]).map((w) => (
            <label key={w} className={"flex min-h-[44px] cursor-pointer items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm sm:min-h-0 " +
              (when === w ? "border-brand-600 bg-brand-50 font-medium text-slate-900" : "border-slate-300 text-slate-700")}>
              <input type="radio" name={`when-${batchId}`} className="sr-only" checked={when === w} onChange={() => setWhen(w)} />
              <span>{WHEN_LABELS[w]}</span>
            </label>
          ))}
        </div>
      </div>
      {when === "spread" && (
        <div key="spread" className="space-y-2 pl-0 text-xs text-slate-700 sm:pl-[4.5rem]">
          <p>
            <span translate="no" data-testid="schedule-line">{scheduleLine(schedule)}</span>
            <span> · </span>
            <button type="button" className="tap text-brand-700 underline" onClick={() => setEditing((v) => !v)} aria-expanded={editing}>
              {editing ? "done" : "change"}
            </button>
          </p>
          {editing && (
            <div key="form" className="grid max-w-xl grid-cols-2 gap-2 sm:grid-cols-3">
              <label className="space-y-0.5"><span className="label block">Start</span>
                <input type="date" className="field" value={schedule.start_date} onChange={(e) => set({ start_date: e.target.value })} /></label>
              <label className="space-y-0.5"><span className="label block">A day per shop</span>
                <input type="number" min={1} max={50} className="field" value={schedule.per_shop_per_day}
                  onChange={(e) => set({ per_shop_per_day: Math.max(1, Number(e.target.value) || 1) })} /></label>
              <label className="space-y-0.5"><span className="label block">Minutes apart</span>
                <input type="number" min={1} className="field" value={schedule.spacing_minutes}
                  onChange={(e) => set({ spacing_minutes: Math.max(1, Number(e.target.value) || 1) })} /></label>
              <label className="space-y-0.5"><span className="label block">From</span>
                <input type="time" className="field" value={schedule.window_start} onChange={(e) => set({ window_start: e.target.value })} /></label>
              <label className="space-y-0.5"><span className="label block">Until</span>
                <input type="time" className="field" value={schedule.window_end} onChange={(e) => set({ window_end: e.target.value })} /></label>
              <label className="space-y-0.5"><span className="label block">Shops apart (min)</span>
                <input type="number" min={1} className="field" value={schedule.stagger_minutes}
                  onChange={(e) => set({ stagger_minutes: Math.max(1, Number(e.target.value) || 1) })} /></label>
            </div>
          )}
        </div>
      )}

      <p className="text-xs text-slate-500">{SETTINGS_LINE}</p>

      {preview && (
        <div key="summary" className="space-y-1 text-sm">
          <p translate="no" className="font-medium text-slate-900" data-testid="send-sentence"><span>{preview.sentence}</span></p>
          {preview.notes.map((n) => <p key={n} className="text-xs text-slate-600">{n}</p>)}
          {(preview.how_made ?? []).length > 0 && (
            // How it's made: Etsy's API cannot set it; ticked in Etsy after publishing. Never blocks.
            <div key="how-made" className="text-xs text-slate-700" data-testid="send-how-made">
              {(preview.how_made ?? []).map((line) => <p key={line}>{line}</p>)}
              <Link href="/batches/finish" className="tap text-brand-700 underline">Finish in Etsy</Link>
            </div>
          )}
          {preview.left_out.length > 0 && (
            <p key="left" className="text-xs text-slate-600">
              <span translate="no">{`${preview.left_out.length} not sent: `}</span>
              <span>{Array.from(new Set(preview.left_out.map((x) => x.reason))).join("; ")}</span>
            </p>
          )}
          {preview.rows.length > 0 && (
            <details key="list" className="text-xs">
              <summary className="tap cursor-pointer text-brand-700 underline">Show each listing</summary>
              <ul className="mt-2 max-h-80 space-y-2 overflow-y-auto" data-testid="send-list">
                {preview.rows.map((r) => (
                  <li key={r.content_id} className="rounded border border-slate-200 p-2">
                    <p className="flex flex-wrap items-baseline gap-x-2">
                      <span className="min-w-0 truncate font-medium text-slate-900">{r.title || r.sku || "Untitled"}</span>
                      <span translate="no" className="text-slate-600"><span>{`→ ${r.label}`}</span></span>
                      {state && !oneShop && (
                        <span key="change" className="text-slate-600">
                          <span>· </span>
                          <ListingChange batchId={batchId} state={state} row={r} onDone={() => setTick((n) => n + 1)} />
                        </span>
                      )}
                    </p>
                    <ul className="mt-1 space-y-0.5 text-slate-600">
                      {r.cells.map((c) => (
                        <li key={c.shop_id} translate="no" className={c.state === "cannot" ? "text-amber-800" : ""}>{cellLine(c)}</li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className={primary ? "btn-primary" : "btn-secondary"} onClick={go} data-testid="send-button"
          disabled={disabled || busy || !preview || !!preview.disabled}>
          <span translate="no">{busy ? "Sending…" : preview?.button ?? WHEN_LABELS[when]}</span>
        </button>
        {preview?.disabled && <span key="why" translate="no" className="text-xs text-slate-600" data-testid="send-disabled"><Txt>{preview.disabled}</Txt></span>}
      </div>
      {result && (
        <p key="result" role="status" translate="no" className="text-sm text-emerald-800">
          <span>{result.message}</span><span> </span>
          <a href="/scheduled" className="tap text-brand-700 underline">See them on Scheduled</a>
        </p>
      )}
      {error && <p key="error" className="text-sm text-rose-700">{error}</p>}
    </section>
  );
}

/** "→ GROUP 1 · change" for one listing: its own destination, or the batch's again. */
export function ListingChange({
  batchId,
  state,
  row,
  onDone,
}: {
  batchId: string;
  state: SendState;
  row: { content_id: string; custom: boolean; send_to: SendState["send_to"]; label: string };
  onDone: () => void;
}) {
  return (
    <DestinationPicker
      compact
      title="Send this listing to"
      state={state}
      value={row.custom ? row.send_to : null}
      label={row.label}
      follow={state.label}
      onChange={async (next) => {
        await api.setSendTo(batchId, next, [row.content_id]);
        onDone();
      }}
    />
  );
}
