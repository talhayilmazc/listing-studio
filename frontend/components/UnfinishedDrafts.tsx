"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { UnfinishedDraft } from "@/lib/types";
import { ShopSetupDialog, type SetupItem } from "./ShopSetupDialog";
import {
  DELETE_NOTE,
  deleteHeading,
  deleteReason,
  finishLine,
  fixHeading,
  fixReason,
  replacedIds,
  sections,
  unfinishedHeading,
} from "@/lib/unfinished";

/**
 * Drafts created on Etsy that need something (backend api/unfinished.py), one per design
 * and shop, in three parts by the server's category:
 *
 * - **Finish these**: "Finish these drafts" (or "Finish draft" on one) runs the same draft
 *   job again, which carries on with the listing Etsy already has; it never makes a
 *   second listing of a design already in the shop, and nothing is deleted on Etsy.
 * - **Fix these**: the personalization question Etsy would refuse (change it on the
 *   listing's card), or a setting the shop lacks ("Set up in <shop>").
 * - **Delete these on Etsy**: a second draft of a design that has another draft in the
 *   shop (matched across batches too), or one whose batch was deleted. The seller deletes
 *   them in Shop Manager (the app never deletes anything on Etsy), then takes each off the list.
 *
 * Shows nothing when there is nothing to do.
 */
