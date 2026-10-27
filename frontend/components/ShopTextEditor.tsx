"use client";

import { useEffect, useRef, useState } from "react";
import type { ShopText } from "@/lib/anotherShop";
import { draftErrors, MAX_TITLE } from "@/lib/etsyText";
import { TagEditor } from "./TagEditor";

/**
 * One listing's title, tags and description for one shop, on its row in the shop workspace:
 * the review card's editors and checks (Etsy's limits as it is typed, then the server's errors,
 * trademark filter included, and its notes, which never stop it). Saved shortly after typing
 * stops. "Keep as it is" goes back to the listing's text; "Rewrite for <shop>" says the
 * allowance's rewrite rule before it runs.
 */
export function ShopTextEditor({
  text,
  own,
  errors,
  warnings,
  shopName,
  label,
  rewriteRule,
  freeRewritesLeft,
  onSave,
  onKeep,
  onRewrite,
  busy,
}: {
  text: ShopText;
  /** The fields that are this shop's own (the rest follow the listing). */
  own: string[];
  errors: string[];
  warnings: string[];
  shopName: string;
  label: string;
  rewriteRule: string;
  freeRewritesLeft: number;
  onSave: (text: ShopText) => Promise<void>;
  onKeep: () => Promise<void>;
  onRewrite: () => Promise<void>;
  busy: boolean;
}) {
  const [title, setTitle] = useState(text.title);
  const [tags, setTags] = useState(text.tags);
  const [description, setDescription] = useState(text.description);
  const [dirty, setDirty] = useState(false);
  const [asking, setAsking] = useState(false);
  const saved = useRef(text);
  // A save or a rewrite elsewhere changed the text: show it (not while the seller is typing).
  const incoming = JSON.stringify(text);
  useEffect(() => {
    if (dirty) return;
    setTitle(text.title);
    setTags(text.tags);
    setDescription(text.description);
    saved.current = text;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incoming]);
  useEffect(() => {
    if (!dirty) return;
    const t = setTimeout(async () => {
      await onSave({ title, tags, description });
      setDirty(false);
    }, 800);
    return () => clearTimeout(t);
  }, [title, tags, description, dirty, onSave]);

  const local = draftErrors(title, tags, description);
  const shown = [...new Set([...local, ...(dirty ? [] : errors)])];
  const follows = (name: string) => (own.includes(name) ? null : <span key="f" className="text-slate-500"> · follows the listing</span>);

  return (
    <div className="space-y-2" data-testid="shop-text-editor">
      <label className="block text-xs text-slate-600">
        <span className="flex justify-between gap-2">
          <span><span>Title</span>{follows("title")}</span>
          <span translate="no" className={title.length > MAX_TITLE ? "text-rose-700" : ""}>{`${title.length} / ${MAX_TITLE}`}</span>
        </span>
        <input className="field mt-1 w-full text-sm" value={title} aria-label={`Title in ${shopName} for ${label}`}
          onChange={(e) => { setTitle(e.target.value); setDirty(true); }} />
      </label>
      <div className="text-xs text-slate-600">
        {!own.includes("tags") && <p key="tf" className="mb-1 text-slate-500">Tags follow the listing until changed.</p>}
        <TagEditor tags={tags} onChange={(next) => { setTags(next); setDirty(true); }} />
      </div>
      <details className="text-xs text-slate-600">
        <summary className="tap cursor-pointer"><span>Description</span>{follows("description")}</summary>
        <textarea className="field mt-1 h-32 w-full text-sm" value={description} aria-label={`Description in ${shopName} for ${label}`}
          onChange={(e) => { setDescription(e.target.value); setDirty(true); }} />
      </details>
      {shown.length > 0 && (
        <ul key="errors" role="alert" className="list-disc space-y-0.5 pl-5 text-xs text-rose-700" data-testid="shop-text-errors">
          {shown.map((e) => <li key={e}>{e}</li>)}
        </ul>
      )}
      {!dirty && warnings.length > 0 && (
        <details key="notes" className="text-xs text-slate-600">
          <summary className="tap cursor-pointer"><span>Notes (they do not stop it): </span><span translate="no">{warnings.length}</span></summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </details>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {own.length > 0 && (
          <button key="keep" type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy}
            onClick={async () => { setDirty(false); await onKeep(); }}>
            Keep as it is
          </button>
        )}
        <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy} onClick={() => setAsking(true)}>
          <span translate="no">{`Rewrite for ${shopName}`}</span>
        </button>
        <span className="text-xs text-slate-500" translate="no">{dirty ? "Saving…" : own.length ? `Own text for ${shopName}` : ""}</span>
      </div>
      {asking && (
        <div key="ask" role="alertdialog" aria-label={`Rewrite for ${shopName}`} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <p><span>{rewriteRule}</span></p>
          <p className="mt-1" translate="no"><span>{freeRewritesLeft > 0 ? `${freeRewritesLeft} free left for this design.` : "No free rewrite left: this one counts as a listing."}</span></p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button type="button" className="btn-primary px-3 py-1.5 text-xs" disabled={busy}
              onClick={async () => { setAsking(false); setDirty(false); await onRewrite(); }}>
              Rewrite it
            </button>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setAsking(false)}>Not now</button>
          </div>
        </div>
      )}
    </div>
  );
}
