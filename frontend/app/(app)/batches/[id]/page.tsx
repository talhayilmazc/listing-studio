"use client";

import { useParams } from "next/navigation";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, uploadAsset } from "@/lib/api";
import { isUnsorted, rememberChoice, uploadKind, type GroupingMode } from "@/lib/grouping";
import { GroupingResult } from "@/components/GroupingChoice";
import { SkuEditor } from "@/components/SkuEditor";
import type { Asset, BatchDetail, Content, Group, Profile, Publication } from "@/lib/types";
import { waitForJob } from "@/lib/jobs";
import { BatchStatusBadge } from "@/components/BatchStatusBadge";
import { ProfilePicker } from "@/components/ProfilePicker";
import { BatchName } from "@/components/BatchName";
import { PatternPicker, usePatternListings } from "@/components/PatternPicker";
import { useSession } from "@/components/SessionProvider";
import { ASSET_DRAG, GroupImages } from "@/components/GroupImages";
import { ReplaceChoice } from "@/components/ReplaceChoice";
import { replacedNotice, type ReplaceMode } from "@/lib/replaceModes";
import { imageLimit, leftOutNotice, limitNotice } from "@/lib/chartOrder";

import { Txt } from "@/components/Txt";
interface AssetGroup {
  key: string;
  label: string;
  sku: string | null;
  assets: Asset[];
  done: boolean;
}

