"use client";

import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  STATE_WORDS,
  addTagsTo,
  addWordsTo,
  removeTagsFrom,
  removeWordsFrom,
  replaceIn,
  replacePrefix,
  selectable,
  sendSummary,
  shopColour,
  shopLine,
  stateTone,
} from "@/lib/anotherShop";
import type { AnotherShopPreview, BatchShops, Content, GroupSchedule, Profile, SendPreview } from "@/lib/types";
import { ShopTextEditor } from "./ShopTextEditor";
import { SkuEditor } from "./SkuEditor";
import { StatusChip } from "./StatusChip";

import { Txt } from "@/components/Txt";
type Row = AnotherShopPreview["rows"][number];
/** A shop name ending a sentence: one full stop, even for "Juniper Print Co.". */
const said = (name: string) => (name.endsWith(".") ? name : `${name}.`);
interface Shop {
  id: string;
  name: string;
}

export interface ShopWorkspaceHandle {
  /** Bring the bar into view and open its shop list ("Publish in another shop" elsewhere). */
  focus: () => void;
}

/**
 * The batch page's shop workspace (seller feedback, 2026-10-27; it replaced the "Publish in
 * another shop" dialog). "Working on: <shop>" follows the sidebar's shop and the URL. Each
 * listing has a row for that shop: its state there, and its profile, SKU and text for that
 * shop, saved as they are changed (a field left alone follows the listing and says so). Rows
 * already in that shop are locked (edited on Etsy). Selected rows get bulk actions and "Send N
 * to <shop>": a summary first, then drafts on the normal path, then that shop's Where and when
 * for the rows sent. Nothing in any other shop changes; only approved listings are sent.
 */