export function UnfinishedDrafts({ batchId }: { batchId?: string }) {
  const [items, setItems] = useState<UnfinishedDraft[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await api.unfinishedDrafts(batchId));
    } catch {
      setItems([]); // this panel is an aid: it never blocks the page
    }
  }, [batchId]);
  useEffect(() => {
    load();
  }, [load]);

  // While some are being finished (or set up), look again.
  const moving = (items ?? []).some((i) => i.finishing);
  useEffect(() => {
    if (!moving) return;
    const t = setTimeout(load, 5000);
    return () => clearTimeout(t);
  }, [moving, items, load]);

  // Every hook before the early return below (a hook after it crashed the page once a seller had
  // unfinished drafts: React saw more hooks than on the render before).
  const [setupItems, setSetupItems] = useState<SetupItem[] | null>(null);
  if (!items || items.length === 0) return null;
  const { finish: open, fix, remove } = sections(items);
  const ready = open.filter((i) => i.can_finish);

  async function finish(only?: UnfinishedDraft) {
    setBusy(true);
    setError(null);
    try {
      const res = await api.finishDrafts({ record_ids: (only ? [only] : ready).map((i) => i.record_id) });
      setNote(finishLine(res.started, res.skipped.length));
      await load();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function dismiss(item: UnfinishedDraft) {
    if (!window.confirm(`Take listing ${item.etsy_listing_id} off this list? Do this once you have deleted it in Shop Manager; the app does not delete it on Etsy.`)) return;
    setError(null);
    try {
      await api.dismissReplacedDraft(item.record_id);
      await load();
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  async function copyIds() {
    try {
      await navigator.clipboard.writeText(replacedIds(remove));
      setNote("Listing ids copied.");
    } catch {
      setNote(replacedIds(remove));
    }
  }

  // The shop's setup opens here, in the same dialog the Profiles page uses.
  function setUp(item: UnfinishedDraft) {
    if (!item.profile_id) return;
    setError(null);
    setSetupItems([{ profileId: item.profile_id, profileName: item.profile_name ?? null, shopIds: [item.connection_id] }]);
  }

  const head = (i: UnfinishedDraft) => (
    <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
      <span className="min-w-0 truncate font-medium text-slate-800" title={i.title}>{i.title || "Untitled draft"}</span>
      <span className="shrink-0 text-xs text-slate-500" translate="no">
        <span>{i.shop_name}</span><span>{" · listing "}</span>
        <a className="tap text-brand-700 underline" href={i.edit_url} target="_blank" rel="noreferrer">{`${i.etsy_listing_id}`}</a>
      </span>
    </div>
  );

  return (
    <section id="unfinished-drafts" className="card space-y-3 border-amber-200 bg-amber-50/60 p-4" data-testid="unfinished-drafts">
      {setupItems && (
        <ShopSetupDialog key="setup-dialog" items={setupItems} onClose={() => {
          setSetupItems(null);
          load();
        }} />
      )}
      {open.length > 0 && (
      <div key="open" className="space-y-3">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-amber-900" translate="no">{unfinishedHeading(open.length)}</h2>
          <p className="mt-1 text-xs text-amber-900/80">
            They are on Etsy as drafts with only part of their settings. Finishing completes the same drafts: nothing new
            is created and nothing is deleted on Etsy.
          </p>
        </div>
        {ready.length > 0 && (
          <button key="finish" type="button" className="btn-primary min-h-11" onClick={() => finish()} disabled={busy} data-testid="finish-drafts">
            <span translate="no">{busy ? "Starting…" : `Finish these drafts (${ready.length})`}</span>
          </button>
        )}
      </div>
      <ul className="space-y-2">
        {open.map((i) => (
          <li key={i.record_id} className="rounded-md border border-amber-200 bg-white p-2.5 text-sm" data-testid="unfinished-draft">
            {head(i)}
            {i.step && <p key="step" className="mt-1 text-xs text-slate-600" translate="no">{`Stopped while ${i.step}.`}</p>}
            {i.error && <p key="error" className="mt-0.5 text-xs text-slate-700">{i.error}</p>}
            {i.finishing && <p key="finishing" className="mt-1 text-xs text-slate-500" translate="no">Being finished now…</p>}
            {i.can_finish && (
              <button key="finish-one" type="button" className="btn-secondary mt-2 min-h-11 px-3 text-xs" onClick={() => finish(i)} disabled={busy}
                data-testid="finish-draft">
                Finish draft
              </button>
            )}
          </li>
        ))}
      </ul>
      </div>
      )}
      {fix.length > 0 && (
        <div key="fix" className={`space-y-2 ${open.length ? "border-t border-amber-200 pt-3" : ""}`} data-testid="fix-drafts">
          <h3 className="text-sm font-semibold text-amber-900" translate="no">{fixHeading(fix.length)}</h3>
          <ul className="space-y-2">
            {fix.map((i) => (
              <li key={i.record_id} className="rounded-md border border-amber-200 bg-white p-2.5 text-sm" data-testid="fix-draft">
                {head(i)}
                <p className="mt-1 text-xs text-amber-900">
                  <span>{fixReason(i)}</span>
                  {i.category === "fix_shop" && i.setup && i.profile_id && (
                    <button key="setup" type="button" className="tap ml-1 text-brand-700 underline" onClick={() => setUp(i)} data-testid="unfinished-setup">
                      <span>Set up in </span><span translate="no">{i.shop_name}</span>
                    </button>
                  )}
                </p>
                {i.error && <p key="error" className="mt-0.5 text-xs text-slate-600">{i.error}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {remove.length > 0 && (
        <div key="replaced" className={`space-y-2 ${open.length || fix.length ? "border-t border-amber-200 pt-3" : ""}`} data-testid="replaced-drafts">
          <div className="flex flex-wrap items-start gap-2">
            <div className="min-w-0 flex-1">
              <h3 className="text-sm font-semibold text-slate-800" translate="no">{deleteHeading(remove.length)}</h3>
              <p className="mt-1 text-xs text-slate-600">{DELETE_NOTE}</p>
            </div>
            <button type="button" className="btn-secondary min-h-11 px-3 text-xs" onClick={copyIds} data-testid="replaced-copy">
              Copy listing ids
            </button>
          </div>
          <ul className="space-y-1.5">
            {remove.map((i) => (
              <li key={i.record_id} className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-white p-2 text-xs"
                data-testid="replaced-draft">
                <span className="min-w-0 flex-1" translate="no">
                  <span className="font-medium text-slate-800">{i.shop_name}</span>
                  <span>{" · listing "}</span>
                  <a className="tap text-brand-700 underline" href={i.edit_url} target="_blank" rel="noreferrer">{`${i.etsy_listing_id}`}</a>
                  <span>{" · "}</span>
                  {i.replaced_edit_url ? (
                    <a key="newer" className="tap text-brand-700 underline" href={i.replaced_edit_url} target="_blank" rel="noreferrer">{deleteReason(i)}</a>
                  ) : (
                    <span key="newer">{deleteReason(i)}</span>
                  )}
                </span>
                {i.can_dismiss && (
                  <button key="dismiss" type="button" className="btn-secondary min-h-11 px-3 text-xs" onClick={() => dismiss(i)} data-testid="replaced-dismiss">
                    I deleted it on Etsy
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {note && <p key="note" role="status" className="text-sm text-slate-700">{note}</p>}
      {error && <p key="error" role="alert" className="text-sm text-rose-700">{error}</p>}
    </section>
  );
}
