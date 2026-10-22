"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  ApproveAllResult,
  Content,
  Pause,
  Personalization,
  Profile,
  PublishJob,
  PublishSkipped,
  SendPreview,
  SendState,
} from "@/lib/types";
import { UnfinishedDrafts } from "@/components/UnfinishedDrafts";
import { ListingChange, WhereAndWhen } from "@/components/WhereAndWhen";
import { BulkItemOptions } from "@/components/BulkItemOptions";
import { ListingPersonalization } from "@/components/ListingPersonalization";
import { resumeTime } from "@/lib/format";
import { waitForJob } from "@/lib/jobs";
import { applyChange, cardKey, emptyHandWritten, needsById, pendingManualSteps, reviewActions } from "@/lib/review";
import { anyUnsaved, flushAll } from "@/lib/cardSave";
import { ReviewCard } from "@/components/ReviewCard";
import { useShops } from "@/components/ShopProvider";

import { Txt } from "@/components/Txt";
import { useSession } from "@/components/SessionProvider";
interface Progress {
  label: string;
  total: number;
  done: number;
  failed: number;
  skipped: number;
  /** Still running when we stopped watching; the list refreshes when they land. */
  running: number;
  /** Queued, waiting for the daily Etsy reset; they run by themselves then. */
  paused: number;
  pause: Pause | null;
  /** Every job that settled, for the result shown when all have (v7 §E2). */
  outcomes: Outcome[];
  finished: boolean;
}

interface Outcome {
  contentId: string;
  shop: string | null;
  url: string | null;
  ok: boolean;
  error: string | null;
}

