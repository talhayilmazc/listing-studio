"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Asset, BatchDetail, Content, Group, Profile, Publication } from "@/lib/types";
import { waitForJob } from "@/lib/jobs";
import { StatusPill } from "@/components/StatusPill";
import { CostPanel } from "@/components/CostPanel";
import { ProfilePicker } from "@/components/ProfilePicker";
import { ShopBadge, ShopPicker } from "@/components/ShopPicker";
import { BatchName } from "@/components/BatchName";
import { useShops } from "@/components/ShopProvider";
import { PatternPicker, usePatternListings } from "@/components/PatternPicker";
import { useSession } from "@/components/SessionProvider";
import { GroupImages } from "@/components/GroupImages";

import { Txt } from "@/components/Txt";
interface AssetGroup {
  key: string;
  label: string;
  sku: string | null;
  assets: Asset[];
  done: boolean;
}

export default function BatchPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const [batch, setBatch] = useState<BatchDetail | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [settings, setSettings] = useState<Record<string, Group>>({});
  const [bulkProfileId, setBulkProfileId] = useState<string>("");
  // The account's shops, and the one the switcher is on (offered, never assumed).
  const { shops, selected: switcherShop } = useShops();
  const shopChoices = (shops ?? []).map((sh) => ({ id: sh.id, name: sh.name }));
  const manyShops = shopChoices.length > 1;
  // Groups ticked for bulk-apply.
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // group key, or "__all__"
  const [notice, setNotice] = useState<string | null>(null);
  // Refusals and errors (e.g. the allowance is used up): shown as a warning, not a notice.
  const [problem, setProblem] = useState<string | null>(null);
  const [failures, setFailures] = useState<{ original_filename: string; error: string }[]>([]);
  const [costKey, setCostKey] = useState(0);
  // Modelling listings on the seller's own (v7 §B), when an admin turned it on.
  const { account } = useSession();
  const patternsOn = Boolean(account?.features?.own_patterns);
  const patternListings = usePatternListings(patternsOn);
  // The groups' content: whether it is approved, and whether its draft is on Etsy.
  const [contents, setContents] = useState<Content[]>([]);
  // A group waiting for the seller to confirm replacing something.
  const [confirming, setConfirming] = useState<{ key: string; kind: "regenerate" | "replace" } | null>(null);

  const load = useCallback(async () => {
    try {
      setBatch(await api.getBatch(id));
      setContents(await api.listContent(id));
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, [id]);

  const loadGroups = useCallback(async () => {
    try {
      const gs = await api.listGroups(id);
      setSettings(Object.fromEntries(gs.map((g) => [g.group_key, g])));
    } catch {
      /* groups appear after upload; ignore transient errors */
    }
  }, [id]);

  useEffect(() => {
    load();
    loadGroups();
    api
      .listProfiles()
      .then((all) => setProfiles(all.filter((p) => p.confirmed)))
      .catch(() => {});
  }, [load, loadGroups]);

  // One folder = one listing group (D1); merge asset display with server settings.
  const groups: AssetGroup[] = useMemo(() => {
    if (!batch) return [];
    const by = new Map<string, Asset[]>();
    for (const a of batch.assets) {
      const key = a.group_key ?? "";
      (by.get(key) ?? by.set(key, []).get(key)!).push(a);
    }
    return [...by.entries()]
      .map(([key, assets]) => ({
        key,
        label: key === "" ? "(root)" : key,
        sku: assets.find((a) => a.parsed_sku)?.parsed_sku ?? null,
        assets: assets.sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0)),
        done: assets.some((a) => a.has_content),
      }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [batch]);

  // Persist a choice: for one group (the unset groups after it take the same),
  // for the ticked groups, or for every group not set by hand. Only the fields
  // sent change.
  async function assign(body: Parameters<typeof api.assignGroup>[1]) {
    try {
      setProblem(null);
      const gs = await api.assignGroup(id, body);
      setSettings(Object.fromEntries(gs.map((g) => [g.group_key, g])));
    } catch (e: any) {
      setProblem(e.message ?? String(e));
    }
  }

  // The shop the batch is for. Groups not set by hand move with it.
  async function chooseBatchShop(shop: string) {
    try {
      setProblem(null);
      const gs = await api.setBatchShop(id, shop);
      setSettings(Object.fromEntries(gs.map((g) => [g.group_key, g])));
      setBulkProfileId("");
      await load();
    } catch (e: any) {
      setProblem(e.message ?? String(e));
    }
  }

  // "Generate content for all", one group per request (docs/duzeltmeler-v6.md §A3).
  // A single request for the whole batch outlived the proxy's timeout on large
  // batches: the page showed an error and never refreshed the cost panel while
  // the server kept generating. Group by group, every result and the cost land
  // as they happen.
  async function generateAll() {
    const todo = groups.filter((g) => !g.done);
    if (todo.length === 0) {
      setNotice("Every group already has content.");
      return;
    }
    setBusy("__all__");
    setFailures([]);
    let generated = 0;
    let failed = 0;
    let skipped = 0;
    const allFailures: { original_filename: string; error: string }[] = [];
    for (const [i, g] of todo.entries()) {
      setNotice(`Generating ${i + 1} of ${todo.length}: ${g.label}…`);
      try {
        const res = await api.generate(id, bulkProfileId || undefined, g.key);
        generated += res.generated;
        failed += res.failed;
        skipped += res.skipped;
        allFailures.push(...res.failures);
      } catch (e: any) {
        failed += 1;
        allFailures.push({ original_filename: g.label, error: e.message ?? String(e) });
      }
      setFailures([...allFailures]);
      await load();
      setCostKey((k) => k + 1); // the cost panel follows each group
    }
    setNotice(`Generated ${generated}, failed ${failed}, skipped ${skipped}.`);
    setBusy(null);
  }

  async function generate(groupKey?: string) {
    setBusy(groupKey ?? "__all__");
    setNotice(null);
    setProblem(null);
    setFailures([]);
    try {
      const res = await api.generate(id, bulkProfileId || undefined, groupKey);
      setNotice(`Generated ${res.generated}, failed ${res.failed}, skipped ${res.skipped}.`);
      setFailures(res.failures);
      await load();
      setCostKey((k) => k + 1);
    } catch (e: any) {
      setProblem(e.message ?? String(e));
    } finally {
      setBusy(null);
    }
  }

  // "Regenerate": new content in place of the group's current one. One more LLM
  // call; approved content needs the seller's confirmation, and a group whose
  // draft is on Etsy is never replaced here (the server refuses it too).
  async function regenerate(g: AssetGroup, approved: boolean) {
    setConfirming(null);
    setBusy(g.key);
    setNotice(null);
    setProblem(null);
    setFailures([]);
    try {
      const res = await api.generate(id, bulkProfileId || undefined, g.key, {
        replace: true,
        replaceApproved: approved,
      });
      const why = (res.skipped_groups ?? []).map((s) => s.reason).join("; ");
      setNotice(
        res.generated
          ? `New content written for ${g.label}${approved ? "; approve it again on the review page" : ""}.`
          : why || `Nothing regenerated for ${g.label}.`,
      );
      setFailures(res.failures);
      await load();
      setCostKey((k) => k + 1);
    } catch (e: any) {
      setProblem(e.message ?? String(e));
    } finally {
      setBusy(null);
    }
  }

  // A group already on Etsy: put this group's photos on its listing(s) instead,
  // in the order shown, and rewrite the listing's title and tags there.
  async function replaceOnEtsy(g: AssetGroup, pubs: Publication[]) {
    setConfirming(null);
    setBusy(g.key);
    setNotice(`Replacing the photos on Etsy for ${g.label}…`);
    try {
      const jobs = await Promise.all(
        pubs.map((p) => api.replaceImages(p.etsy_listing_id, id, p.connection_id, g.key)),
      );
      const done = await Promise.all(jobs.map((j) => waitForJob(j.job_id)));
      const failed = done.filter((d) => d && d.status === "failed");
      const waiting = done.find((d) => d?.pause);
      const pending = done.filter((d) => d === null).length;
      setNotice(
        failed.length
          ? `Replacing the photos failed: ${failed.map((d) => d?.error ?? "unknown error").join("; ")}`
          : waiting?.pause
            ? `Queued, not failed. ${waiting.pause.message}`
            : pending
              ? "Still replacing the photos on Etsy; this page updates when you come back."
              : `Photos, title and tags replaced on Etsy for ${g.label}.`,
      );
      await load();
    } catch (e: any) {
      setProblem(e.message ?? String(e));
    } finally {
      setBusy(null);
    }
  }

  if (error) return <div className="card p-4 text-sm text-rose-700">{error}</div>;
  if (!batch) return <p className="text-sm text-slate-400">Loading…</p>;

  const withContent = batch.assets.filter((a) => a.has_content).length;
  const anyBusy = busy !== null;
  const noProfiles = profiles.length === 0;
  // With one shop connected there is nothing to choose: it is that shop.
  const batchShop = batch.connection_id ?? (manyShops ? null : shopChoices[0]?.id ?? null);
  const batchShopName = shopChoices.find((sh) => sh.id === batchShop)?.name ?? batch.shop_name ?? null;
  const shopOf = (key: string) => settings[key]?.connection_id ?? batchShop;
  // The ticked groups' shop, when they share one: a profile can only be applied within one shop.
  const pickedShops = new Set(picked.map((k) => shopOf(k)));
  const pickedShop = pickedShops.size === 1 ? [...pickedShops][0] ?? null : null;

  // A group's profile picks the shop it is written for (v5 §E); each picker
  // searches by name, template and reference listing title (v7 §D1).

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/batches" className="tap inline-block text-sm text-slate-400 hover:text-slate-600 max-sm:py-2">
            ← Batches
          </Link>
          <h2 className="mt-1 max-w-xl font-display text-2xl text-slate-900">
            <BatchName batch={batch} onRenamed={(updated) => setBatch((cur) => (cur ? { ...cur, ...updated } : cur))} />
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <StatusPill status={batch.status} />
            {(batch.shop_names ?? []).map((name) => (
              <ShopBadge key={name} name={name} />
            ))}
          </div>
          <p className="mt-1 text-sm text-slate-500">
            <span><span>{groups.length}</span><span> listing group</span><Txt>{groups.length === 1 ? "" : "s"}</Txt><span> · </span><span>{batch.processed_count}</span>{" "}
            <span>processed · </span><span>{withContent}</span><span> with content · </span><span>{batch.approved_count}</span><span> approved</span></span>
          </p>
        </div>
        <Link href={`/batches/${id}/review`} className="btn-primary">
          Review listings
        </Link>
      </div>

      {/* Step 1: the shop. Step 2: that shop's profile and size charts, for all groups. */}
      <div className="card space-y-3 p-4">
        {noProfiles ? (
          <Link href="/profiles" className="text-sm text-brand-600 underline">
            Create a profile first →
          </Link>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <span className="text-sm font-medium text-slate-800">
                <span className="mr-1.5 inline-flex h-5 w-5 items-center justify-center rounded-full bg-slate-800 text-[11px] text-white">1</span>
                Shop for this batch
              </span>
              {manyShops ? (
                <ShopPicker shops={shopChoices} value={batchShop} onChange={chooseBatchShop} label="Shop for this batch" disabled={anyBusy} />
              ) : (
                <ShopBadge name={shopChoices[0]?.name ?? batch.shop_name} />
              )}
              {manyShops && !batchShop && switcherShop && (
                <button key="use" type="button" className="text-sm text-brand-700 underline" onClick={() => chooseBatchShop(switcherShop.id)}>
                  <span>Use <span translate="no">{switcherShop.name}</span></span>
                </button>
              )}
              {manyShops && (
                <span key="hint" className="text-xs text-slate-500">
                  {batchShop
                    ? "Groups start in this shop; any group can be given another below."
                    : "Nothing is assigned to a shop until you choose one."}
                </span>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-t border-slate-100 pt-3">
              <span className="text-sm font-medium text-slate-800">
                <span className="mr-1.5 inline-flex h-5 w-5 items-center justify-center rounded-full bg-slate-800 text-[11px] text-white">2</span>
                For all groups
              </span>
              <label className="flex items-center gap-2 text-sm text-slate-600">
                <span>Profile</span>
                <ProfilePicker
                  profiles={profiles}
                  shopId={batchShop}
                  shopName={batchShopName}
                  value={bulkProfileId || null}
                  emptyLabel="Choose…"
                  label="Profile for all groups"
                  onChange={(pid) => {
                    setBulkProfileId(pid ?? "");
                    if (pid) assign({ profile_id: pid });
                  }}
                />
              </label>
              <label className="flex items-center gap-2 text-sm text-slate-600">
                <span>Size charts</span>
                <ProfilePicker
                  profiles={profiles}
                  shopId={batchShop}
                  shopName={batchShopName}
                  value={null}
                  emptyLabel="Each group’s own"
                  label="Size charts for all groups"
                  onChange={(pid) => pid && assign({ size_chart_profile_id: pid })}
                />
              </label>
              <button className="btn-secondary sm:ml-auto" onClick={generateAll} disabled={anyBusy || noProfiles}>
                {busy === "__all__" ? "Generating all…" : "Generate content for all"}
              </button>
            </div>
          </>
        )}
      </div>

      {/* Bulk-apply: the same three choices for the ticked groups. */}
      {!noProfiles && groups.length > 1 && (
        <div key="bulk" className="card flex flex-wrap items-center gap-x-3 gap-y-2 p-3 text-sm">
          <label className="flex min-h-[2.75rem] items-center gap-2 text-slate-700">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={picked.length === groups.length}
              ref={(el) => {
                if (el) el.indeterminate = picked.length > 0 && picked.length < groups.length;
              }}
              onChange={(e) => setPicked(e.target.checked ? groups.map((g) => g.key) : [])}
            />
            <span translate="no">{picked.length ? `${picked.length} selected` : "Select groups"}</span>
          </label>
          {picked.length > 0 && (
            <>
              <span className="text-slate-400" aria-hidden>→</span>
              {manyShops && (
                <ShopPicker key="shop"
                  shops={shopChoices}
                  value={pickedShop}
                  emptyLabel={pickedShops.size > 1 ? "Several shops…" : "Choose a shop…"}
                  onChange={(shop) => assign({ group_keys: picked, connection_id: shop })}
                  label="Shop for the selected groups"
                  size="xs"
                />
              )}
              <ProfilePicker
                profiles={profiles}
                shopId={pickedShop}
                shopName={shopChoices.find((sh) => sh.id === pickedShop)?.name}
                value={null}
                emptyLabel="Profile…"
                label="Profile for the selected groups"
                size="xs"
                onChange={(pid) => pid && assign({ group_keys: picked, profile_id: pid })}
              />
              <ProfilePicker
                profiles={profiles}
                shopId={pickedShop}
                shopName={shopChoices.find((sh) => sh.id === pickedShop)?.name}
                value={null}
                emptyLabel="Size charts…"
                label="Size charts for the selected groups"
                size="xs"
                onChange={(pid) => pid && assign({ group_keys: picked, size_chart_profile_id: pid })}
              />
              {pickedShops.size > 1 && (
                <span key="mixed" className="text-xs text-amber-800">
                  The selected groups are in different shops: choose one shop for them, then a profile.
                </span>
              )}
              <button type="button" className="text-xs text-slate-500 underline" onClick={() => setPicked([])}>
                Clear
              </button>
            </>
          )}
        </div>
      )}

      {notice && (
        <div key="div-322-6" role="status" translate="no" className="card border-brand-100 bg-brand-50 p-3 text-sm text-brand-800">{notice}</div>
      )}
      {problem && (
        <div key="problem" role="alert" className="card flex items-start justify-between gap-3 border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          <span>{problem}</span>
          <button type="button" className="shrink-0 text-xs underline" onClick={() => setProblem(null)}>
            Dismiss
          </button>
        </div>
      )}

      {failures.length > 0 && (
        <div key="div-326-6" className="card border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          <p className="font-medium">Generation failures</p>
          <ul className="mt-1 space-y-1">
            {failures.map((f, i) => (
              <li key={i} className="break-words">
                <span className="font-mono text-xs">{f.original_filename}</span><span>: <span>{f.error}</span></span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Detected groups, each with its own profile + size-chart selection (§E) */}
      <div className="space-y-3">
        {groups.map((g) => {
          const s = settings[g.key];
          const mine = contents.filter((c) => g.assets.some((a) => a.id === c.asset_id));
          const approved = mine.some((c) => c.approved);
          const pubs = mine.flatMap((c) => c.publications);
          const asking = confirming?.key === g.key ? confirming.kind : null;
          return (
            <div key={g.key} className="card p-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  {groups.length > 1 && !noProfiles && (
                    <label key="pick" className="-m-2 flex cursor-pointer items-center p-2 max-sm:-m-3 max-sm:p-3">
                      <input
                        type="checkbox"
                        className="h-4 w-4"
                        aria-label={`Select ${g.label}`}
                        checked={picked.includes(g.key)}
                        onChange={(e) => setPicked((cur) => (e.target.checked ? [...cur, g.key] : cur.filter((k) => k !== g.key)))}
                      />
                    </label>
                  )}
                  <span className="rounded bg-slate-100 px-2 py-0.5 text-sm text-slate-600">
                    {g.label}
                  </span>
                  {g.sku && <span key="span-354-18" className="font-mono text-xs text-slate-500"><span>SKU <span>{g.sku}</span></span></span>}
                  <span className="text-xs text-slate-400">
                    <span><span>{g.assets.length}</span><span> image</span><Txt>{g.assets.length === 1 ? "" : "s"}</Txt></span>
                  </span>
                  {g.done && pubs.length === 0 && (
                    <span key="span-358-18" className="text-xs text-emerald-600">
                      <span><span>✓ content ready</span><Txt>{approved ? " · approved" : ""}</Txt></span>
                    </span>
                  )}
                  {pubs.length > 0 && (
                    <span key="span-363-18" className="text-xs text-emerald-700">
                      <span><span>✓ on Etsy</span><Txt>{pubs.length > 1 ? ` in ${pubs.length} shops` : ""}</Txt></span>
                    </span>
                  )}
                </div>
                {pubs.length > 0 ? (
                  <button
                    className="btn-secondary py-1 text-xs"
                    onClick={() => setConfirming({ key: g.key, kind: "replace" })}
                    disabled={anyBusy}
                    title="Put this group's photos on the Etsy listing"
                  >
                    {busy === g.key ? "Replacing…" : "Replace images on Etsy"}
                  </button>
                ) : (
                  <button
                    className="btn-secondary py-1 text-xs"
                    onClick={() =>
                      !g.done
                        ? generate(g.key)
                        : approved
                          ? setConfirming({ key: g.key, kind: "regenerate" })
                          : regenerate(g, false)
                    }
                    disabled={anyBusy || noProfiles}
                  >
                    {busy === g.key ? "Generating…" : g.done ? "Regenerate" : "Generate"}
                  </button>
                )}
              </div>

              {asking && (
                <div key="div-395-14" role="alertdialog" className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                  {asking === "regenerate" ? (
                    <p>
                      This group&apos;s content is approved. Regenerating writes a new title, tags and
                      description (one more AI generation), replaces the approved text, and the new
                      one needs approving again.
                    </p>
                  ) : (
                    <p>
                      <span><span>This listing is already on Etsy</span>
                      <Txt>{pubs.length > 1 ? ` in ${pubs.length} shops` : ""}</Txt><span>. Its photos will be replaced
                      with this group&apos;s, in the order shown, and its title and tags rewritten
                      (one more AI generation). Its category, price, variations and whether it is live
                      stay as they are.</span></span>
                    </p>
                  )}
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      className="btn-primary px-2.5 py-1 text-xs"
                      onClick={() => (asking === "regenerate" ? regenerate(g, true) : replaceOnEtsy(g, pubs))}
                    >
                      {asking === "regenerate" ? "Replace the approved content" : "Replace on Etsy"}
                    </button>
                    <button type="button" className="text-amber-900 underline" onClick={() => setConfirming(null)}>
                      Keep it
                    </button>
                  </div>
                </div>
              )}

              {!noProfiles && (
                <div key="choices" className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
                  {manyShops && (
                    <label key="shop" className="flex items-center gap-1.5 text-slate-500">
                      <span>Shop</span>
                      <ShopPicker
                        shops={shopChoices}
                        value={shopOf(g.key)}
                        onChange={(shop) => assign({ group_key: g.key, connection_id: shop })}
                        label={`Shop for ${g.label}`}
                        size="xs"
                        disabled={anyBusy}
                      />
                    </label>
                  )}
                  <label className="flex items-center gap-1.5 text-slate-500">
                    <span>Profile</span>
                    <ProfilePicker
                      profiles={profiles}
                      shopId={shopOf(g.key)}
                      shopName={s?.shop_name ?? batchShopName}
                      value={s?.profile_id ?? null}
                      emptyLabel="Choose…"
                      label={`Profile for ${g.label}`}
                      size="xs"
                      onChange={(pid) => assign({ group_key: g.key, profile_id: pid })}
                    />
                  </label>
                  <label className="flex items-center gap-1.5 text-slate-500">
                    <span>Size charts</span>
                    <ProfilePicker
                      profiles={profiles}
                      shopId={shopOf(g.key)}
                      shopName={s?.shop_name ?? batchShopName}
                      value={s?.size_chart_profile_id ?? null}
                      emptyLabel="Own profile"
                      label={`Size charts for ${g.label}`}
                      size="xs"
                      onChange={(pid) => assign({ group_key: g.key, size_chart_profile_id: pid })}
                    />
                  </label>
                  <span className="text-slate-400">
                    {s?.manual ? "set here" : s?.profile_id ? "carried from above" : shopOf(g.key) ? "choose a profile" : "choose a shop"}
                  </span>
                </div>
              )}
              {patternsOn && (
                <PatternPicker key="patternpicker-460-14"
                  chosen={s?.pattern_listing_id ?? null}
                  listings={patternListings}
                  busy={anyBusy}
                  onChoose={async (listingId) => {
                    try {
                      const gs = await api.setGroupPattern(id, g.key, listingId);
                      setSettings(Object.fromEntries(gs.map((x) => [x.group_key, x])));
                    } catch (e: any) {
                      setProblem(e.message ?? String(e));
                    }
                  }}
                />
              )}

              <GroupImages
                batchId={id}
                groupKey={g.key}
                assets={g.assets}
                onSaved={setBatch}
                listingsOnEtsy={pubs.length}
                hasContent={g.done}
                onDeleted={(result) => {
                  if (result.group_removed) {
                    setPicked((cur) => cur.filter((k) => k !== g.key));
                    setNotice(
                      `${g.label} had no images left, so the group was removed` +
                        (g.done ? ", with the listing written for it." : ".") +
                        (result.listings_on_etsy ? " What it already has on Etsy was not touched." : ""),
                    );
                    load();
                    loadGroups();
                    setCostKey((k) => k + 1);
                  } else if (result.listings_on_etsy) {
                    setNotice(`Image deleted from ${g.label}. Its listing on Etsy still has the photo; use “Replace images on Etsy” to update it there.`);
                  }
                }}
              />
            </div>
          );
        })}
      </div>

      <CostPanel batchId={id} refreshKey={costKey} />
    </div>
  );
}
