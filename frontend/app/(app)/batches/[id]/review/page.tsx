"use client";

import { useParams } from "next/navigation";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  ApproveAllResult,
  BatchShops,
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
import { Help } from "@/components/Help";
import { ListingChange, WhereAndWhen } from "@/components/WhereAndWhen";
import { BulkItemOptions } from "@/components/BulkItemOptions";
import { AttributeBulkSet } from "@/components/BulkExtras";
import { resumeTime } from "@/lib/format";
import { waitForJob } from "@/lib/jobs";
import { applyChange, cardKey, emptyHandWritten, needsById, pendingManualSteps, reviewActions } from "@/lib/review";
import { anyUnsaved, flushAll } from "@/lib/cardSave";
import { ReviewCard } from "@/components/ReviewCard";
import { GenerateConfirm, WrittenElsewhere } from "@/components/GenerateConfirm";
import { useShops } from "@/components/ShopProvider";
import { editsShopText } from "@/lib/anotherShop";

import { Txt } from "@/components/Txt";
import { useSession } from "@/components/SessionProvider";
import { toast, doneNote } from "@/lib/toast";
import { WORDS } from "@/lib/words";
import { primaryAction, summaryLine } from "@/lib/reviewBar";
import { SHORTCUTS, actionFor, move } from "@/lib/reviewKeys";
import { EmptyState, ListSkeleton } from "@/components/EmptyState";
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