// Next 15: a page's params arrive as a Promise; a client page reads them with useParams.
export default function BatchPage() {
  const { id } = useParams<{ id: string }>();
  const [batch, setBatch] = useState<BatchDetail | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [settings, setSettings] = useState<Record<string, Group>>({});
  const [bulkProfileId, setBulkProfileId] = useState<string>("");
  // No batch or group shop (B3): a listing is written with its profile; where it goes is
  // chosen on the review page ("Where and when").
  // Groups ticked for bulk-apply.
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // group key, or "__all__"
  const [notice, setNotice] = useState<string | null>(null);
  // Refusals and errors (e.g. the allowance is used up): shown as a warning, not a notice.
  const [problem, setProblem] = useState<string | null>(null);
  const [failures, setFailures] = useState<{ original_filename: string; error: string }[]>([]);
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
      if (isUnsorted(a.group_key)) continue; // the Unsorted tray is not a listing group
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

  const unsorted = useMemo(() => (batch ? batch.assets.filter((a) => isUnsorted(a.group_key)) : []), [batch]);
  // Writing does not start while photos are unsorted, unless the seller ignores them.
  const [ignoreUnsorted, setIgnoreUnsorted] = useState(false);
  const blockedByUnsorted = unsorted.length > 0 && !ignoreUnsorted;
  // Until anything is written, the photos can be turned into listings another way.
  const uploaded = useMemo(
    () => (batch ? batch.assets.map((a) => ({ folder: a.upload_folder ?? "", name: a.original_filename })) : []),
    [batch],
  );
  const [regrouping, setRegrouping] = useState(false);
  async function regroup(mode: GroupingMode) {
    setRegrouping(true);
    rememberChoice(uploadKind(uploaded), mode);
    try {
      await api.regroup(id, mode);
      await load();
      await loadGroups();
    } catch (e: any) {
      setProblem(String(e.message ?? e));
    } finally {
      setRegrouping(false);
    }
  }

  // Persist a choice: for one group (the unset groups after it take the same),
  // for the ticked groups, or for every group not set by hand. Only the fields
  // sent change.
  async function assign(body: Parameters<typeof api.assignGroup>[1]) {
    try {
      setProblem(null);
      const gs = await api.assignGroup(id, body);
      setSettings(Object.fromEntries(gs.map((g) => [g.group_key, g])));
      // A listing already written takes the profile chosen for its group.
      const keys = body.group_key != null ? [body.group_key] : body.group_keys ?? [];
      const moved = body.profile_id ? groups.filter((g) => keys.includes(g.key) && g.done) : [];
      if (moved.length) {
        const name = profiles.find((p) => p.id === body.profile_id)?.name ?? "the new profile";
        setNotice(
          `${moved.map((g) => g.label).join(", ")} now ${moved.length === 1 ? "uses" : "use"} ${name}: price, variations, shipping, ` +
            "returns and the description come from it, and the title has its prefix. No AI was used. Approve again on the review page.",
        );
        await load();
      }
    } catch (e: any) {
      setProblem(e.message ?? String(e));
    }
  }

  // Arranged in place (B3): a photo dragged onto another listing's card moves there; onto
  // "+ New listing", it starts one; photos from the computer go into the card they are
  // dropped on (or its "+").
  const [arranging, setArranging] = useState(false);
  const [overCard, setOverCard] = useState<string | null>(null);
  const filePicker = useRef<HTMLInputElement>(null);
  const [pickInto, setPickInto] = useState<string | null | undefined>(undefined);
  function newListingName(): string {
    const taken = new Set((batch?.assets ?? []).map((a) => a.group_key ?? ""));
    for (let n = 1; ; n += 1) {
      const name = n === 1 ? "New listing" : `New listing ${n}`;
      if (!taken.has(name)) return name;
    }
  }
  async function moveInto(assetIds: string[], to: string | null) {
    if (!assetIds.length) return;
    setArranging(true);
    setProblem(null);
    try {
      const b = to === null ? await api.boardMove(id, assetIds, newListingName(), true) : await api.boardMove(id, assetIds, to);
      setBatch(b);
      await load();
      await loadGroups();
    } catch (e: any) {
      setProblem(e.message ?? String(e));
    } finally {
      setArranging(false);
    }
  }
  async function addFilesInto(files: File[], to: string | null) {
    const images = files.filter((f) => /\.(png|jpe?g|webp|gif|tiff?)$/i.test(f.name));
    if (!images.length) return;
    setArranging(true);
    setProblem(null);
    try {
      const made: string[] = [];
      for (const file of images) made.push((await uploadAsset(id, file, () => {}, "")).id);
      await moveInto(made, to);
    } catch (e: any) {
      setProblem(e.message ?? String(e));
      setArranging(false);
    }
  }
  const dropTarget = (to: string | null) => ({
    onDragOver: (e: React.DragEvent) => {
      e.preventDefault();
      setOverCard(to ?? "+new");
    },
    onDragLeave: () => setOverCard(null),
    onDrop: (e: React.DragEvent) => {
      e.preventDefault();
      setOverCard(null);
      const moved = e.dataTransfer.getData(ASSET_DRAG);
      if (moved) {
        const [assetId, from] = moved.split("|");
        if (from !== to) moveInto([assetId], to); // within one card the strip reorders it
        return;
      }
      if (e.dataTransfer.files.length) addFilesInto(Array.from(e.dataTransfer.files), to);
    },
  });
  const pickFiles = (to: string | null) => {
    setPickInto(to);
    filePicker.current?.click();
  };

  // "Write title and tags myself": an empty listing with everything else from the
  // group's profile. No AI is called; it counts as one listing, like any other.
  async function writeMyself(groupKey?: string) {
    if (blockedByUnsorted) {
      setProblem(unsortedMessage(unsorted.length));
      return;
    }
    setBusy(groupKey ?? "__all__");
    setNotice(null);
    setProblem(null);
    setFailures([]);
    try {
      const res = await api.writeManually(id, groupKey, { ignoreUnsorted });
      const why = (res.skipped_groups ?? []).map((s) => s.reason).join("; ");
      setNotice(
        res.generated
          ? `${res.generated} listing${res.generated === 1 ? "" : "s"} ready for you to write: open Review listings to type the title and tags.` +
              (res.failed ? ` ${res.failed} could not be started.` : "")
          : why || (res.failed ? "Nothing could be started." : "Every group is already written."),
      );
      setFailures(res.failures);
      await load();
      await loadGroups();
    } catch (e: any) {
      setProblem(e.message ?? String(e));
    } finally {
      setBusy(null);
    }
  }

  // "Generate content for all", one group per request (docs/duzeltmeler-v6.md §A3).
  // A single request for the whole batch outlived the proxy's timeout on large
  // batches: the page showed an error and never refreshed the cost panel while
  // the server kept generating. Group by group, every result and the cost land
  // as they happen.
  async function generateAll() {
    if (blockedByUnsorted) {
      setProblem(unsortedMessage(unsorted.length));
      return;
    }
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
        const res = await api.generate(id, bulkProfileId || undefined, g.key, { ignoreUnsorted });
        if (res.paused) {
          // Paused on our side: stop asking. Nothing failed; the rest simply wait.
          setProblem(res.paused);
          break;
        }
        generated += res.generated;
        failed += res.failed;
        skipped += res.skipped;
        allFailures.push(...res.failures);
      } catch (e: any) {
        if (e?.status === 409 && /another tab or window/.test(e.message ?? "")) {
          // Being written elsewhere (another tab): stop here; nothing failed.
          setProblem(e.message);
          break;
        }
        failed += 1;
        allFailures.push({ original_filename: g.label, error: e.message ?? String(e) });
      }
      setFailures([...allFailures]);
      await load();
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
      const res = await api.generate(id, bulkProfileId || undefined, groupKey, { ignoreUnsorted });
      if (res.paused) {
        setProblem(res.paused);
        return;
      }
      setNotice(`Generated ${res.generated}, failed ${res.failed}, skipped ${res.skipped}.`);
      setFailures(res.failures);
      await load();
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
        ignoreUnsorted,
      });
      if (res.paused) {
        setProblem(res.paused);
        return;
      }
      const why = (res.skipped_groups ?? []).map((s) => s.reason).join("; ");
      setNotice(
        res.generated
          ? `New content written for ${g.label}${approved ? "; approve it again on the review page" : ""}.`
          : why || `Nothing regenerated for ${g.label}.`,
      );
      setFailures(res.failures);
      await load();
    } catch (e: any) {
      setProblem(e.message ?? String(e));
    } finally {
      setBusy(null);
    }
  }

  // A group already on Etsy: put this group's photos on its listing(s) instead,
  // in the order shown. "photos" stops there; "full" also rewrites the listing's
  // title and tags (one listing generated).
  async function replaceOnEtsy(g: AssetGroup, pubs: Publication[], mode: ReplaceMode) {
    setConfirming(null);
    setBusy(g.key);
    setNotice(`Replacing the photos on Etsy for ${g.label}…`);
    try {
      const jobs = await Promise.all(
        pubs.map((p) => api.replaceImages(p.etsy_listing_id, id, mode, p.connection_id, g.key)),
      );
      const done = await Promise.all(jobs.map((j) => waitForJob(j.job_id)));
      // More photos than Etsy keeps beside the size charts: say which were left out.
      const over = done.find((d) => d?.left_out?.length)?.left_out;
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
              : [replacedNotice(mode, g.label), leftOutNotice(over)].filter(Boolean).join(" "),
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
  // Each picker searches by name, template and reference listing title (v7 §D1). Profiles
  // are the account's: no shop is asked here (B3).

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
            <BatchStatusBadge statuses={batch.statuses} main={batch.headline} notes={batch.status_notes} />
          </div>
          <p className="mt-1 text-sm text-slate-500">
            <span><span>{groups.length}</span><span> listing group</span><Txt>{groups.length === 1 ? "" : "s"}</Txt><span> · </span><span>{batch.processed_count}</span>{" "}
            <span>processed · </span><span>{withContent}</span><span> with content · </span><span>{batch.approved_count}</span><span> approved</span></span>
          </p>
        </div>
        {/* One primary button (B9): Generate while groups are unwritten, then Review. */}
        <Link href={`/batches/${id}/review`} className={groups.length > 0 && groups.every((g) => g.done) ? "btn-primary" : "btn-secondary"}>
          Review listings
        </Link>
      </div>

      {/* Asked once, on the upload page (B2): here the result, with "change" until anything is written. */}
      {batch.grouping_mode && uploaded.length > 0 ? (
        <GroupingResult
          key="grouping"
          files={uploaded}
          value={batch.grouping_mode as GroupingMode}
          onChange={regroup}
          regroupable={!!batch.regroupable}
          disabled={regrouping}
        />
      ) : batch.grouping ? (
        <p key="grouping" className="text-sm text-slate-600" translate="no">
          <span>{batch.grouping}</span>
        </p>
      ) : null}
      {unsorted.length > 0 && (
        <div key="unsorted" className={"rounded-lg border px-4 py-3 text-sm " + (ignoreUnsorted ? "border-slate-200 bg-slate-50 text-slate-600" : "border-amber-200 bg-amber-50 text-amber-900")}>
          <p>
            <span>{unsortedMessage(unsorted.length)}</span>
          </p>
          <p className="mt-1 text-xs" translate="no">
            <span>{unsorted.slice(0, 8).map((a) => a.original_filename).join(", ")}</span>
            <Txt>{unsorted.length > 8 ? ` and ${unsorted.length - 8} more` : ""}</Txt>
          </p>
          <label className="tap mt-2 inline-flex items-center gap-2 text-sm">
            <input type="checkbox" checked={ignoreUnsorted} onChange={(e) => setIgnoreUnsorted(e.target.checked)} />
            <span>Ignore unsorted: write the groups without these photos</span>
          </label>
        </div>
      )}

      {/* One row for every listing: the profile, size charts, and the two ways to write (B3). */}
      <div className="card p-4" data-testid="batch-top-row">
        {noProfiles ? (
          <Link href="/profiles" className="text-sm text-brand-600 underline">
            Create a profile first →
          </Link>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <label className="flex items-center gap-2 text-sm text-slate-600">
                <span>Profile for all</span>
                <ProfilePicker
                  profiles={profiles}
                  shopId={null}
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
                  shopId={null}
                  value={null}
                  emptyLabel="Each listing’s own"
                  label="Size charts for all groups"
                  onChange={(pid) => pid && assign({ size_chart_profile_id: pid })}
                />
              </label>
              <span className="flex flex-wrap items-center gap-2 sm:ml-auto">
                <button className={groups.some((g) => !g.done) ? "btn-primary" : "btn-secondary"} onClick={generateAll}
                  disabled={anyBusy || noProfiles} data-testid="generate-all">
                  {busy === "__all__" ? "Generating all…" : "Generate all"}
                </button>
                {groups.some((g) => !g.done) && (
                  <button key="manual-all" className="btn-secondary" onClick={() => writeMyself()} disabled={anyBusy || noProfiles}
                    title="No AI: you type each title and its tags; everything else comes from the listing's profile" data-testid="manual-all">
                    Write them myself
                  </button>
                )}
              </span>
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
              <ProfilePicker
                profiles={profiles}
                shopId={null}
                value={null}
                emptyLabel="Profile…"
                label="Profile for the selected groups"
                size="xs"
                onChange={(pid) => pid && assign({ group_keys: picked, profile_id: pid })}
              />
              <ProfilePicker
                profiles={profiles}
                shopId={null}
                value={null}
                emptyLabel="Size charts…"
                label="Size charts for the selected groups"
                size="xs"
                onChange={(pid) => pid && assign({ group_keys: picked, size_chart_profile_id: pid })}
              />
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
            <div key={g.key} id={`group-${g.key}`} {...dropTarget(g.key)} data-testid="group-card"
              className={"card scroll-mt-4 p-3 target:ring-2 target:ring-brand-500/50 " + (overCard === g.key ? "ring-2 ring-brand-500" : "")}>
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
                  <SkuEditor
                    key="sku"
                    compact
                    value={g.sku}
                    contentId={mine[0]?.id ?? null}
                    save={(sku) => api.setGroupSku(id, g.key, sku)}
                    onSaved={() => {
                      load();
                      loadGroups();
                    }}
                  />
                  <span className="text-xs text-slate-400">
                    <span><span>{g.assets.length}</span><span> image</span><Txt>{g.assets.length === 1 ? "" : "s"}</Txt></span>
                  </span>
                  <button type="button" className="btn-secondary h-8 w-8 p-0 text-base" aria-label={`Add photos to ${g.label}`}
                    onClick={() => pickFiles(g.key)} disabled={arranging} data-testid="add-to-group">+</button>
                  {g.done && pubs.length === 0 && (
                    <span key="span-358-18" className="text-xs text-emerald-600">
                      <span><span>{s?.source === "manual" ? "✓ written by you" : "✓ content ready"}</span><Txt>{approved ? " · approved" : ""}</Txt></span>
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
                  <span className="flex flex-wrap items-center justify-end gap-2">
                    {!g.done && (
                      <button
                        key="manual"
                        className="tap text-xs text-brand-700 underline"
                        onClick={() => writeMyself(g.key)}
                        disabled={anyBusy || noProfiles}
                        title="No AI: you type the title and the 13 tags; everything else comes from this group's profile"
                        data-testid="manual-one"
                      >
                        Write title and tags myself
                      </button>
                    )}
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
                      data-testid="generate-one"
                    >
                      {busy === g.key ? "Working…" : !g.done ? "Generate" : s?.source === "manual" ? "Generate with AI" : "Regenerate"}
                    </button>
                  </span>
                )}
              </div>

              {mine.some((c) => c.written_from_asset_id && c.written_from_asset_id !== c.asset_id) && (
                <div key="cover-changed" role="status" className="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                  <span>
                    <span>The cover changed. The title and tags were written from </span>
                    <span translate="no">{writtenFromName(mine, batch.assets)}</span>
                    <span>, not from the current cover.</span>
                    {pubs.length > 0 ? (
                      <span key="etsy"> “Replace images on Etsy” with new title and tags rewrites them from the new cover.</span>
                    ) : (
                      <span key="regen"> Regenerate to write them from the new cover (a rewrite: free up to 3 times for a design).</span>
                    )}
                  </span>
                  {pubs.length === 0 && (
                    <button
                      key="regen-btn"
                      type="button"
                      className="btn-secondary px-3 py-1.5 text-xs"
                      disabled={anyBusy || noProfiles}
                      onClick={() => (approved ? setConfirming({ key: g.key, kind: "regenerate" }) : regenerate(g, false))}
                    >
                      Regenerate
                    </button>
                  )}
                </div>
              )}
              {asking === "regenerate" && (
                <div key="div-395-14" role="alertdialog" className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                  <p>
                    This group&apos;s content is approved. Regenerating writes a new title, tags and
                    description (one more AI generation), replaces the approved text, and the new
                    one needs approving again.
                  </p>
                  <div className="mt-2 flex gap-2">
                    <button type="button" className="btn-primary px-2.5 py-1 text-xs" onClick={() => regenerate(g, true)}>
                      Replace the approved content
                    </button>
                    <button type="button" className="text-amber-900 underline" onClick={() => setConfirming(null)}>
                      Keep it
                    </button>
                  </div>
                </div>
              )}
              {asking === "replace" && (
                <div key="replace-choice" role="alertdialog" aria-label="Replace images on Etsy" className="mt-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-3">
                  <ReplaceChoice
                    intro={
                      pubs.length > 1
                        ? `This listing is already on Etsy in ${pubs.length} shops. Choose what to replace there; the same choice applies in each shop.`
                        : "This listing is already on Etsy. Choose what to replace there."
                    }
                    photos="this group's, in the order shown"
                    notice={(() => {
                      // The group's photos as they are now against its size charts.
                      const over = imageLimit(g.assets, s?.size_charts?.length ?? 0);
                      return over ? limitNotice(over) : null;
                    })()}
                    confirmLabel={(m) => (m === "photos" ? "Replace the photos on Etsy" : "Replace photos, title and tags on Etsy")}
                    onConfirm={(m) => replaceOnEtsy(g, pubs, m)}
                    onCancel={() => setConfirming(null)}
                  />
                </div>
              )}

              {!noProfiles && (
                <div key="choices" className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
                  <label className="flex items-center gap-1.5 text-slate-500">
                    <span>Profile</span>
                    <ProfilePicker
                      profiles={profiles}
                      shopId={null}
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
                      shopId={null}
                      value={s?.size_chart_profile_id ?? null}
                      emptyLabel="Own profile"
                      label={`Size charts for ${g.label}`}
                      size="xs"
                      onChange={(pid) => assign({ group_key: g.key, size_chart_profile_id: pid })}
                    />
                  </label>
                  <span className="text-slate-400">
                    {s?.manual ? "set here" : s?.profile_id ? "carried from above" : "choose a profile"}
                  </span>
                </div>
              )}
              {g.done && s?.profile_id && s.listing_profile_id && s.listing_profile_id !== s.profile_id && (
                <p key="profile-differs" role="status" className="mt-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" data-testid="profile-differs">
                  <span>This listing is built with </span>
                  <span className="font-medium" translate="no">{s.listing_profile_name ?? "another profile"}</span>
                  <span>, not the profile shown here. </span>
                  {pubs.length === 0 ? (
                    <button key="apply" type="button" className="tap text-brand-700 underline" disabled={anyBusy}
                      onClick={() => assign({ group_key: g.key, profile_id: s.profile_id })}>
                      Use the profile shown here for it
                    </button>
                  ) : (
                    <span key="etsy">It is already on Etsy, so it keeps that profile.</span>
                  )}
                </p>
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
                charts={s?.size_charts ?? []}
                chartSlots={s?.chart_slots ?? []}
                chartCustom={s?.chart_slots_custom ?? false}
                chartProfileName={s?.chart_profile_name}
                chartListingId={s?.chart_listing_id}
                onChartSlots={async (placement) => {
                  const gs = await api.setChartSlots(id, g.key, placement);
                  setSettings(Object.fromEntries(gs.map((x) => [x.group_key, x])));
                }}
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
                  } else if (result.listings_on_etsy) {
                    setNotice(`Image deleted from ${g.label}. Its listing on Etsy still has the photo; use “Replace images on Etsy” to update it there.`);
                  }
                }}
              />
            </div>
          );
        })}

        {/* Photos no listing takes yet: drag them onto a listing, or onto "+ New listing". */}
        {unsorted.length > 0 && (
          <div key="unsorted-tiles" className="card p-3" data-testid="unsorted-tray">
            <p className="text-sm font-medium text-slate-800">Unsorted photos</p>
            <ul className="mt-2 flex flex-wrap gap-2">
              {unsorted.map((a) => (
                <li key={a.id} draggable onDragStart={(e) => e.dataTransfer.setData(ASSET_DRAG, `${a.id}|${a.group_key ?? ""}`)}
                  className="relative h-20 w-20 cursor-grab overflow-hidden rounded bg-slate-100" title={a.original_filename}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={api.assetImage(a.id, 112)} alt={a.original_filename} className="h-full w-full object-cover" draggable={false} loading="lazy" />
                  <select aria-label={`Move ${a.original_filename} to a listing`} value="" disabled={arranging}
                    className="absolute inset-x-0 bottom-0 h-6 w-full appearance-none bg-slate-900/55 px-1 text-[10px] text-white"
                    onChange={(e) => e.target.value && moveInto([a.id], e.target.value === "+new" ? null : e.target.value.slice(1))}>
                    <option value="">Move to…</option>
                    {groups.map((g) => <option key={g.key} value={`=${g.key}`}>{g.label}</option>)}
                    <option value="+new">New listing</option>
                  </select>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* "+ New listing": drop photos (from a card or the computer) here, or click to add. */}
        <button type="button" {...dropTarget(null)} onClick={() => pickFiles(null)} disabled={arranging}
          className={"flex w-full flex-col items-center justify-center rounded-xl border-2 border-dashed py-6 text-sm font-medium text-slate-600 " +
            (overCard === "+new" ? "border-brand-600 bg-brand-50" : "border-slate-300 bg-white")}
          data-testid="batch-new-listing">
          <span className="text-2xl" aria-hidden>+</span>
          <span>New listing</span>
          <span className="mt-0.5 text-xs font-normal text-slate-500">Drop photos here, or click to add</span>
        </button>
        <input ref={filePicker} type="file" multiple accept="image/png,image/jpeg,image/webp,image/gif,image/tiff" className="hidden"
          data-testid="group-files-input"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            e.target.value = "";
            if (files.length && pickInto !== undefined) addFilesInto(files, pickInto);
          }} />
      </div>

    </div>
  );
}

/** Why writing waits, in the same words the server uses. */
/** The photo a group's text was written from: its file name, or "a photo that was deleted". */
function writtenFromName(mine: Content[], assets: Asset[]): string {
  const from = mine.find((c) => c.written_from_asset_id && c.written_from_asset_id !== c.asset_id)?.written_from_asset_id;
  return assets.find((a) => a.id === from)?.original_filename ?? "a photo that was deleted";
}

function unsortedMessage(n: number): string {
  return `${n} photo${n === 1 ? " is" : "s are"} still unsorted: no SKU could be read from ${n === 1 ? "its name" : "their names"}. Move ${n === 1 ? "it" : "them"} into a group, or choose "Ignore unsorted" to write the groups without ${n === 1 ? "it" : "them"}.`;
}