export default function ReviewPage({ params }: { params: { id: string } }) {
  const { timeZone } = useSession();
  const { id } = params;
  const [items, setItems] = useState<Content[] | null>(null);
  // Ticked for "Set for selected" (Occasion, Holiday, Section); a bump re-reads the cards' options.
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [optionsVersion, setOptionsVersion] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [busy, setBusy] = useState(false);
  const [skipped, setSkipped] = useState<PublishSkipped[]>([]);

  // Where and when (B4): the batch's listings go to the groups and shops chosen in the panel
  // at the top; every approved listing to every one of their shops, each shop once. The
  // profile is not chosen here: it is the listing's own in every shop.
  const { shops } = useShops();
  const [sendState, setSendState] = useState<SendState | null>(null);
  const [preview, setPreview] = useState<SendPreview | null>(null);
  const [sendTick, setSendTick] = useState(0);
  const shopNames = Object.fromEntries((shops ?? []).map((sh) => [sh.id, sh.name]));

  // Per listing: the shops it goes to (what its own "Create draft" sends).
  const chosenShops: Record<string, string[]> | undefined = preview
    ? Object.fromEntries(
        preview.rows.map((r) => [r.content_id, r.cells.filter((c) => c.state !== "cannot").map((c) => c.shop_id)]),
      )
    : undefined;
  const rowOf = Object.fromEntries((preview?.rows ?? []).map((r) => [r.content_id, r]));

  // The account's profiles, for changing a listing's profile on its card.
  const [profiles, setProfiles] = useState<Profile[]>([]);
  useEffect(() => {
    api.listProfiles().then((p) => setProfiles(p.filter((x) => x.confirmed))).catch(() => setProfiles([]));
  }, []);
  async function changeProfile(groupKey: string | null, profileId: string | null) {
    setError(null);
    try {
      await api.assignGroup(id, { group_keys: [groupKey ?? ""], profile_id: profileId });
      load();
    } catch (e: any) {
      setError(e.message ?? String(e));
    }
  }

  // Cards are keyed by their Etsy state (cardKey): a reload remounts only the cards
  // whose draft or live state changed, so text being edited elsewhere survives.
  const load = useCallback(() => {
    api
      .listContent(id)
      .then(setItems)
      .catch((e) => setError(String(e.message ?? e)));
  }, [id]);

  // A card approved something or finished its own job: the bulk buttons follow.
  const onCardChange = useCallback(
    (change: Partial<Content> & { id: string }) =>
      setItems((cur) => (cur ? applyChange(cur, change) : cur)),
    [],
  );

  useEffect(() => {
    load();
  }, [load]);

  // The batch's name, for the header (it is what the seller searches by).
  const [batchName, setBatchName] = useState<string | null>(null);
  useEffect(() => {
    api.batchSummary(id).then((b) => setBatchName(b.name)).catch(() => {});
  }, [id]);

  // The panel asks again when listings are approved, drafted, published or change profile.
  const approvedKey = (items ?? []).filter((c) => c.approved).map((c) => c.id).join(",");
  const draftedKey = (items ?? []).map((c) => `${c.publications.map((p) => p.state).join("/")}:${c.profile_id ?? ""}`).join(",");

  // One listing's personalization for every listing of the batch (its card's "Use this for all listings").
  async function personalizationForAll(setting: Personalization) {
    await api.setPersonalizationForAll(id, setting);
    load();
  }

  // Watch a set of queued jobs until each settles. Every job that lands refreshes
  // the list at once, so its card and the bulk buttons change without a reload.
  async function pollJobs(jobs: PublishJob[], label: string, skipped: number) {
    setProgress({
      label, total: jobs.length, done: 0, failed: 0, skipped, running: 0, paused: 0, pause: null,
      outcomes: [], finished: false,
    });
    await Promise.all(
      jobs.map(async (j) => {
        const s = await waitForJob(j.job_id);
        if (s === null) {
          setProgress((p) => p && { ...p, running: p.running + 1 });
        } else if (s.pause) {
          const pause = s.pause;
          setProgress((p) => p && { ...p, paused: p.paused + 1, pause });
        } else {
          const ok = s.status === "succeeded";
          const outcome: Outcome = {
            contentId: j.content_id,
            shop: s.shop_name,
            url: s.listing_url,
            ok,
            error: ok ? null : s.error,
          };
          setProgress((p) =>
            p && {
              ...p,
              done: p.done + (ok ? 1 : 0),
              failed: p.failed + (ok ? 0 : 1),
              outcomes: [...p.outcomes, outcome],
            },
          );
        }
        load();
      }),
    );
    setProgress((p) => p && { ...p, finished: true });
  }

  async function runBulk(
    label: string,
    call: () => Promise<{ jobs: PublishJob[]; skipped: PublishSkipped[] }>,
  ) {
    setBusy(true);
    setError(null);
    try {
      await flushAll(); // drafts are made from what is typed on the cards, saved first
      const res = await call();
      setSkipped(res.skipped);
      await pollJobs(res.jobs, label, res.skipped.length);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  const publishAll = () => runBulk("Publishing", () => api.publishBatchLive(id));

  // One click for the whole batch (v6 §D). The server approves only what passes
  // validation; the rest stay as they are and are listed below with the reason.
  const [approveResult, setApproveResult] = useState<ApproveAllResult | null>(null);
  async function approveAll() {
    setBusy(true);
    setError(null);
    try {
      // Every card saves what is typed on it first: approval reads the saved text.
      await flushAll();
      setApproveResult(await api.approveAll(id));
      load();
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  // Leaving the page (a reload, closing the tab, another site): what is typed on a card
  // is sent with keepalive, and the browser asks first while it is on its way.
  useEffect(() => {
    const leave = (e: BeforeUnloadEvent) => {
      if (!anyUnsaved()) return;
      flushAll(true);
      e.preventDefault();
      e.returnValue = "";
    };
    const hidden = () => {
      if (document.visibilityState === "hidden" && anyUnsaved()) flushAll(true);
    };
    window.addEventListener("beforeunload", leave);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      window.removeEventListener("beforeunload", leave);
      document.removeEventListener("visibilitychange", hidden);
    };
  }, []);

  // "Generate the empty ones": only hand-written listings nothing was typed into, one
  // group at a time (as the batch page writes), counted as Generate counts.
  const empties = emptyHandWritten(items ?? []);
  const [writing, setWriting] = useState<{ done: number; total: number } | null>(null);
  async function generateEmpty() {
    setBusy(true);
    setError(null);
    try {
      await flushAll(); // a card being typed into is not empty any more: the server sees it
      const keys = emptyHandWritten(items ?? []);
      setWriting({ done: 0, total: keys.length });
      const failed: string[] = [];
      for (const [n, key] of keys.entries()) {
        const res = await api.generate(id, undefined, key, { onlyEmpty: true });
        if (res.paused) {
          failed.push(res.paused);
          break;
        }
        for (const f of res.failures ?? []) failed.push(`${f.original_filename}: ${f.error}`);
        setWriting({ done: n + 1, total: keys.length });
      }
      if (failed.length) setError(failed.join(" "));
      setApproveResult(null);
      load();
    } catch (e: any) {
      setError(e.message ?? String(e)); // the allowance ran out: its own message, with when it resets
      load();
    } finally {
      setWriting(null);
      setBusy(false);
    }
  }
  const needs = needsById(approveResult);
  // One line per profile without a production partner (Etsy's Creativity Standards).
  const productionNotes = Object.values(
    (items ?? []).reduce<Record<string, { id: string; name: string; count: number }>>((acc, c) => {
      if (c.production_note && c.profile_id) {
        const row = acc[c.profile_id] ?? { id: c.profile_id, name: c.profile_name ?? "this profile", count: 0 };
        row.count += 1;
        acc[c.profile_id] = row;
      }
      return acc;
    }, {}),
  );

  const actions = reviewActions(items ?? [], chosenShops);
  // Settings Etsy's API cannot make, still to be set on the drafts in Shop Manager.
  const manual = pendingManualSteps(items ?? []);
  const [marking, setMarking] = useState(false);
  // For a seller who set them for the whole batch in Shop Manager at once.
  async function markAllDone() {
    setMarking(true);
    setError(null);
    try {
      await api.markManualStepsDone(id);
      load();
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setMarking(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0">
          <Link href={`/batches/${id}`} className="tap inline-block text-sm text-slate-400 hover:text-slate-600 max-sm:py-2">
            <span>← <span translate="no">{batchName ?? `Batch ${id.slice(0, 8)}`}</span></span>
          </Link>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Edit and approve each listing, then create drafts. Publishing is always a separate,
            explicit step.
          </p>
        </div>
        {items && items.length > 0 && (
          <div key="div-218-8" className="flex flex-wrap items-center gap-2 sm:gap-3">
            <span translate="no" className="text-xs tabular-nums text-slate-500">
              <span className="font-medium text-slate-700">{actions.approved}</span><span> of{" "}
              <span>{items.length}</span> approved</span>
            </span>
            {/* Each action appears when it has work, from the listings as they are now; the next
                step is the one primary action (approve, then draft, then publish). */}
            {empties.length > 0 && (
              <button key="generate-empty" className="btn-secondary" onClick={generateEmpty} disabled={busy} data-testid="generate-empty"
                title="Write the listings that have no title or tags yet with AI; listings you typed into are left as they are">
                <span translate="no">{writing ? `Writing ${writing.done} of ${writing.total}…` : `Generate the empty ones (${empties.length})`}</span>
              </button>
            )}
            {actions.approved < items.length && (
              <button key="button-225-12" className="btn-primary" onClick={approveAll} disabled={busy} data-testid="review-next">
                <span>Approve all (<span>{items.length - actions.approved}</span>)</span>
              </button>
            )}
            {actions.toPublish > 0 && (
              <button key="button-240-12" className="btn-secondary" onClick={publishAll} disabled={busy}
                title="Every draft of these listings goes live now">
                <span>Publish all drafts (<span>{actions.toPublish}</span>)</span>
              </button>
            )}
          </div>
        )}
      </div>

      <UnfinishedDrafts batchId={id} />

      {items && items.length > 0 && (
        <WhereAndWhen key="where-and-when"
          batchId={id}
          timeZone={timeZone}
          refreshKey={`${approvedKey}|${draftedKey}|${sendTick}`}
          disabled={busy}
          onState={setSendState}
          onPreview={setPreview}
          onDrafts={(call) => runBulk("Creating drafts", call)}
          onDone={load}
        />
      )}

      {productionNotes.length > 0 && (
        // Said once per profile, linking to it (it used to be a large box on every listing card).
        <ul key="production-notes" className="space-y-1 text-xs text-amber-900" data-testid="production-notes">
          {productionNotes.map((p) => (
            <li key={p.id}>
              <span translate="no">{`No production partner on the profile "${p.name}" (${p.count} listing${p.count === 1 ? "" : "s"}). `}</span>
              <Link href={`/profiles#profile-${p.id}`} className="tap text-brand-700 underline">Open the profile</Link>
            </li>
          ))}
        </ul>
      )}

      {items && items.length > 1 && (
        <details key="personalize-all" className="card p-3 text-sm">
          <summary className="tap cursor-pointer font-medium text-slate-800">Personalization: set for all listings</summary>
          <p className="mb-2 mt-1 text-xs text-slate-500">
            One setting for every listing of this batch, in every shop it goes to. Each card can still be changed on its
            own; &ldquo;Use the profile&apos;s&rdquo; there goes back to its profile. To give every listing the setting
            one listing already has, use &ldquo;Use this for all listings&rdquo; on that listing&apos;s card.
          </p>
          <ListingPersonalization
            value={null}
            source=""
            onSave={async (setting) => {
              await api.setPersonalizationForAll(id, setting);
              await load();
            }}
          />
          <button type="button" className="tap mt-2 text-xs text-slate-500 underline" onClick={async () => {
            await api.setPersonalizationForAll(id, null);
            await load();
          }}>
            Every listing follows its profile again
          </button>
        </details>
      )}
      {items && items.length > 1 && (
        <BulkItemOptions
          key="bulk-options"
          batchId={id}
          selected={selectedIds.filter((x) => items.some((c) => c.id === x))}
          total={items.length}
          onSelectAll={(all) => setSelectedIds(all ? items.map((c) => c.id) : [])}
          onDone={() => {
            setOptionsVersion((v) => v + 1);
            load(); // "Set SKU for selected" changes each card's SKU
          }}
        />
      )}

      {approveResult && (
        <div key="div-275-6" role="status" translate="no" className="card p-3 text-sm text-slate-700">
          <div className="flex items-start justify-between gap-3">
            <p>
              <span><span>Approved </span><span>{approveResult.approved}</span>
              <Txt>{approveResult.already_approved > 0 &&
                ` · ${approveResult.already_approved} already approved`}</Txt>
              <Txt>{approveResult.skipped.length > 0 &&
                ` · ${approveResult.skipped.length} not approved, fix these first:`}</Txt></span>
            </p>
            <button
              type="button"
              className="text-xs text-slate-400 hover:text-slate-700"
              onClick={() => setApproveResult(null)}
            >
              Dismiss
            </button>
          </div>
          {approveResult.summary && (
            // One line, then one line per reason with how many: never the same list for every listing.
            <p key="summary" className="mt-1.5 font-medium text-amber-900" data-testid="approve-summary">
              <span>{approveResult.summary}</span>
              {approveResult.skipped[0] && (
                <span key="show">
                  <span> </span>
                  <a href={`#${approveResult.skipped[0].content_id}`} className="tap font-normal text-brand-700 underline">show the first</a>
                </span>
              )}
            </p>
          )}
          {(approveResult.problems ?? []).length > 1 && (
            <ul key="problems" className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-amber-800" data-testid="approve-problems">
              {(approveResult.problems ?? []).map((p) => <li key={p.line} className="break-words">{p.line}</li>)}
            </ul>
          )}
          {!approveResult.summary && approveResult.skipped.length > 0 && (
            // An older server: its own list, still each listing once.
            <ul key="ul-293-10" className="mt-1.5 space-y-1 text-xs text-amber-800">
              {approveResult.skipped.map((s) => (
                <li key={s.content_id} className="break-words">
                  <span className="font-mono">{s.original_filename}</span><span>: <span>{s.reason}</span></span>
                </li>
              ))}
            </ul>
          )}
          {(approveResult.problems ?? []).some((p) => p.line.includes("no title yet")) && empties.length > 0 && (
            <button key="generate-empty-here" type="button" className="btn-secondary mt-2 px-3 py-1.5 text-xs" onClick={generateEmpty} disabled={busy}>
              <span translate="no">{`Generate the empty ones (${empties.length})`}</span>
            </button>
          )}
          {(approveResult.notes ?? []).length > 0 && (
            <div key="notes" className="mt-2 text-xs text-slate-600" data-testid="approve-notes">
              <p>Approved with notes (they do not stop a draft):</p>
              <ul className="mt-0.5 list-disc space-y-0.5 pl-5">
                {(approveResult.notes ?? []).map((p) => <li key={p.line}>{p.line}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}

      {manual.length > 0 && (
        // Information, not a gate (docs/duzeltmeler-v6.md §A2): publishing works without them.
        <div key="div-305-6" role="status" className="card border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
          <div className="flex flex-wrap items-start justify-between gap-3">
          <p>
            <span className="font-medium text-slate-900">Recommended in Shop Manager.</span>{" "}
            Etsy&apos;s API cannot set these, so set them on the drafts yourself. They don&apos;t
            block publishing: &ldquo;Publish all drafts&rdquo; works either way.
          </p>
          <button
            type="button"
            className="btn-secondary shrink-0 px-2.5 py-1 text-xs"
            onClick={markAllDone}
            disabled={marking}
            title="Tick every setting on every approved draft in this batch"
          >
            {marking ? "Marking…" : "Mark all as done"}
          </button>
          </div>
          <ul className="mt-1.5 space-y-1 text-xs">
            {manual.map((m) => (
              <li key={m.key}>
                <span className="font-medium">{m.label}</span>
                <span className="text-slate-500">
                  <span>{" "}
                  <span>· not yet ticked on </span><span>{m.drafts}</span><span> draft</span><Txt>{m.drafts === 1 ? "" : "s"}</Txt><span>. Each listing below
                  links to its draft; tick it there, or mark all as done once you have set them.</span></span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {skipped.length > 0 && (
        <div key="div-339-6" className="card space-y-1 p-3 text-xs text-amber-800">
          <p className="font-medium">Not sent:</p>
          <ul className="space-y-0.5">
            {skipped.map((s, i) => (
              <li key={i}>
                <span><Txt>{items?.find((c) => c.id === s.content_id)?.original_filename ?? "A listing"}</Txt>
                <Txt>{s.shop_name ? ` → ${s.shop_name}` : ""}</Txt><span>: </span><span>{s.reason}</span></span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {progress?.finished && (
        <PublishResult key="publishresult-353-6"
          progress={progress}
          titleOf={(cid) => items?.find((c) => c.id === cid)?.title ?? items?.find((c) => c.id === cid)?.original_filename ?? "Listing"}
          onDismiss={() => setProgress(null)}
        />
      )}

      {progress && !progress.finished && (
        <div key="div-361-6" translate="no" className="card space-y-2 p-3">
          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-700"><span><span>{progress.label}</span>…</span></span>
            <span className="text-xs text-slate-500">
              <span><span>{progress.done + progress.failed + progress.paused + progress.running}</span><span>/</span><span>{progress.total}</span>
              <Txt>{progress.failed > 0 && ` · ${progress.failed} failed`}</Txt>
              <Txt>{progress.paused > 0 && ` · ${progress.paused} waiting`}</Txt>
              <Txt>{progress.running > 0 && ` · ${progress.running} still running`}</Txt>
              <Txt>{progress.skipped > 0 && ` · ${progress.skipped} skipped`}</Txt></span>
            </span>
          </div>
          <div className="progress">
            <div
              className="progress-fill"
              style={{
                width: `${progress.total ? ((progress.done + progress.failed + progress.paused + progress.running) / progress.total) * 100 : 0}%`,
              }}
            />
          </div>
          {progress.pause && (
            <p key="p-381-10" role="status" translate="no" className="text-xs text-amber-800">
              <span><span>{progress.paused}</span> <span>{progress.paused === 1 ? "listing is" : "listings are"}</span> queued, not
              failed. <span>{progress.pause.message}</span> That is around{" "}
              <span>{resumeTime(progress.pause.resumes_at, timeZone)}</span>.</span>
            </p>
          )}
        </div>
      )}

      {error && <div key="div-391-6" className="card p-4 text-sm text-rose-700">{error}</div>}

      {items === null && !error && <p key="p-393-6" className="text-sm text-slate-400">Loading…</p>}

      {items && items.length === 0 && (
        <div key="div-395-6" className="card flex flex-col items-center gap-3 p-12 text-center">
          <p className="text-slate-500">No generated content yet for this batch.</p>
          <Link href={`/batches/${id}`} className="btn-primary">
            Generate content
          </Link>
        </div>
      )}

      {items && items.length > 0 && (
        <div key="div-404-6" className="space-y-5">
          {items.map((c) => (
            <ReviewCard
              key={cardKey(c)}
              initial={c}
              onChange={onCardChange}
              targets={chosenShops?.[c.id]}
              batchId={id}
              onPersonalizationForAll={items.length > 1 ? personalizationForAll : undefined}
              shopNames={shopNames}
              bulkRunning={busy}
              selected={selectedIds.includes(c.id)}
              onSelect={items.length > 1 ? (on) => setSelectedIds((cur) => (on ? [...cur, c.id] : cur.filter((x) => x !== c.id))) : undefined}
              optionsVersion={optionsVersion}
              needs={needs[c.id]}
              profiles={profiles}
              onProfile={(profileId) => changeProfile(c.group_key ?? null, profileId)}
              destination={rowOf[c.id] && sendState && sendState.shops.length > 1 ? (
                <span translate="no">
                  <span>{`→ ${rowOf[c.id].label} · `}</span>
                  <ListingChange batchId={id} state={sendState} row={rowOf[c.id]} onDone={() => setSendTick((n) => n + 1)} />
                </span>
              ) : undefined}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/** What a publish or draft run ended with (v7 §E2): how many, their links, and why any failed. */
function PublishResult({
  progress,
  titleOf,
  onDismiss,
}: {
  progress: Progress;
  titleOf: (contentId: string) => string;
  onDismiss: () => void;
}) {
  const { timeZone } = useSession();
  const publishing = progress.label.startsWith("Publish");
  const ok = progress.outcomes.filter((o) => o.ok);
  const bad = progress.outcomes.filter((o) => !o.ok);
  const n = (k: number, one: string, many: string) => `${k} ${k === 1 ? one : many}`;
  const head = publishing
    ? ok.length
      ? `Published ${n(ok.length, "listing", "listings")} on Etsy`
      : "Nothing was published"
    : ok.length
      ? `Created ${n(ok.length, "draft", "drafts")} on Etsy`
      : "No drafts were created";
  return (
    <div
      role="status" translate="no"
      className={
        "card space-y-3 p-4 text-sm " +
        (bad.length ? "border-amber-200" : ok.length ? "border-emerald-200 bg-emerald-50/40" : "")
      }
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-medium text-slate-900">
            {ok.length > 0 && <span key="span-552-12" className="text-emerald-700">✓ </span>}
            <span>{head}</span>
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            {[
              bad.length && n(bad.length, "failed", "failed"),
              progress.paused && `${progress.paused} waiting for the daily budget`,
              progress.running && `${progress.running} still running`,
              progress.skipped && `${progress.skipped} skipped`,
            ]
              .filter(Boolean)
              .join(" · ") || "Everything went through."}
          </p>
        </div>
        <button type="button" className="text-xs text-slate-400 underline hover:text-slate-700" onClick={onDismiss}>
          Dismiss
        </button>
      </div>
      {ok.length > 0 && (
        <ul key="ul-570-6" className="max-h-60 space-y-1 overflow-y-auto text-xs">
          {ok.map((o, i) => (
            <li key={i} className="flex items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-slate-700">
                <span>{titleOf(o.contentId)}</span>
                {o.shop && <span key="span-576-16" className="text-slate-400"><span> · <span>{o.shop}</span></span></span>}
              </span>
              {o.url && (
                <a key="a-578-14" href={o.url} target="_blank" rel="noreferrer" className="shrink-0 font-medium text-brand-700 hover:underline">
                  {publishing ? "View on Etsy ↗" : "Edit draft ↗"}
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
      {bad.length > 0 && (
        <ul key="ul-587-6" className="space-y-1 text-xs text-rose-800">
          {bad.map((o, i) => (
            <li key={i}>
              <span className="font-medium">{titleOf(o.contentId)}</span>
              {o.shop && <span key="span-592-14"><span> · <span>{o.shop}</span></span></span>}<span><span>: </span><Txt>{o.error ?? "failed"}</Txt></span>
            </li>
          ))}
        </ul>
      )}
      {progress.pause && (
        <p key="p-597-6" className="text-xs text-amber-800">
          <span>Queued, not failed: <span>{progress.pause.message}</span> That is around <span>{resumeTime(progress.pause.resumes_at, timeZone)}</span>.</span>
        </p>
      )}
    </div>
  );
}