export const ShopWorkspace = forwardRef<ShopWorkspaceHandle, {
  batchId: string;
  shops: Shop[];
  /** The shop in the bar (the sidebar's); null: none chosen yet. */
  shopId: string | null;
  onPick: (shopId: string) => void;
  profiles: Profile[];
  contents: Content[];
  shopData: BatchShops | null;
  /** Rows to select once the shop's rows are read (from a review card's link). */
  preselect: string[];
  /** Something was saved or sent: the page reads its data again. */
  onChanged: () => void;
}>(function ShopWorkspace({ batchId, shops, shopId, onPick, profiles, contents, shopData, preselect, onChanged }, ref) {
  const bar = useRef<HTMLSelectElement>(null);
  const root = useRef<HTMLDivElement>(null);
  useImperativeHandle(ref, () => ({
    focus: () => {
      root.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      bar.current?.focus();
    },
  }));
  const shop = shops.find((s) => s.id === shopId) ?? null;
  const colour = shopColour(Math.max(0, shops.findIndex((s) => s.id === shopId)));
  // Rows belong to the shop they were read for. Shown under another shop, or kept mounted across a
  // switch, a row's waiting save went to the new shop: one shop's text written into the next.
  const [loaded, setLoaded] = useState<{ shopId: string; rows: AnotherShopPreview } | null>(null);
  const preview = loaded && loaded.shopId === shopId ? loaded.rows : null;
  const shopInView = useRef(shopId);
  shopInView.current = shopId;
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [summary, setSummary] = useState<AnotherShopPreview | null>(null);
  const [sent, setSent] = useState<string[] | null>(null);

  const load = useCallback(async () => {
    if (!shopId) return setLoaded(null);
    try {
      const rows = await api.workspaceRows(batchId, shopId);
      if (shopInView.current === shopId) setLoaded({ shopId, rows });
    } catch (e: any) {
      if (shopInView.current === shopId) setProblem(e.message ?? String(e));
    }
  }, [batchId, shopId]);
  useEffect(() => {
    setPicked([]);
    setSummary(null);
    setSent(null);
    setNotice(null);
    setProblem(null);
    load();
  }, [load]);
  useEffect(() => {
    if (preview && preselect.length) setPicked((cur) => (cur.length ? cur : preselect.filter((id) => preview.rows.some((r) => r.content_id === id && selectable(r)))));
  }, [preview, preselect]);

  const rows = preview?.rows ?? [];
  const open = rows.filter(selectable);
  const chosen = picked.filter((id) => open.some((r) => r.content_id === id));
  const coverOf = useMemo(() => Object.fromEntries(contents.map((c) => [c.id, c.asset_id])), [contents]);
  const shopProfiles = profiles.filter((p) => p.connection_id === shopId);
  const counts = shopData?.shops.find((s) => s.shop_id === shopId) ?? null;

  async function run(what: () => Promise<unknown>, done?: string) {
    setBusy(true);
    setProblem(null);
    try {
      await what();
      if (done) setNotice(done);
      await load();
      onChanged();
    } catch (e: any) {
      setProblem(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  async function openSummary() {
    if (!shopId || !chosen.length) return;
    setBusy(true);
    setProblem(null);
    try {
      const answer = await api.workspaceRows(batchId, shopId, chosen);
      if (shopInView.current === shopId) setSummary(answer);
    } catch (e: any) {
      setProblem(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    if (!shopId || !summary) return;
    const ids = summary.rows.filter((r) => r.state === "new" && r.errors.length === 0).map((r) => r.content_id);
    await run(async () => {
      const res = await api.anotherShop(batchId, { connection_id: shopId, content_ids: ids, sku_mode: "keep" });
      setNotice(res.message);
      setSummary(null);
      setPicked([]);
      setSent(ids);
    });
  }

  const unapproved = chosen.filter((id) => rows.find((r) => r.content_id === id)?.approved === false);

  return (
    <section ref={root} id="shop-workspace" className="scroll-mt-4 space-y-3" data-testid="shop-workspace">
      <div className={"card flex flex-wrap items-center gap-x-4 gap-y-2 border-2 p-3 " + (shop ? colour : "")} data-testid="shop-bar">
        <label className="flex min-h-[2.75rem] items-center gap-2 text-sm font-medium">
          <span className="whitespace-nowrap">Working on:</span>
          <select ref={bar} className="field py-1.5 text-sm font-medium" value={shopId ?? ""} aria-label="Working on shop"
            onChange={(e) => e.target.value && onPick(e.target.value)} data-testid="shop-bar-select">
            {!shopId && <option key="none" value="">Choose a shop…</option>}
            {shops.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>
        {shop && counts ? (
          <span key="counts" className="flex flex-wrap gap-x-3 gap-y-1 text-xs" translate="no" data-testid="shop-bar-counts">
            <span>{`${counts.not_sent} not sent`}</span>
            <span>{`${counts.drafted} drafted`}</span>
            <span>{`${counts.scheduled} scheduled`}</span>
            <span>{`${counts.published} published`}</span>
          </span>
        ) : (
          <span key="hint" className="text-xs text-slate-600">
            <span>Choose a shop: each listing&apos;s profile, SKU and text for it are set on its row, then send.</span>
          </span>
        )}
      </div>
      {!shop && shopData && (
        <ul key="all" className="flex flex-wrap gap-x-5 gap-y-1 px-1 text-xs text-slate-600" data-testid="shop-bar-all">
          {shopData.shops.filter((s) => s.not_sent < s.total).map((s) => (
            <li key={s.shop_id}><span translate="no">{`${s.name} · ${shopLine(s)}`}</span></li>
          ))}
        </ul>
      )}

      {shop && preview && (
        <div key="rows" className="space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setPicked(open.map((r) => r.content_id))}
              data-testid="select-not-in-shop" disabled={!open.length}>
              <span translate="no">{`Select every row not yet in ${shop.name}`}</span>
            </button>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setPicked([])} disabled={!chosen.length}>Select none</button>
            <span className="text-slate-600" translate="no">{`${chosen.length} of ${open.length} selected`}</span>
          </div>
          {notice && <p key="notice" role="status" className="text-sm text-brand-800" translate="no">{notice}</p>}
          {problem && <p key="problem" role="alert" className="text-sm text-rose-700">{problem}</p>}
          <ul className="space-y-2">
            {rows.map((r) => (
              <WorkspaceRow key={`${shopId}:${r.content_id}`} row={r} shop={shop} colour={colour} batchId={batchId}
                cover={coverOf[r.content_id]} profiles={shopProfiles} picked={chosen.includes(r.content_id)}
                rewriteRule={preview.rewrite_rule} busy={busy}
                onPick={(on) => setPicked((cur) => (on ? [...cur, r.content_id] : cur.filter((x) => x !== r.content_id)))}
                run={run} reload={load} />
            ))}
          </ul>
          {sent && sent.length > 0 && (
            <ShopWhen key="when" batchId={batchId} shop={shop} contentIds={sent} onDone={(m) => { setNotice(m); setSent(null); load(); onChanged(); }} />
          )}
        </div>
      )}

      {shop && chosen.length > 0 && (
        <BulkBar key="bulk" batchId={batchId} shop={shop} rows={rows.filter((r) => chosen.includes(r.content_id))}
          profiles={shopProfiles} rewriteRule={preview?.rewrite_rule ?? ""} busy={busy} run={run}
          unapproved={unapproved}
          onApprove={() => run(() => api.workspaceApprove(batchId, shop.id, unapproved), `Approved ${unapproved.length} for ${said(shop.name)}`)}
          onSend={openSummary} />
      )}

      {summary && shop && (
        <SendSummary key="summary" summary={summary} chosen={summary.rows.map((r) => r.content_id)} shop={shop} busy={busy} onCancel={() => setSummary(null)} onSend={send}
          onApprove={(ids) => run(async () => {
            await api.workspaceApprove(batchId, shop.id, ids);
            setSummary(await api.workspaceRows(batchId, shop.id, summary.rows.map((r) => r.content_id)));
          }, `Approved ${ids.length} for ${said(shop.name)}`)} />
      )}
    </section>
  );
});

function WorkspaceRow({
  row, shop, colour, batchId, cover, profiles, picked, rewriteRule, busy, onPick, run, reload,
}: {
  row: Row;
  shop: Shop;
  colour: string;
  batchId: string;
  cover?: string;
  profiles: Profile[];
  picked: boolean;
  rewriteRule: string;
  busy: boolean;
  onPick: (on: boolean) => void;
  run: (what: () => Promise<unknown>, done?: string) => Promise<void>;
  reload: () => Promise<void>;
}) {
  const locked = row.state === "there";
  const [sku, setSku] = useState(row.sku ?? "");
  const [skuProblem, setSkuProblem] = useState<string | null>(null);
  const [textOpen, setTextOpen] = useState(false);
  useEffect(() => setSku(row.sku ?? ""), [row.sku]);
  // Duplicates in that shop said as the SKU is typed.
  useEffect(() => {
    if (locked || sku === (row.sku ?? "")) return setSkuProblem(null);
    const t = setTimeout(() => {
      api.workspaceSkusCheck(batchId, shop.id, { [row.content_id]: sku })
        .then((res) => setSkuProblem(res.refused[0] ?? null)).catch(() => {});
    }, 350);
    return () => clearTimeout(t);
  }, [sku, row.sku, row.content_id, batchId, shop.id, locked]);
  const label = row.group_key || row.title.slice(0, 30);

  return (
    <li className={"card p-3 " + (picked ? "ring-2 ring-brand-500" : "")} data-testid="workspace-row">
      <div className="flex flex-wrap items-center gap-2">
        <label className="-m-2 flex min-h-[2.75rem] min-w-[2.75rem] items-center justify-center p-2">
          <input type="checkbox" className="h-4 w-4" checked={picked} disabled={locked} aria-label={`Select ${label}`}
            onChange={(e) => onPick(e.target.checked)} />
        </label>
        {cover && (
          // eslint-disable-next-line @next/next/no-img-element
          <img key="cover" src={api.assetImage(cover, "small")} alt="" className="h-11 w-11 rounded object-cover" loading="lazy" />
        )}
        <span className="min-w-[9rem] flex-1">
          <span className="block truncate text-sm font-medium text-slate-900" translate="no" title={row.title}>{label}</span>
          <span className="block truncate text-xs text-slate-600" translate="no">{row.title}</span>
        </span>
        <span className={"rounded border px-2 text-xs font-medium leading-6 " + colour} translate="no">{shop.name}</span>
        <StatusChip tone={stateTone(row.shop_state)}>{STATE_WORDS[row.shop_state]}</StatusChip>
      </div>

      {locked ? (
        <div key="locked" className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-600" data-testid="workspace-row-locked">
          {row.listing_link && (
            <a key="link" href={row.listing_link} target="_blank" rel="noreferrer" className="tap text-brand-700 underline">View on Etsy</a>
          )}
          <span>Edited on Etsy: profile and text there stay as they are.</span>
          <SkuEditor compact value={row.sku} contentId={row.content_id} batchId={batchId} shopId={shop.id} shopName={shop.name}
            save={(next) => api.setGroupSku(batchId, row.group_key, next, shop.id)} onSaved={() => reload()} />
        </div>
      ) : (
        <div key="open" className="mt-2 grid gap-2 text-xs sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_auto]">
          <label className="flex flex-col gap-1 text-slate-600">
            <span>{`Profile in ${shop.name}`}</span>
            <select className="field py-1.5 text-sm" value={row.profile_id ?? ""} disabled={busy} aria-label={`Profile in ${shop.name} for ${label}`}
              onChange={(e) => run(() => api.workspaceProfile(batchId, shop.id, [row.content_id], e.target.value || null))}>
              <option value="">{`Listing's own (${row.profile_id ? "its profile" : row.profile_name ?? "none"}) via shop link`}</option>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            {!row.profile_id && <span key="f" className="text-slate-500">Follows the listing</span>}
          </label>
          <form className="flex flex-col gap-1 text-slate-600"
            onSubmit={(e) => {
              e.preventDefault();
              if (!skuProblem && sku !== (row.sku ?? "")) run(() => api.workspaceSkus(batchId, shop.id, { [row.content_id]: sku }));
            }}>
            <span>{`SKU in ${shop.name}`}</span>
            <span className="flex gap-2">
              <input className="field min-w-0 flex-1 py-1.5 font-mono text-sm" value={sku} onChange={(e) => setSku(e.target.value)}
                aria-label={`SKU in ${shop.name} for ${label}`} aria-invalid={skuProblem ? "true" : undefined} />
              <button type="submit" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy || !!skuProblem || sku === (row.sku ?? "")}>
                Edit SKU
              </button>
            </span>
            {skuProblem ? (
              <span key="p" role="alert" className="text-rose-700" translate="no">{skuProblem}</span>
            ) : !row.sku_own ? (
              <span key="f" className="text-slate-500">Follows the listing</span>
            ) : null}
          </form>
          <div className="flex items-end">
            <button type="button" className="btn-secondary w-full px-3 py-1.5 text-xs" aria-expanded={textOpen}
              onClick={() => setTextOpen((v) => !v)} data-testid="workspace-text-toggle">
              <span translate="no">{`${textOpen ? "Close" : "Edit"} text for ${shop.name}`}</span>
            </button>
          </div>
          {row.text_own.length === 0 && !textOpen && (
            <p key="tf" className="text-slate-500 sm:col-span-full">Text follows the listing.</p>
          )}
          {row.state === "cannot" && (
            <p key="why" className="text-amber-800 sm:col-span-full" data-testid="workspace-row-why">
              <span translate="no">{`Not sent: ${row.reason}`}</span>
              {row.partner === "required" && row.partner_profile_id && (
                <a key="profile" href={`/profiles#profile-${row.partner_profile_id}`} className="tap ml-2 text-brand-700 underline">Open the profile</a>
              )}
            </p>
          )}
          {row.partner === "note" && (
            <p key="partner" className="text-slate-500 sm:col-span-full" translate="no">
              {`Note: ${row.profile_name ?? "its profile"} names no production partner; only needed if a partner makes it.`}
            </p>
          )}
          {row.errors.length > 0 && !textOpen && (
            <p key="err" role="alert" className="text-rose-700 sm:col-span-full" translate="no">{`Etsy would refuse its text: ${row.errors[0]}`}</p>
          )}
          {textOpen && (
            <div key="text" className="sm:col-span-full">
              <ShopTextEditor
                text={{ title: row.title, tags: row.tags, description: row.description }}
                own={row.text_own} errors={row.errors} warnings={row.warnings} shopName={shop.name} label={label}
                rewriteRule={rewriteRule} freeRewritesLeft={row.free_rewrites_left} busy={busy}
                onSave={async (t) => { await api.workspaceTexts(batchId, shop.id, { [row.content_id]: t }); await reload(); }}
                onKeep={() => run(() => api.workspaceTexts(batchId, shop.id, { [row.content_id]: { reset: true } }))}
                onRewrite={() => run(async () => {
                  const res = await api.anotherShopRewrite(batchId, { connection_id: shop.id, content_ids: [row.content_id] });
                  const why = res.rows.find((x) => x.reason)?.reason;
                  if (why) throw new Error(why);
                }, `Rewritten for ${said(shop.name)}`)}
              />
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function BulkBar({
  batchId, shop, rows, profiles, rewriteRule, busy, run, unapproved, onApprove, onSend,
}: {
  batchId: string;
  shop: Shop;
  rows: Row[];
  profiles: Profile[];
  rewriteRule: string;
  busy: boolean;
  run: (what: () => Promise<unknown>, done?: string) => Promise<void>;
  unapproved: string[];
  onApprove: () => void;
  onSend: () => void;
}) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [refused, setRefused] = useState<string[]>([]);
  const [addTags, setAddTags] = useState("");
  const [dropTags, setDropTags] = useState("");
  const [addWords, setAddWords] = useState("");
  const [dropWords, setDropWords] = useState("");
  const [find, setFind] = useState("");
  const [replace, setReplace] = useState("");
  const [asking, setAsking] = useState(false);
  const [more, setMore] = useState(false);
  const ids = rows.map((r) => r.content_id);
  // Every new SKU as the prefix is typed, and the duplicate check shortly after.
  const next = useMemo(() => Object.fromEntries(rows.map((r) => [r.content_id, replacePrefix(r.sku, from, to)])), [rows, from, to]);
  const changed = Object.fromEntries(Object.entries(next).filter(([id, [sku, ok]]) => ok && sku !== (rows.find((r) => r.content_id === id)?.sku ?? "")).map(([id, [sku]]) => [id, sku]));
  useEffect(() => {
    if (!Object.keys(changed).length) return setRefused([]);
    const t = setTimeout(() => {
      api.workspaceSkusCheck(batchId, shop.id, changed).then((r) => setRefused(r.refused)).catch(() => {});
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(changed), batchId, shop.id]);

  function texts() {
    const out: Record<string, { title: string; tags: string[] }> = {};
    for (const r of rows) {
      let title = r.title;
      let tags = r.tags;
      if (addWords.trim()) title = addWordsTo(title, addWords);
      if (dropWords.trim()) title = removeWordsFrom(title, dropWords);
      if (find) title = replaceIn(title, find, replace);
      if (addTags.trim()) tags = addTagsTo(tags, addTags);
      if (dropTags.trim()) tags = removeTagsFrom(tags, dropTags);
      if (title !== r.title || tags.join("\u0000") !== r.tags.join("\u0000")) out[r.content_id] = { title, tags };
    }
    return out;
  }
  const textChanges = texts();
  const free = rows.filter((r) => r.free_rewrites_left > 0).length;
  const approvable = rows.length - unapproved.length;

  return (
    <div className="fixed inset-x-0 bottom-0 z-30 max-h-[70vh] overflow-y-auto border-t-2 border-slate-300 bg-white p-3 shadow-2xl sm:static sm:max-h-none sm:rounded-xl sm:border-2 sm:shadow-none"
      data-testid="workspace-bulk">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-slate-900" translate="no">{`${rows.length} selected for ${shop.name}`}</span>
        <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setMore((v) => !v)} aria-expanded={more}>
          {more ? "Fewer actions" : "Bulk actions"}
        </button>
        <span className="ml-auto flex flex-wrap gap-2">
          {unapproved.length > 0 && (
            <button key="approve" type="button" className="btn-secondary" onClick={onApprove} disabled={busy} data-testid="workspace-approve">
              <span translate="no">{`Approve selected (${unapproved.length})`}</span>
            </button>
          )}
          <button type="button" className="btn-primary" onClick={onSend} disabled={busy || approvable === 0} data-testid="workspace-send">
            <span translate="no">{`Send ${approvable} to ${shop.name}`}</span>
          </button>
        </span>
      </div>
      {unapproved.length > 0 && (
        <p key="unapproved" className="mt-1 text-xs text-amber-800" translate="no">
          {`${unapproved.length} not approved yet: only approved listings are sent.`}
        </p>
      )}
      {more && (
        <div key="more" className="mt-3 grid gap-3 text-xs sm:grid-cols-2">
          <label className="flex flex-col gap-1">
            <span>Profile for the selected</span>
            <select className="field py-1.5 text-sm" value="" disabled={busy} aria-label={`Profile in ${shop.name} for the selected`}
              onChange={(e) => e.target.value && run(() => api.workspaceProfile(batchId, shop.id, ids, e.target.value === "own" ? null : e.target.value), "Profile set.")}>
              <option value="">Choose…</option>
              <option value="own">Each listing&apos;s own via shop link</option>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </label>
          <div className="flex flex-col gap-1">
            <span>Replace SKU prefix</span>
            <span className="flex items-center gap-2">
              <input className="field w-24 py-1.5 font-mono text-sm" value={from} onChange={(e) => setFrom(e.target.value)} placeholder="PCS" aria-label="SKU prefix to replace" />
              <span>with</span>
              <input className="field w-24 py-1.5 font-mono text-sm" value={to} onChange={(e) => setTo(e.target.value)} placeholder="ILC" aria-label="New SKU prefix" />
              <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy || !Object.keys(changed).length || refused.length > 0}
                onClick={() => run(() => api.workspaceSkus(batchId, shop.id, changed), "SKUs saved.")}>Apply</button>
            </span>
            {Object.keys(changed).length > 0 && (
              <span key="preview" className="font-mono text-slate-700" translate="no" data-testid="bulk-sku-preview">
                <span><span>{rows.slice(0, 6).map((r) => `${r.sku ?? "—"} → ${next[r.content_id][1] ? next[r.content_id][0] : `${r.sku ?? "—"} (kept)`}`).join(" · ")}</span>
                <Txt>{rows.length > 6 ? ` · and ${rows.length - 6} more` : ""}</Txt></span>
              </span>
            )}
            {refused.length > 0 && (
              <ul key="refused" role="alert" className="list-disc pl-5 text-rose-700" data-testid="bulk-sku-refused">
                {refused.map((r) => <li key={r} translate="no">{r}</li>)}
              </ul>
            )}
          </div>
          <label className="flex flex-col gap-1"><span>Add these tags</span>
            <input className="field py-1.5 text-sm" value={addTags} onChange={(e) => setAddTags(e.target.value)} aria-label="Add these tags to the selected" /></label>
          <label className="flex flex-col gap-1"><span>Remove these tags</span>
            <input className="field py-1.5 text-sm" value={dropTags} onChange={(e) => setDropTags(e.target.value)} aria-label="Remove these tags from the selected" /></label>
          <label className="flex flex-col gap-1"><span>Add these words to titles</span>
            <input className="field py-1.5 text-sm" value={addWords} onChange={(e) => setAddWords(e.target.value)} aria-label="Add these words to the titles" /></label>
          <label className="flex flex-col gap-1"><span>Remove these words from titles</span>
            <input className="field py-1.5 text-sm" value={dropWords} onChange={(e) => setDropWords(e.target.value)} aria-label="Remove these words from the titles" /></label>
          <label className="flex flex-col gap-1"><span>Find in titles</span>
            <input className="field py-1.5 text-sm" value={find} onChange={(e) => setFind(e.target.value)} aria-label="Find in the titles" /></label>
          <label className="flex flex-col gap-1"><span>Replace with</span>
            <input className="field py-1.5 text-sm" value={replace} onChange={(e) => setReplace(e.target.value)} aria-label="Replace in the titles with" /></label>
          <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy || !Object.keys(textChanges).length}
              onClick={() => run(async () => {
                await api.workspaceTexts(batchId, shop.id, textChanges);
                for (const set of [setAddTags, setDropTags, setAddWords, setDropWords, setFind, setReplace]) set("");
              }, `Text changed for ${Object.keys(textChanges).length}.`)}>
              <span translate="no">{`Apply text changes (${Object.keys(textChanges).length})`}</span>
            </button>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy} onClick={() => setAsking(true)}>
              <span translate="no">{`Rewrite for ${shop.name}`}</span>
            </button>
          </div>
          {asking && (
            <div key="ask" role="alertdialog" aria-label={`Rewrite for ${shop.name}`} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900 sm:col-span-2">
              <p><span>{rewriteRule}</span></p>
              <p className="mt-1" translate="no"><span>{`${free} of ${rows.length} still have a free rewrite; the others count as listings.`}</span></p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button type="button" className="btn-primary px-3 py-1.5 text-xs" disabled={busy}
                  onClick={() => { setAsking(false); run(async () => {
                    const res = await api.anotherShopRewrite(batchId, { connection_id: shop.id, content_ids: ids });
                    return res;
                  }, `Rewritten for ${said(shop.name)}`); }}>
                  <span translate="no">{`Rewrite ${rows.length}`}</span>
                </button>
                <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setAsking(false)}>Not now</button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SendSummary({ summary, chosen, shop, busy, onCancel, onSend, onApprove }: {
  summary: AnotherShopPreview;
  chosen: string[];
  shop: Shop;
  busy: boolean;
  onCancel: () => void;
  onSend: () => void;
  onApprove: (ids: string[]) => void;
}) {
  const s = sendSummary(summary, chosen);
  // Never a dead end: what stops the button is said on it, with the one thing to do.
  const blocked = summary.refused.length ? "Change the SKUs listed first"
    : s.going === 0 && s.toApprove.length ? `Approve them for ${shop.name} first`
    : s.going === 0 ? "Nothing here can go yet: see why above" : null;
  return (
    <div role="alertdialog" aria-label={`Send to ${shop.name}`} className="card space-y-2 border-2 border-brand-300 p-4 text-sm" data-testid="workspace-summary">
      <p className="font-medium text-slate-900" translate="no">{`${s.going} draft${s.going === 1 ? "" : "s"} in ${said(shop.name)} Nothing changes in your other shops.`}</p>
      <ul className="list-disc space-y-0.5 pl-5 text-xs text-slate-700" translate="no">
        <li>{`Profiles: ${s.profiles.map(([name, n]) => `${name} (${n})`).join(", ") || "none"}`}</li>
        <li>{`SKUs: ${s.skus.slice(0, 8).join(", ")}${s.skus.length > 8 ? ` and ${s.skus.length - 8} more` : ""}${s.ownSku ? ` (${s.ownSku} with a SKU of their own for ${shop.name})` : ""}`}</li>
        <li>{`${s.ownText} with text of their own for ${shop.name}; the rest go with the listing's text`}</li>
      </ul>
      {s.notGoing.length > 0 && (
        <div key="not" className="text-xs text-amber-900" data-testid="workspace-summary-refused">
          <p className="font-medium">Not sent:</p>
          <ul className="list-disc pl-5" translate="no">{s.notGoing.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {s.toApprove.length > 0 && (
          <button key="approve" type="button" className={s.going === 0 ? "btn-primary" : "btn-secondary"} onClick={() => onApprove(s.toApprove)} disabled={busy}
            data-testid="workspace-summary-approve">
            <span translate="no">{`Approve these ${s.toApprove.length} for ${shop.name}`}</span>
          </button>
        )}
        <button type="button" className={s.going === 0 ? "btn-secondary" : "btn-primary"} onClick={onSend} disabled={busy || !!blocked} data-testid="workspace-summary-send">
          <span translate="no">{blocked ?? summary.button}</span>
        </button>
        <button type="button" className="btn-secondary" onClick={onCancel}>Back</button>
      </div>
    </div>
  );
}

/** After sending: that shop's Where and when for the rows sent (publish now or spread out). */
function ShopWhen({ batchId, shop, contentIds, onDone }: {
  batchId: string;
  shop: Shop;
  contentIds: string[];
  onDone: (message: string) => void;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const [when, setWhen] = useState<"publish" | "spread">("spread");
  const [schedule, setSchedule] = useState<GroupSchedule>({
    start_date: today, per_shop_per_day: 5, window_start: "09:00", window_end: "17:00", spacing_minutes: 30, stagger_minutes: 5,
  });
  const [preview, setPreview] = useState<SendPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const only = useMemo(() => ({ shop: shop.id, contentIds }), [shop.id, contentIds]);
  useEffect(() => {
    let live = true;
    const t = setTimeout(() => {
      api.sendPreview(batchId, when, when === "spread" ? schedule : null, only)
        .then((p) => live && (setPreview(p), setError(null))).catch((e) => live && setError(e.message ?? String(e)));
    }, 300);
    return () => { live = false; clearTimeout(t); };
  }, [batchId, when, schedule, only]);
  return (
    <div className="card space-y-2 p-4 text-sm" data-testid="workspace-when">
      <p className="font-medium text-slate-900" translate="no">{`Where and when in ${shop.name}: ${contentIds.length} sent`}</p>
      <div className="flex flex-wrap gap-x-4">
        <label className="flex min-h-[2.75rem] items-center gap-2">
          <input type="radio" name="ws-when" checked={when === "spread"} onChange={() => setWhen("spread")} /><span>Spread out</span>
        </label>
        <label className="flex min-h-[2.75rem] items-center gap-2">
          <input type="radio" name="ws-when" checked={when === "publish"} onChange={() => setWhen("publish")} /><span>Publish now</span>
        </label>
      </div>
      {when === "spread" && (
        <div key="spread" className="flex flex-wrap items-end gap-3 text-xs">
          <label className="flex flex-col gap-1"><span>From</span>
            <input type="date" className="field py-1.5 text-sm" value={schedule.start_date} onChange={(e) => setSchedule({ ...schedule, start_date: e.target.value })} /></label>
          <label className="flex flex-col gap-1"><span>A day</span>
            <input type="number" min={1} max={50} className="field w-20 py-1.5 text-sm" value={schedule.per_shop_per_day}
              onChange={(e) => setSchedule({ ...schedule, per_shop_per_day: Math.max(1, Number(e.target.value) || 1) })} /></label>
          <label className="flex flex-col gap-1"><span>Between</span>
            <input type="time" className="field py-1.5 text-sm" value={schedule.window_start} onChange={(e) => setSchedule({ ...schedule, window_start: e.target.value })} /></label>
          <label className="flex flex-col gap-1"><span>and</span>
            <input type="time" className="field py-1.5 text-sm" value={schedule.window_end} onChange={(e) => setSchedule({ ...schedule, window_end: e.target.value })} /></label>
        </div>
      )}
      <p className="text-xs text-slate-600" translate="no">{error ?? preview?.disabled ?? preview?.sentence ?? ""}</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn-primary" disabled={busy || !preview || !!preview.disabled}
          onClick={async () => {
            setBusy(true);
            try {
              const res = await api.send(batchId, when, when === "spread" ? schedule : null, only);
              onDone(res.message);
            } catch (e: any) {
              setError(e.message ?? String(e));
            } finally {
              setBusy(false);
            }
          }}>
          <span translate="no">{preview?.button ?? "…"}</span>
        </button>
        <button type="button" className="btn-secondary" onClick={() => onDone("They stay drafts; publish or schedule them any time.")}>Keep as drafts</button>
      </div>
    </div>
  );
}
