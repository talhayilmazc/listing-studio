"use client";

import { useState } from "react";
import { addTagsTo, addWordsTo, removeTagsFrom, removeWordsFrom, replaceIn, type ShopText } from "@/lib/anotherShop";
import { draftErrors, MAX_TITLE } from "@/lib/etsyText";
import type { AnotherShopPreview } from "@/lib/types";
import { TagEditor } from "./TagEditor";

export type TextEdit = Partial<ShopText> & { reset?: boolean };
type Row = AnotherShopPreview["rows"][number];

/** A row's text as the dialog holds it: what was typed, else what the server says goes there. */
export function shownText(row: Row, edit: TextEdit | undefined): ShopText {
  return {
    title: edit?.title ?? row.title,
    tags: edit?.tags ?? row.tags,
    description: edit?.description ?? row.description,
  };
}

/**
 * "Text for <shop>" in Publish in another shop: each listing's title, tags and description for
 * that shop only, with the review card's editors and checks (Etsy's refusals stop a listing; the
 * app's notes never do), helpers for all at once, "Keep as it is", and "Rewrite for this shop"
 * (a rewrite of the design: the allowance's rewrite rule is said before it runs).
 */
export function ShopTextStep({
  rows,
  edits,
  onEdit,
  shopName,
  rewriteRule,
  onRewrite,
  busy,
}: {
  rows: Row[];
  edits: Record<string, TextEdit>;
  onEdit: (contentId: string, edit: TextEdit | null) => void;
  shopName: string;
  rewriteRule: string;
  /** Rewrites these listings for the shop (the dialog asks the server and fills the editors). */
  onRewrite: (contentIds: string[]) => Promise<void>;
  busy: boolean;
}) {
  const open = rows.filter((r) => r.state === "new");
  const [addTags, setAddTags] = useState("");
  const [dropTags, setDropTags] = useState("");
  const [addWords, setAddWords] = useState("");
  const [dropWords, setDropWords] = useState("");
  const [find, setFind] = useState("");
  const [replace, setReplace] = useState("");
  const [asking, setAsking] = useState<string[] | null>(null);

  function applyAll() {
    for (const r of open) {
      const t = shownText(r, edits[r.content_id]);
      let title = t.title;
      let tags = t.tags;
      if (addWords.trim()) title = addWordsTo(title, addWords);
      if (dropWords.trim()) title = removeWordsFrom(title, dropWords);
      if (find) title = replaceIn(title, find, replace);
      if (addTags.trim()) tags = addTagsTo(tags, addTags);
      if (dropTags.trim()) tags = removeTagsFrom(tags, dropTags);
      if (title !== t.title || tags.join("\u0000") !== t.tags.join("\u0000")) {
        onEdit(r.content_id, { ...edits[r.content_id], title, tags });
      }
    }
    setAddTags("");
    setDropTags("");
    setAddWords("");
    setDropWords("");
    setFind("");
    setReplace("");
  }

  const freeNote = (ids: string[]) =>
    ids
      .map((id) => open.find((r) => r.content_id === id))
      .filter((r): r is Row => !!r)
      .map((r) => `${r.group_key || r.title.slice(0, 30)}: ${r.free_rewrites_left > 0 ? `${r.free_rewrites_left} free left` : "counts as a listing"}`)
      .join(" · ");

  return (
    <div className="space-y-3" data-testid="shop-text-step">
      <div className="space-y-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs">
        <p className="font-medium text-slate-800">
          <span>For all listings</span>
        </p>
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="flex flex-col gap-1">
            <span>Add these tags (comma between)</span>
            <input className="field py-1 text-sm" value={addTags} onChange={(e) => setAddTags(e.target.value)} aria-label="Add these tags to all" />
          </label>
          <label className="flex flex-col gap-1">
            <span>Remove these tags</span>
            <input className="field py-1 text-sm" value={dropTags} onChange={(e) => setDropTags(e.target.value)} aria-label="Remove these tags from all" />
          </label>
          <label className="flex flex-col gap-1">
            <span>Add these words to every title</span>
            <input className="field py-1 text-sm" value={addWords} onChange={(e) => setAddWords(e.target.value)} aria-label="Add these words to every title" />
          </label>
          <label className="flex flex-col gap-1">
            <span>Remove these words from every title</span>
            <input className="field py-1 text-sm" value={dropWords} onChange={(e) => setDropWords(e.target.value)} aria-label="Remove these words from every title" />
          </label>
          <label className="flex flex-col gap-1">
            <span>Find in titles</span>
            <input className="field py-1 text-sm" value={find} onChange={(e) => setFind(e.target.value)} aria-label="Find in titles" />
          </label>
          <label className="flex flex-col gap-1">
            <span>Replace with</span>
            <input className="field py-1 text-sm" value={replace} onChange={(e) => setReplace(e.target.value)} aria-label="Replace with" />
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={applyAll} data-testid="shop-text-apply-all"
            disabled={!(addTags.trim() || dropTags.trim() || addWords.trim() || dropWords.trim() || find)}>
            Apply to all
          </button>
          <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy || open.length === 0}
            onClick={() => setAsking(open.map((r) => r.content_id))} data-testid="shop-text-rewrite-all">
            <span translate="no">{`Rewrite all for ${shopName}`}</span>
          </button>
        </div>
        {asking && (
          <div key="ask" role="alertdialog" aria-label="Rewrite for this shop" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900">
            <p><span>{rewriteRule}</span></p>
            <p className="mt-1" translate="no"><span>{freeNote(asking)}</span></p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button type="button" className="btn-primary px-3 py-1.5 text-xs" disabled={busy}
                onClick={async () => {
                  const ids = asking;
                  setAsking(null);
                  await onRewrite(ids);
                }}>
                <span translate="no">{`Rewrite ${asking.length === 1 ? "it" : `${asking.length} listings`}`}</span>
              </button>
              <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setAsking(null)}>Not now</button>
            </div>
          </div>
        )}
      </div>

      <ul className="space-y-3">
        {open.map((r) => {
          const t = shownText(r, edits[r.content_id]);
          const local = draftErrors(t.title, t.tags, t.description);
          // Etsy's limits at once as it is typed; the server's answer (with the trademark filter) shortly after.
          const errors = [...new Set([...local, ...r.errors])];
          return (
            <li key={r.content_id} className="space-y-2 rounded-md border border-slate-200 p-3" data-testid="shop-text-row">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium text-slate-800" translate="no">{r.group_key || "Listing"}</span>
                <span className="flex flex-wrap gap-2">
                  {(r.own_text || edits[r.content_id]) && (
                    <button key="keep" type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => onEdit(r.content_id, { reset: true })}>
                      Keep as it is
                    </button>
                  )}
                  <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy}
                    onClick={() => setAsking([r.content_id])}>
                    Rewrite for this shop
                  </button>
                </span>
              </div>
              <label className="block text-xs text-slate-600">
                <span className="flex justify-between">
                  <span>Title</span>
                  <span translate="no" className={t.title.length > MAX_TITLE ? "text-rose-700" : ""}>{`${t.title.length} / ${MAX_TITLE}`}</span>
                </span>
                <input className="field mt-1 w-full text-sm" value={t.title} aria-label={`Title in ${shopName} for ${r.group_key}`}
                  onChange={(e) => onEdit(r.content_id, { ...edits[r.content_id], title: e.target.value })} />
              </label>
              <div className="text-xs text-slate-600">
                <TagEditor tags={t.tags} onChange={(tags) => onEdit(r.content_id, { ...edits[r.content_id], tags })} />
              </div>
              <details className="text-xs text-slate-600">
                <summary className="tap cursor-pointer">Description</summary>
                <textarea className="field mt-1 h-32 w-full text-sm" value={t.description} aria-label={`Description in ${shopName} for ${r.group_key}`}
                  onChange={(e) => onEdit(r.content_id, { ...edits[r.content_id], description: e.target.value })} />
              </details>
              {errors.length > 0 && (
                <ul key="errors" role="alert" className="list-disc space-y-0.5 pl-5 text-xs text-rose-700" data-testid="shop-text-errors">
                  {errors.map((e) => <li key={e}>{e}</li>)}
                </ul>
              )}
              {r.warnings.length > 0 && (
                <details key="notes" className="text-xs text-slate-600">
                  <summary className="tap cursor-pointer"><span>Notes (they do not stop it): </span><span translate="no">{r.warnings.length}</span></summary>
                  <ul className="mt-1 list-disc space-y-0.5 pl-5">{r.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
                </details>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
