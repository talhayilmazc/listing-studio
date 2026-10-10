"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { SkuResult } from "@/lib/types";
import { SKU_MAX, skuProblem } from "@/lib/skus";

/**
 * A listing's SKU, prefilled from the file or folder name. Checked against Etsy's
 * rules as it is typed; another listing in the same shop with it is a warning.
 * Once drafts exist on Etsy, a changed SKU shows "Update SKU on Etsy" with its
 * request cost per shop; nothing is sent until the seller presses it.
 */
export function SkuEditor({
  value,
  save,
  contentId,
  compact = false,
  onSaved,
}: {
  value: string | null;
  /** Saves the SKU (group card or review card endpoint). */
  save: (sku: string) => Promise<SkuResult>;
  /** The listing written for it, when there is one (for updating drafts on Etsy). */
  contentId?: string | null;
  compact?: boolean;
  onSaved?: (result: SkuResult) => void;
}) {
  const [sku, setSku] = useState(value ?? "");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SkuResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  // Drafts on Etsy whose SKU differs from this one (asked, never sent, until "Update it").
  const [drafts, setDrafts] = useState<SkuResult["etsy"]>([]);
  useEffect(() => {
    setSku(value ?? "");
    // Changed elsewhere (e.g. "Set SKU for selected"): the last save's notes no longer apply.
    setResult((r) => (r && r.sku !== value ? null : r));
  }, [value]);
  useEffect(() => {
    if (!contentId) return;
    let live = true;
    api
      .updateSkuOnEtsy(contentId, false)
      .then((res) => live && setDrafts(res.shops))
      .catch(() => live && setDrafts([]));
    return () => {
      live = false;
    };
  }, [contentId, value]);
  const problem = skuProblem(sku);

  async function submit() {
    if (problem) return;
    setBusy(true);
    setError(null);
    setSent(null);
    try {
      const res = await save(sku.trim());
      setResult(res);
      setDrafts(res.etsy);
      setEditing(false);
      onSaved?.(res);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  async function updateOnEtsy() {
    if (!contentId) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.updateSkuOnEtsy(contentId, true);
      setAsking(false);
      setSent(
        res.queued
          ? `Updating the SKU on Etsy in ${res.shops.map((s) => s.shop_name ?? "a shop").join(", ")}.`
          : "Nothing to update on Etsy.",
      );
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  const etsy = drafts;
  const cost = etsy.reduce((n, s) => n + s.requests, 0);
  return (
    <div className={compact ? "inline-flex flex-wrap items-center gap-2" : "space-y-1.5"} data-testid="sku-editor">
      {editing ? (
        <form
          key="form"
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <input
            className="field w-40 py-1 font-mono text-sm"
            value={sku}
            onChange={(e) => setSku(e.target.value)}
            aria-label="SKU"
            maxLength={SKU_MAX + 8}
            autoFocus
            aria-invalid={problem ? "true" : undefined}
          />
          <button type="submit" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy || !!problem}>
            Save SKU
          </button>
          <button type="button" className="tap text-xs text-slate-500 underline" onClick={() => { setEditing(false); setSku(value ?? ""); }}>
            Cancel
          </button>
          {problem && (
            <span key="problem" role="alert" className="w-full text-xs text-rose-700">
              {problem}
            </span>
          )}
        </form>
      ) : (
        <span key="view" className="inline-flex items-center gap-2 text-xs text-slate-600">
          <span className="font-mono" translate="no">{value ? `SKU ${value}` : "no SKU"}</span>
          <button type="button" className="tap text-brand-700 underline" onClick={() => setEditing(true)} disabled={busy}>
            Edit SKU
          </button>
        </span>
      )}
      {(result?.warnings ?? []).map((w, i) => (
        <p key={`w${i}`} role="status" className="text-xs text-amber-800">
          {w}
        </p>
      ))}
      {contentId && etsy.length > 0 && !sent && (
        <div key="etsy" className="w-full rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
          <p>
            <span>Its drafts on Etsy still have the old SKU: </span>
            <span translate="no">
              {etsy.map((s) => `${s.shop_name ?? "shop"} (${s.current_sku || "no SKU"}, ${s.requests} requests)`).join("; ")}
            </span>
            <span>.</span>
          </p>
          {!asking ? (
            <button key="ask" type="button" className="btn-secondary mt-2 px-3 py-1.5 text-xs" onClick={() => setAsking(true)} disabled={busy}>
              <span>Update SKU on Etsy</span>
              <span translate="no">{` (${cost} requests)`}</span>
            </button>
          ) : (
            <div key="confirm" role="alertdialog" aria-label="Update SKU on Etsy" className="mt-2 space-y-2">
              <p>
                <span>Only the SKU on each draft&apos;s inventory changes; prices, quantities and variations stay. It uses about </span>
                <span translate="no">{cost}</span>
                <span> Etsy requests from your daily limit.</span>
              </p>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn-primary px-3 py-1.5 text-xs" onClick={updateOnEtsy} disabled={busy}>
                  Update it
                </button>
                <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setAsking(false)}>
                  Not now
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      {sent && (
        <p key="sent" role="status" translate="no" className="text-xs text-brand-800">
          {sent}
        </p>
      )}
      {error && (
        <p key="error" role="alert" className="text-xs text-rose-700">
          {error}
        </p>
      )}
    </div>
  );
}
