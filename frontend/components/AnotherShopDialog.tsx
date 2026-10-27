"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import { previewSkus } from "@/lib/anotherShop";
import type { AnotherShopPreview, AnotherShopRequest, SkuMode } from "@/lib/types";
import { ShopTextStep, type TextEdit } from "./ShopTextStep";

/**
 * "Publish in another shop" (backend api/another_shop.py): the batch's listings, already on
 * Etsy in one shop, as drafts in another shop with that shop's profile and its own SKUs, and,
 * if the seller wants, its own title, tags and description ("Text for <shop>", skippable).
 * Photos go as they are; nothing changes in any other shop;
 * drafts do not count against the allowance. Going live there is the seller's own step
 * afterwards (Where and when, or Scheduled), as usual.
 */
export function AnotherShopDialog({
  batchId,
  contentIds,
  shops,
  initialShop,
  onClose,
  onDone,
}: {
  batchId: string;
  /** The listings chosen; null: every listing of the batch. */
  contentIds: string[] | null;
  shops: { id: string; name: string }[];
  initialShop: string | null;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const [shopId, setShopId] = useState<string>(initialShop ?? shops[0]?.id ?? "");
  const [profileId, setProfileId] = useState<string>("");
  const [mode, setMode] = useState<SkuMode>("keep");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [each, setEach] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<AnotherShopPreview | null>(null);
  // "Text for <shop>": off (each listing's text goes as it is) until the seller opens it.
  const [editText, setEditText] = useState(false);
  const [edits, setEdits] = useState<Record<string, TextEdit>>({});
  const [rewriting, setRewriting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => root.current?.focus(), []);

  const body: AnotherShopRequest = useMemo(
    () => ({
      connection_id: shopId,
      content_ids: contentIds,
      profile_id: profileId || null,
      sku_mode: mode,
      prefix_from: from,
      prefix_to: to,
      skus: mode === "each" ? each : {},
      texts: editText ? edits : {},
    }),
    [shopId, contentIds, profileId, mode, from, to, each, editText, edits],
  );

  function onEdit(id: string, edit: TextEdit | null) {
    setEdits((cur) => {
      const next = { ...cur };
      if (edit) next[id] = edit;
      else delete next[id];
      return next;
    });
  }

  // "Rewrite for this shop": the server writes and keeps it as that shop's text; the editors follow.
  async function rewrite(ids: string[]) {
    setRewriting(true);
    setNotice(null);
    try {
      const res = await api.anotherShopRewrite(batchId, { connection_id: shopId, content_ids: ids, profile_id: profileId || null });
      setEdits((cur) => {
        const next = { ...cur };
        for (const r of res.rows) if (!r.reason && r.title) next[r.content_id] = { title: r.title, tags: r.tags, description: r.description ?? "" };
        return next;
      });
      const why = res.rows.filter((r) => r.reason).map((r) => r.reason);
      setNotice(res.message + (why.length ? ` ${[...new Set(why)].join("; ")}` : ""));
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setRewriting(false);
    }
  }

  // The server's answer (refused SKUs, what cannot go): asked again shortly after each change.
  useEffect(() => {
    if (!shopId) return;
    let live = true;
    const t = setTimeout(() => {
      api
        .anotherShopPreview(batchId, body)
        .then((p) => {
          if (!live) return;
          setPreview(p);
          setError(null);
          if (profileId && !p.profiles.some((x) => x.id === profileId)) setProfileId("");
        })
        .catch((e) => live && setError(e.message ?? String(e)));
    }, 350);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [batchId, body, shopId, profileId]);

  // Every new SKU as it is typed (the server checks the same when it answers).
  const live = useMemo(() => (preview ? previewSkus(preview.rows, mode, from, to, each) : {}), [preview, mode, from, to, each]);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.anotherShop(batchId, body);
      onDone(res.message + (res.skipped.length ? ` ${res.skipped.length} could not go: ${res.skipped.map((s) => s.reason).join("; ")}.` : ""));
    } catch (e: any) {
      setError(e.message ?? String(e));
      setBusy(false);
    }
  }

  const name = preview?.shop_name ?? shops.find((s) => s.id === shopId)?.name ?? "that shop";
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-stretch justify-center bg-slate-900/60 lg:items-center lg:p-4" role="dialog"
      aria-modal="true" aria-label="Publish in another shop" data-testid="another-shop-dialog">
      <div ref={root} tabIndex={-1} onKeyDown={(e) => e.key === "Escape" && onClose()}
        className="flex h-full w-full flex-col overflow-hidden bg-white outline-none lg:h-auto lg:max-h-[calc(100vh-2rem)] lg:w-[min(52rem,calc(100vw-2rem))] lg:rounded-xl lg:shadow-2xl">
        <header className="flex items-center gap-2 border-b border-slate-200 px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold text-slate-900">Publish in another shop</h2>
            <p className="text-xs text-slate-600">
              <span>Drafts of these listings in another shop, with its profile, its SKUs and, if you like, its own text. Photos go as they are; nothing changes in your other shops.</span>
            </p>
          </div>
          <button type="button" className="btn-secondary h-11 w-11 shrink-0 p-0" aria-label="Close" onClick={onClose}>
            ✕
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4 text-sm">
          <fieldset>
            <legend className="label">Shop</legend>
            <select className="field mt-1 w-full sm:w-72" value={shopId} onChange={(e) => setShopId(e.target.value)} aria-label="Shop">
              {shops.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </fieldset>

          <fieldset>
            <legend className="label">Profile in <span translate="no">{name}</span></legend>
            <div className="mt-1 space-y-1">
              <label className="flex min-h-[2.75rem] items-start gap-2 py-1">
                <input type="radio" name="profile" className="mt-1" checked={!profileId} onChange={() => setProfileId("")} />
                <span>
                  <span>Each listing&apos;s own profile</span>
                  <Own names={preview?.own_profiles ?? []} />
                  <span className="block text-xs text-slate-500">
                    <span>With </span><span translate="no">{name}</span><span>&apos;s shipping, return and processing settings.</span>
                  </span>
                </span>
              </label>
              {(preview?.profiles ?? []).map((p) => (
                <label key={p.id} className="flex min-h-[2.75rem] items-start gap-2 py-1">
                  <input type="radio" name="profile" className="mt-1" checked={profileId === p.id} onChange={() => setProfileId(p.id)} />
                  <span translate="no">{p.name}</span>
                </label>
              ))}
              {preview && preview.profiles.length === 0 && (
                <p key="none" className="text-xs text-slate-500">
                  <span>No profile has </span><span translate="no">{name}</span><span> as its main shop yet.</span>
                </p>
              )}
            </div>
          </fieldset>

          <fieldset>
            <legend className="label">SKUs in <span translate="no">{name}</span></legend>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
              {(
                [
                  ["keep", "Keep them"],
                  ["prefix", "Replace a prefix"],
                  ["each", "Edit each one"],
                ] as const
              ).map(([value, label]) => (
                <label key={value} className="flex min-h-[2.75rem] items-center gap-2">
                  <input type="radio" name="sku-mode" checked={mode === value} onChange={() => setMode(value)} />
                  <span>{label}</span>
                </label>
              ))}
            </div>
            {mode === "prefix" && (
              <div key="prefix" className="mt-2 flex flex-wrap items-center gap-2">
                <span>Replace</span>
                <input className="field w-28 font-mono" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Prefix to replace" placeholder="PCS" />
                <span>with</span>
                <input className="field w-28 font-mono" value={to} onChange={(e) => setTo(e.target.value)} aria-label="New prefix" placeholder="ILC" />
              </div>
            )}
          </fieldset>

          <fieldset data-testid="shop-text-fieldset">
            <legend className="label">Text for <span translate="no">{name}</span></legend>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
              <label className="flex min-h-[2.75rem] items-center gap-2">
                <input type="radio" name="text-mode" checked={!editText} onChange={() => setEditText(false)} />
                <span>Keep each listing&apos;s text</span>
              </label>
              <label className="flex min-h-[2.75rem] items-center gap-2">
                <input type="radio" name="text-mode" checked={editText} onChange={() => setEditText(true)} />
                <span>Edit text for this shop</span>
              </label>
            </div>
            {editText && preview && (
              <ShopTextStep key="text-step"
                rows={preview.rows}
                edits={edits}
                onEdit={onEdit}
                shopName={name}
                rewriteRule={preview.rewrite_rule}
                onRewrite={rewrite}
                busy={rewriting || busy}
              />
            )}
            {notice && (
              <p key="notice" role="status" className="mt-2 text-xs text-brand-800" translate="no">{notice}</p>
            )}
          </fieldset>

          {preview && preview.refused.length > 0 && (
            <div key="refused" role="alert" className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800" data-testid="another-shop-refused">
              <p className="font-medium">
                <span>Each SKU can be used once in </span><span translate="no">{name}</span><span>:</span>
              </p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                {preview.refused.map((r) => (
                  <li key={r} className="break-words" translate="no">{r}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="overflow-x-auto rounded-md border border-slate-200">
            <table className="w-full text-left text-xs" data-testid="another-shop-rows">
              <thead className="bg-slate-50 text-slate-600">
                <tr>
                  <th className="px-3 py-2 font-medium">Listing</th>
                  <th className="px-3 py-2 font-medium">SKU now</th>
                  <th className="px-3 py-2 font-medium">SKU in <span translate="no">{name}</span></th>
                </tr>
              </thead>
              <tbody>
                {(preview?.rows ?? []).map((r) => {
                  const next = live[r.content_id];
                  return (
                    <tr key={r.content_id} className="border-t border-slate-100 align-top">
                      <td className="max-w-[16rem] px-3 py-2">
                        <span className="block truncate font-medium text-slate-800" title={r.title} translate="no">{r.group_key || r.title}</span>
                        {r.state !== "new" && (
                          <span key="why" className="block text-slate-500">{r.reason}</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3 py-2 font-mono text-slate-600" translate="no">{r.sku_from ?? "no SKU"}</td>
                      <td className="px-3 py-2">
                        {r.state !== "new" ? (
                          <span key="dash" className="text-slate-500">—</span>
                        ) : mode === "each" ? (
                          <input key="input" className="field w-36 py-1 font-mono text-sm" aria-label={`SKU in ${name} for ${r.group_key || r.title}`}
                            value={each[r.content_id] ?? r.sku_from ?? ""}
                            onChange={(e) => setEach((cur) => ({ ...cur, [r.content_id]: e.target.value }))} />
                        ) : (
                          <span key="sku" className="whitespace-nowrap font-mono text-slate-900" translate="no" data-testid="another-shop-sku">
                            {next?.sku || "no SKU"}
                          </span>
                        )}
                        {r.state === "new" && next?.note && (
                          <span key="note" className="block text-amber-800">{next.note}</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {!preview && !error && <p key="loading" className="px-3 py-3 text-xs text-slate-500">Loading…</p>}
          </div>
          {error && (
            <p key="error" role="alert" className="text-sm text-rose-700">{error}</p>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-4 py-3">
          <p className="min-w-0 flex-1 text-xs text-slate-600" translate="no">
            <span>{preview?.disabled ?? preview?.sentence ?? ""}</span>
          </p>
          <span className="flex gap-2">
            <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
            <button type="button" className="btn-primary" onClick={send} disabled={busy || !preview || !!preview.disabled}
              data-testid="another-shop-send">
              <span translate="no">{busy ? "Creating drafts…" : preview?.button ?? "Create drafts"}</span>
            </button>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function Own({ names }: { names: string[] }) {
  if (!names.length) return null;
  return (
    <span className="text-slate-500" translate="no">
      {` (${names.join(", ")})`}
    </span>
  );
}