// Next 15: a page's params arrive as a Promise; a client page reads them with useParams.
export default function ReviewPage() {
  const params = useParams<{ id: string }>();
  const { timeZone } = useSession();
  const { id } = params;
  const [items, setItems] = useState<Content[] | null>(null);
  const [attributeNote, setAttributeNote] = useState<string | null>(null);
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
  const { shops, scope } = useShops();
  // The shop picked in the menu: a card's profile and SKU are that shop's (several shops only).
  const inView = (shops?.length ?? 0) > 1 && scope ? { id: scope.id, name: scope.name } : null;
  const [shopData, setShopData] = useState<BatchShops | null>(null);
  // A listing not in the shop picked yet: its card edits that shop's text (and remounts on switch).
  const textShopOf = (c: Content) => (inView && editsShopText(shopData, c.id, inView.id) ? inView : null);
  const loadShops = useCallback(() => {
    api.batchShops(id).then(setShopData).catch(() => setShopData(null));
  }, [id]);
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

  // The examples: labelled, and never offered "Where and when" (they can't be sent).
  const isExample = (items ?? []).some((c) => c.is_example);
  // J/K/A/E/T and ? (lib/reviewKeys.ts): the listing they act on is outlined.
  const [focusIndex, setFocusIndex] = useState(-1);
  const [keysOpen, setKeysOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const action = actionFor(e);
      if (!action) return;
      if (action === "close") {
        if (keysOpen) setKeysOpen(false);
        else if (document.activeElement instanceof HTMLElement && document.activeElement.closest("[data-card-index]")) document.activeElement.blur();
        return;
      }
      if (action === "help") {
        e.preventDefault();
        setKeysOpen((o) => !o);
        return;
      }
      const count = items?.length ?? 0;
      if (!count) return;
      e.preventDefault();
      const card = (i: number) => document.querySelector<HTMLElement>(`[data-card-index="${i}"]`);
      if (action === "next" || action === "previous") {
        const to = move(focusIndex, action === "next" ? 1 : -1, count);
        setFocusIndex(to);
        card(to)?.scrollIntoView({ block: "start" });
        return;
      }
      const at = focusIndex < 0 ? 0 : focusIndex;
      if (focusIndex < 0) setFocusIndex(0);
      const root = card(at);
      if (action === "approve") root?.querySelector<HTMLButtonElement>('[data-testid="approve-toggle"]')?.click();
      if (action === "title") root?.querySelector<HTMLElement>('[data-field="title"]')?.focus();
      if (action === "tags") root?.querySelector<HTMLElement>('[data-field="tags"] [data-tag-input]')?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, focusIndex, keysOpen]);
  // Arriving with #send (the checklist's "Send or schedule it"), or the address changing to it while the
  // page is open (a link to #send on this page): the "Where and when" panel.
  const hasItems = items !== null;
  useEffect(() => {
    if (!hasItems) return;
    const open = () => {
      if (window.location.hash !== "#send") return;
      setSendOpen(true);
      setTimeout(() => document.getElementById("send")?.scrollIntoView({ block: "nearest" }), 0);
    };
    open();
    window.addEventListener("hashchange", open);
    return () => window.removeEventListener("hashchange", open);
  }, [hasItems]);

  // The account's profiles, for changing a listing's profile on its card.
  const [profiles, setProfiles] = useState<Profile[]>([]);
  useEffect(() => {
    api.listProfiles().then((p) => setProfiles(p.filter((x) => x.confirmed))).catch(() => setProfiles([]));
  }, []);
  async function changeProfile(groupKey: string | null, profileId: string | null) {
    setError(null);
    try {
      await api.assignGroup(id, { group_keys: [groupKey ?? ""], profile_id: profileId, for_shop: inView?.id ?? null });
      load();
      loadShops();
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
  useEffect(() => {
    loadShops();
  }, [loadShops, items]);

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
  async function approveAll(only: string[] | null = null) {
    setBusy(true);
    setError(null);
    // The ones this click approves: Undo takes back only those (lib/toast.ts).
    const before = new Set((items ?? []).filter((c) => !c.approved && (!only || only.includes(c.id))).map((c) => c.id));
    try {
      // Every card saves what is typed on it first: approval reads the saved text.
      await flushAll();
      const result = await api.approveAll(id, inView?.id ?? null, only);
      setApproveResult(result);
      load();
      if (result.approved > 0) {
        toast(doneNote(WORDS.approved, result.approved), {
          action: {
            label: "Undo",
            run: async () => {
              await Promise.all([...before].map((cid) => api.approve(cid, false).catch(() => undefined)));
              load();
            },
          },
        });
      }
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
  const empties = emptyHandWritten(items ?? [], inView?.id ?? null);
  const [writing, setWriting] = useState<{ done: number; total: number } | null>(null);
  // Said before anything is written: how many, and what it uses of the allowance.
  const [askEmpty, setAskEmpty] = useState(false);
  async function generateEmpty() {
    setAskEmpty(false);
    setBusy(true);
    setError(null);
    try {
      await flushAll(); // a card being typed into is not empty any more: the server sees it
      const keys = emptyHandWritten(items ?? [], inView?.id ?? null);
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
  const actions = reviewActions(items ?? [], chosenShops);
  const editedHere = inView ? (items ?? []).filter((c) => c.approved && (c.edited_for ?? []).includes(inView.id)).length : 0;
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

  // Part H: "Select" is one toggle; "Where and when" opens on Send; one primary action.
  const [selecting, setSelecting] = useState(false);
  const [sendOpen, setSendOpen] = useState(false);
  const selectedCount = (selectedIds ?? []).filter((x) => (items ?? []).some((c) => c.id === x)).length;
  const unapproved = (items ?? []).filter((c) => !c.approved);
  const selectedUnapproved = unapproved.filter((c) => selectedIds.includes(c.id)).map((c) => c.id);
  const primary = primaryAction({
    selectedUnapproved: selectedUnapproved.length, toSend: preview?.listings ?? 0, unapproved: unapproved.length, example: isExample,
  });
  const summary = isExample
    ? "Example listings: approve them to see how it works. They never go to Etsy."
    : summaryLine(preview, actions.approved, unapproved.length);
  function runPrimary() {
    if (!primary) return;
    if (primary.kind === "approve-selected") {
      approveAll(selectedUnapproved);
      return;
    }
    if (primary.kind === "send") {
      setSendOpen(true);
      setTimeout(() => document.getElementById("send")?.scrollIntoView({ block: "nearest" }), 0);
      return;
    }
    // "Approve": the listing outlined, or the first one not approved; then the next one is outlined.
    const list = items ?? [];
    const at = focusIndex >= 0 && list[focusIndex] && !list[focusIndex].approved ? focusIndex : list.findIndex((c) => !c.approved);
    if (at < 0) return;
    document.querySelector<HTMLElement>(`[data-card-index="${at}"] [data-testid="approve-toggle"]`)?.click();
    const next = list.findIndex((c, k) => k > at && !c.approved);
    if (next >= 0) {
      setFocusIndex(next);
      document.querySelector<HTMLElement>(`[data-card-index="${next}"]`)?.scrollIntoView({ block: "start" });
    }
  }
  const moreItems = [
    ...(unapproved.length > 0 ? [{ key: "all", testId: "approve-all", label: `Approve all (${unapproved.length})`, title: undefined as string | undefined, run: () => approveAll() }] : []),
    ...(actions.approved === (items ?? []).length && inView && editedHere > 0
      ? [{ key: "again", testId: "review-approve-shop", label: `Approve again for ${inView.name} (${editedHere})`, title: undefined, run: () => approveAll() }] : []),
    ...(actions.toPublish > 0 ? [{ key: "publish", testId: "publish-all", label: `Publish all drafts (${actions.toPublish})`, title: "Every draft of these listings goes live now", run: publishAll }] : []),
    ...(empties.length > 0 ? [{ key: "empty", testId: "generate-empty", label: writing ? `Writing ${writing.done} of ${writing.total}…` : `Generate the empty ones (${empties.length})`,
      title: "Write the listings that have no title or tags yet with AI; listings you typed into are left as they are", run: () => setAskEmpty(true) }] : []),
  ];

  return (
  <div className="xl:grid xl:grid-cols-[minmax(0,1fr)_20rem] xl:items-start xl:gap-6">
    <div className="min-w-0 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0">
          <Link href={`/batches/${id}`} className="tap inline-block text-sm text-slate-400 hover:text-slate-600 max-sm:py-2">
            <span>← <span translate="no">{batchName ?? `Batch ${id.slice(0, 8)}`}</span></span>
          </Link>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            <span>Approve each listing, then choose where and when it goes.</span>
            <Help label="this page">
              Nothing reaches Etsy until you press the button in Where and when. A listing goes to every shop you choose;
              one already in a shop is not sent there again. Going live is always your own choice: now, or at the times
              you set.
            </Help>
          </p>
        </div>
      </div>

      {isExample ? (
        <section key="example" className="card border-[var(--tag)] bg-[#fff8dc] p-4 text-sm text-slate-900" data-testid="example-banner">
          <p>
            <span className="font-bold">Example listings. </span>
            <span>Listyro wrote these once from three sample designs, to show how review works. Edit and approve them as you like: they cost nothing and can&apos;t be sent to Etsy.</span>
          </p>
          <Link href="/upload" className="btn-primary mt-3">{WORDS.upload}</Link>
        </section>
      ) : null}

      <UnfinishedDrafts batchId={id} />

      {askEmpty && empties.length > 0 && (
        <GenerateConfirm key="ask-empty" count={empties.length} kind="rewrite" onConfirm={generateEmpty} onCancel={() => setAskEmpty(false)} />
      )}
      <WrittenElsewhere key="written-elsewhere" data={shopData} />
      {items && items.length > 1 && selecting && selectedIds.some((x) => items.some((c) => c.id === x)) && (
        <BulkItemOptions
          key="bulk-options"
          batchId={id}
          selected={selectedIds.filter((x) => items.some((c) => c.id === x))}
          onDone={() => {
            setOptionsVersion((v) => v + 1);
            load(); // "Set SKU for selected" changes each card's SKU
          }}
        />
      )}
      {items && selectedIds.some((x) => items.some((c) => c.id === x)) && (
        <div key="bulk-attributes" className="card p-3 text-sm">
          <AttributeBulkSet batchId={id} ids={selectedIds.filter((x) => items.some((c) => c.id === x))}
            onDone={(message) => { setOptionsVersion((v) => v + 1); setAttributeNote(message); }} />
          {attributeNote && <p key="note" role="status" className="mt-1 text-xs text-brand-800"><span translate="no">{attributeNote}</span></p>}
        </div>
      )}

      {approveResult && (
        <div key="div-275-6" role="status" translate="no" className="card p-3 text-sm text-slate-700">
          <div className="flex items-start justify-between gap-3">
            <p>
              <span><span>Approved </span><span>{approveResult.approved}</span>
              <Txt>{approveResult.already_approved > 0 &&
                ` · ${approveResult.already_approved} already approved`}</Txt>
              <Txt>{(approveResult.approved_for_shop ?? 0) > 0 && inView &&
                ` · ${approveResult.approved_for_shop} approved again for ${inView.name}`}</Txt>
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
            <button key="generate-empty-here" type="button" className="btn-secondary mt-2 px-3 py-1.5 text-xs" onClick={() => setAskEmpty(true)} disabled={busy}>
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
            <span className="font-medium text-slate-900">Set in Shop Manager.</span>{" "}
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

      {items === null && !error && <ListSkeleton key="loading" rows={2} height="32rem" />}

      {items && items.length === 0 && (
        <EmptyState key="empty" line="No listing of this batch is written yet. Write them, then check each one here." action={WORDS.write} href={`/batches/${id}`} />
      )}

      {items && items.length > 0 && (
        <div key="keys" className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-700" data-testid="list-header">
          <span className="flex flex-wrap items-center gap-3">
            <span translate="no" className="tabular-nums">
              <span className="font-medium text-slate-900">{actions.approved}</span><span>{` of ${items.length} approved`}</span>
            </span>
            {items.length > 1 ? (
              <button key="select" type="button" className="btn-quiet" aria-pressed={selecting} data-testid="select-toggle"
                onClick={() => { setSelecting((v) => !v); setSelectedIds([]); }}>
                {selecting ? "Done selecting" : "Select"}
              </button>
            ) : null}
            {selecting ? (
              <label key="all" className="flex min-h-[44px] cursor-pointer items-center gap-2">
                <input type="checkbox" className="h-4 w-4" checked={selectedCount === items.length}
                  ref={(el) => { if (el) el.indeterminate = selectedCount > 0 && selectedCount < items.length; }}
                  onChange={(e) => setSelectedIds(e.target.checked ? items.map((c) => c.id) : [])} aria-label="Select every listing" />
                <span translate="no">{selectedCount ? `${selectedCount} selected` : "Select all"}</span>
              </label>
            ) : null}
          </span>
          <button type="button" className="btn-quiet max-sm:hidden" onClick={() => setKeysOpen(true)} data-testid="keys-open">
            Keyboard shortcuts (?)
          </button>
        </div>
      )}

      {keysOpen ? <KeysHelp key="keys-help" onClose={() => setKeysOpen(false)} /> : null}

      {items && items.length > 0 && (
        <div key="div-404-6" className="space-y-5">
          {items.map((c, i) => (
            <div key={`${cardKey(c)}:${textShopOf(c)?.id ?? ""}`} data-card-index={i}
              className={"rounded-[var(--radius-md)] " + (i === focusIndex ? "ring-2 ring-ink ring-offset-2" : "")}>
            <ReviewCard
              key={`${cardKey(c)}:${textShopOf(c)?.id ?? ""}`}
              textShop={textShopOf(c)}
              initial={c}
              onChange={onCardChange}
              targets={chosenShops?.[c.id]}
              batchId={id}
              onPersonalizationForAll={items.length > 1 ? personalizationForAll : undefined}
              shopNames={shopNames}
              bulkRunning={busy}
              selected={selectedIds.includes(c.id)}
              onSelect={items.length > 1 && selecting ? (on) => setSelectedIds((cur) => (on ? [...cur, c.id] : cur.filter((x) => x !== c.id))) : undefined}
              optionsVersion={optionsVersion}
              needs={needs[c.id]}
              profiles={profiles}
              onProfile={(profileId) => changeProfile(c.group_key ?? null, profileId)}
              inView={inView}
              shopData={shopData}
              onShopsChanged={loadShops}
              destination={rowOf[c.id] && sendState && sendState.shops.length > 1 ? (
                <span translate="no">
                  <span>{`→ ${rowOf[c.id].label} · `}</span>
                  <ListingChange batchId={id} state={sendState} row={rowOf[c.id]} onDone={() => setSendTick((n) => n + 1)} />
                </span>
              ) : undefined}
            />
            </div>
          ))}
        </div>
      )}
    </div>

      {/* The action bar (Part H): a plain sentence and one primary action. Beside the listings on a
          wide screen, at the bottom of the screen on a phone; "Where and when" opens on Send. */}
      {items && items.length > 0 && (
        <aside key="bar" aria-label="Approve and send"
          className="sticky bottom-0 z-20 -mx-4 mt-6 border-t border-slate-200 bg-white/95 px-4 py-3 shadow-[var(--shadow-lg)] backdrop-blur sm:-mx-6 sm:px-6 xl:top-4 xl:bottom-auto xl:mx-0 xl:mt-0 xl:rounded-[var(--radius-md)] xl:border xl:px-4 xl:shadow-[var(--shadow-sm)]"
          data-testid="review-bar">
          <p translate="no" className="text-sm text-slate-800" data-testid="review-summary">{summary}</p>
          {sendState && sendState.shops.length > 1 && !isExample ? (
            <p key="to" translate="no" className="mt-1 text-xs text-slate-600" data-testid="review-send-to">{`Send to: ${sendState.label}`}</p>
          ) : null}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {primary ? (
              <button key="primary" type="button" className="btn-primary" disabled={busy} data-testid="review-next"
                onClick={runPrimary}>
                <span translate="no">{primary.label}</span>
              </button>
            ) : null}
            {moreItems.length > 0 ? (
              <details key="more" className="relative" data-testid="review-more">
                <summary className="btn-secondary cursor-pointer list-none">More</summary>
                <div className="absolute bottom-full right-0 z-30 mb-2 flex w-64 flex-col gap-1 rounded-[var(--radius-md)] border border-slate-200 bg-white p-2 shadow-[var(--shadow-lg)] xl:bottom-auto xl:top-full xl:mb-0 xl:mt-2">
                  {moreItems.map((m) => (
                    <button key={m.key} type="button" className="btn-quiet justify-start text-left" disabled={busy} data-testid={m.testId}
                      title={m.title} onClick={(e) => { (e.currentTarget.closest("details") as HTMLDetailsElement | null)?.removeAttribute("open"); m.run(); }}>
                      <span translate="no">{m.label}</span>
                    </button>
                  ))}
                </div>
              </details>
            ) : null}
            {sendOpen ? (
              <button key="close" type="button" className="btn-quiet" onClick={() => setSendOpen(false)}>Close</button>
            ) : null}
          </div>
          {!isExample ? (
            <div key="send" id="send" hidden={!sendOpen} className="mt-3 max-h-[70vh] overflow-y-auto xl:max-h-none">
              <WhereAndWhen
                batchId={id}
                timeZone={timeZone}
                refreshKey={`${approvedKey}|${draftedKey}|${sendTick}`}
                disabled={busy}
                onState={setSendState}
                onPreview={setPreview}
                onDrafts={(call) => runBulk("Creating drafts", call)}
                onDone={load}
                primary
              />
            </div>
          ) : null}
        </aside>
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

/** The review screen's keys (lib/reviewKeys.ts), on "?" or the "Keyboard shortcuts" button. */
function KeysHelp({ onClose }: { onClose: () => void }) {
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="keys-title" className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose} data-testid="keys-help">
      <div className="card w-full max-w-sm p-5" onClick={(e) => e.stopPropagation()}>
        <h2 id="keys-title" className="text-base font-bold text-slate-900">Keyboard shortcuts</h2>
        <dl className="mt-3 space-y-2 text-sm">
          {SHORTCUTS.map((k) => (
            <div key={k.keys} className="flex items-center gap-3">
              <dt className="w-12 shrink-0"><kbd className="rounded border border-slate-300 bg-slate-50 px-1.5 py-0.5 font-mono text-xs text-slate-900">{k.keys}</kbd></dt>
              <dd className="text-slate-700">{k.does}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-xs text-slate-600">They never act while you type in a field.</p>
        <button type="button" className="btn-secondary mt-4" onClick={onClose} autoFocus>Close</button>
      </div>
    </div>
  );
}
