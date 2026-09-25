"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Asset, BatchDetail, Content, Group, Profile, Publication } from "@/lib/types";
import { waitForJob } from "@/lib/jobs";
import { StatusPill } from "@/components/StatusPill";
import { CostPanel } from "@/components/CostPanel";
import { GroupImages } from "@/components/GroupImages";

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
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // group key, or "__all__"
  const [notice, setNotice] = useState<string | null>(null);
  const [failures, setFailures] = useState<{ original_filename: string; error: string }[]>([]);
  const [costKey, setCostKey] = useState(0);
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

  // Persist a group assignment (single group -> manual; no key -> bulk to all).
  async function assign(body: {
    group_key?: string | null;
    profile_id?: string | null;
    size_chart_profile_id?: string | null;
  }) {
    try {
      const gs = await api.assignGroup(id, body);
      setSettings(Object.fromEntries(gs.map((g) => [g.group_key, g])));
    } catch (e: any) {
      setNotice(e.message ?? String(e));
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
    setFailures([]);
    try {
      const res = await api.generate(id, bulkProfileId || undefined, groupKey);
      setNotice(`Generated ${res.generated}, failed ${res.failed}, skipped ${res.skipped}.`);
      setFailures(res.failures);
      await load();
      setCostKey((k) => k + 1);
    } catch (e: any) {
      setNotice(e.message ?? String(e));
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
      setNotice(e.message ?? String(e));
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
      setNotice(e.message ?? String(e));
    } finally {
      setBusy(null);
    }
  }

  if (error) return <div className="card p-4 text-sm text-rose-700">{error}</div>;
  if (!batch) return <p className="text-sm text-slate-400">Loading…</p>;

  const withContent = batch.assets.filter((a) => a.has_content).length;
  const anyBusy = busy !== null;
  const noProfiles = profiles.length === 0;

  // A group's profile picks the shop it is written for (v5 §E), so with several
  // shops the choices are grouped by shop.
  const byShop = new Map<string, typeof profiles>();
  for (const p of profiles) {
    const shop = p.shop_name ?? "Shop";
    byShop.set(shop, [...(byShop.get(shop) ?? []), p]);
  }
  const profileOptions = (empty: string) => (
    <>
      <option value="">{empty}</option>
      {byShop.size > 1
        ? Array.from(byShop, ([shop, list]) => (
            <optgroup key={shop} label={shop}>
              {list.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </optgroup>
          ))
        : profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
    </>
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link href="/" className="text-sm text-slate-400 hover:text-slate-600">
            ← Batches
          </Link>
          <div className="mt-1 flex items-center gap-2">
            <StatusPill status={batch.status} />
          </div>
          <p className="mt-1 text-sm text-slate-500">
            {groups.length} listing group{groups.length === 1 ? "" : "s"} · {batch.processed_count}{" "}
            processed · {withContent} with content · {batch.approved_count} approved
          </p>
        </div>
        <Link href={`/batches/${id}/review`} className="btn-primary">
          Review listings
        </Link>
      </div>

      {/* Bulk selectors (apply to all groups) + Generate all */}
      <div className="card flex flex-wrap items-center justify-between gap-3 p-4">
        {noProfiles ? (
          <Link href="/profiles" className="text-sm text-brand-600 underline">
            Create a profile first →
          </Link>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <label className="text-sm text-slate-600">Profile (all)</label>
            <select
              className="field w-auto py-1 text-sm"
              value={bulkProfileId}
              onChange={(e) => {
                setBulkProfileId(e.target.value);
                if (e.target.value) assign({ profile_id: e.target.value });
              }}
            >
              {profileOptions("Choose…")}
            </select>
            <label className="ml-3 text-sm text-slate-600">Size charts (all)</label>
            <select
              className="field w-auto py-1 text-sm"
              defaultValue=""
              onChange={(e) => e.target.value && assign({ size_chart_profile_id: e.target.value })}
            >
              {profileOptions("Each group’s own")}
            </select>
          </div>
        )}
        <button className="btn-secondary" onClick={generateAll} disabled={anyBusy || noProfiles}>
          {busy === "__all__" ? "Generating all…" : "Generate content for all"}
        </button>
      </div>

      {notice && (
        <div className="card border-brand-100 bg-brand-50 p-3 text-sm text-brand-800">{notice}</div>
      )}

      {failures.length > 0 && (
        <div className="card border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
          <p className="font-medium">Generation failures</p>
          <ul className="mt-1 space-y-1">
            {failures.map((f, i) => (
              <li key={i} className="break-words">
                <span className="font-mono text-xs">{f.original_filename}</span>: {f.error}
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
                <div className="flex items-center gap-2">
                  <span className="rounded bg-slate-100 px-2 py-0.5 text-sm text-slate-600">
                    {g.label}
                  </span>
                  {g.sku && <span className="font-mono text-xs text-slate-500">SKU {g.sku}</span>}
                  <span className="text-xs text-slate-400">
                    {g.assets.length} image{g.assets.length === 1 ? "" : "s"}
                  </span>
                  {g.done && pubs.length === 0 && (
                    <span className="text-xs text-emerald-600">
                      ✓ content ready{approved ? " · approved" : ""}
                    </span>
                  )}
                  {pubs.length > 0 && (
                    <span className="text-xs text-emerald-700">
                      ✓ on Etsy{pubs.length > 1 ? ` in ${pubs.length} shops` : ""}
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
                <div role="alertdialog" className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                  {asking === "regenerate" ? (
                    <p>
                      This group&apos;s content is approved. Regenerating writes a new title, tags and
                      description (one more AI generation), replaces the approved text, and the new
                      one needs approving again.
                    </p>
                  ) : (
                    <p>
                      This listing is already on Etsy
                      {pubs.length > 1 ? ` in ${pubs.length} shops` : ""}. Its photos will be replaced
                      with this group&apos;s, in the order shown, and its title and tags rewritten
                      (one more AI generation). Its category, price, variations and whether it is live
                      stay as they are.
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
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
                  <label className="text-slate-500">Profile</label>
                  <select
                    className="field w-auto py-1 text-xs"
                    value={s?.profile_id ?? ""}
                    onChange={(e) =>
                      assign({
                        group_key: g.key,
                        profile_id: e.target.value || null,
                        size_chart_profile_id: s?.size_chart_profile_id ?? null,
                      })
                    }
                  >
                    {profileOptions("Choose…")}
                  </select>
                  <label className="ml-2 text-slate-500">Size charts</label>
                  <select
                    className="field w-auto py-1 text-xs"
                    value={s?.size_chart_profile_id ?? ""}
                    onChange={(e) =>
                      assign({
                        group_key: g.key,
                        profile_id: s?.profile_id ?? null,
                        size_chart_profile_id: e.target.value || null,
                      })
                    }
                  >
                    {profileOptions("Own profile")}
                  </select>
                  {s?.manual && <span className="text-slate-400">· set manually</span>}
                </div>
              )}

              <GroupImages batchId={id} groupKey={g.key} assets={g.assets} onSaved={setBatch} />
            </div>
          );
        })}
      </div>

      <CostPanel batchId={id} refreshKey={costKey} />
    </div>
  );
}
