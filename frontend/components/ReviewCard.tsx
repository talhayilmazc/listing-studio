"use client";

import Link from "next/link";
import { ScheduleControl } from "./ScheduleControl";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  BatchPublishResult,
  Checklist,
  Content,
  ManualStep,
  Pause,
  Personalization,
  Profile,
  Publication,
  PublishSkipped,
  Work,
} from "@/lib/types";
import { resumeTime } from "@/lib/format";
import { afterSave, patchFor, registerCard, SAVE_AFTER_MS, type CardText } from "@/lib/cardSave";
import { draftErrors } from "@/lib/etsyText";
import { ApiError } from "@/lib/api";
import { limitNotice } from "@/lib/chartOrder";
import { waitForJob } from "@/lib/jobs";
import { TagEditor } from "./TagEditor";
import { ProfilePicker } from "./ProfilePicker";
import { ShopBadge } from "./ShopPicker";

import { Txt } from "@/components/Txt";
import { ItemOptions } from "@/components/ItemOptions";
import { SkuEditor } from "@/components/SkuEditor";
import { profileIn, profileLocked, skuIn, textFor } from "@/lib/anotherShop";
import type { BatchShops } from "@/lib/types";
import { ListingPersonalization } from "./ListingPersonalization";
import { personalizationLine } from "@/lib/personalization";
import { PersonalizationFix } from "@/components/PersonalizationFix";
import { SearchChecklist } from "./SearchChecklist";
import { SEARCH_CARD_NOTE, TITLE_WARNING_HEAD, TITLE_WARNING_TAIL } from "@/lib/listingStyle";
import { useSession } from "./SessionProvider";
// The classic bounds; a listing carries its own profile's (title_min/max_length).
const DEFAULT_MIN_TITLE = 110;
const DEFAULT_MAX_TITLE = 140;
const REQUIRED_TAGS = 13;

export function ReviewCard({
  initial,
  onChange,
  targets,
  batchId,
  onPersonalizationForAll,
  shopNames,
  bulkRunning,
  selected,
  onSelect,
  optionsVersion = 0,
  needs,
  profiles,
  onProfile,
  destination,
  inView = null,
  shopData = null,
  onShopsChanged,
  textShop = null,
}: {
  /** The shop picked in the menu when the listing is not in it yet: the text typed here is
   *  that shop's own (the page remounts the card when this changes). */
  textShop?: { id: string; name: string } | null;
  /** The shop picked in the menu (several shops); null: every shop is shown. */
  inView?: { id: string; name: string } | null;
  /** Where each listing of the batch stands per shop, its SKU and profile there. */
  shopData?: BatchShops | null;
  /** A per-shop SKU or profile was saved: the page reads the shops again. */
  onShopsChanged?: () => void;
  /** The account's profiles: the card changes its listing's profile in place (B4). */
  profiles?: Profile[];
  onProfile?: (profileId: string | null) => void;
  /** "→ GROUP 1 · change": where this listing goes, from the page's "Where and when". */
  destination?: React.ReactNode;
  /** "Approve all" did not approve this listing: what it needs, short ("needs a title"). */
  needs?: string[];
  /** Ticked for "Set for selected" on the review page. */
  selected?: boolean;
  onSelect?: (on: boolean) => void;
  /** Bumped by the page after a bulk change, so the card reads its options again. */
  optionsVersion?: number;
  initial: Content;
  /** Shops ticked for this listing on the review page; omitted = its own shop. */
  targets?: string[];
  /** The batch, for the link back to the listing's group (where its profile is chosen). */
  batchId?: string;
  /** Give every listing of the batch this listing's personalization. */
  onPersonalizationForAll?: (setting: Personalization) => Promise<void>;
  shopNames?: Record<string, string>;
  /** The page is running a bulk action and watching its jobs itself. */
  bulkRunning?: boolean;
  /** Tell the page what changed (approval, a draft created or published), so its
   * bulk actions update without a reload (docs/duzeltmeler-v5.md §C). */
  onChange?: (change: Partial<Content> & { id: string }) => void;
}) {
  const { timeZone } = useSession();
  const MIN_TITLE = initial.title_min_length ?? DEFAULT_MIN_TITLE;
  const MAX_TITLE = initial.title_max_length ?? DEFAULT_MAX_TITLE;
  // The text as it goes to the shop being edited (its own there, else the listing's).
  const start = textFor(initial, textShop?.id ?? null);
  const [title, setTitle] = useState(start.title);
  const [tags, setTags] = useState<string[]>(start.tags);
  const [description, setDescription] = useState(start.description);
  // A listing the seller writes themselves: no AI wrote it and none is called here.
  const manual = initial.source === "manual";
  const [starting, setStarting] = useState(false);
  // The listing's SKU, prefilled from the file or folder name; editable here.
  const [sku, setSku] = useState<string | null>(initial.parsed_sku ?? null);
  useEffect(() => setSku(initial.parsed_sku ?? null), [initial.parsed_sku]);
  const [personalization, setPersonalization] = useState(initial.personalization ?? null);
  const [personalizationSource, setPersonalizationSource] = useState(initial.personalization_source ?? "unknown");
  const [editingPersonalization, setEditingPersonalization] = useState(false);
  // "Set for all" on the page changes it without remounting the card.
  // Etsy would refuse its personalization: nothing is sent, and the card offers the fix.
  const [hold, setHold] = useState(initial.personalization_hold ?? null);
  const incomingPersonalization = JSON.stringify([initial.personalization, initial.personalization_source, initial.personalization_hold]);
  useEffect(() => {
    setPersonalization(initial.personalization ?? null);
    setPersonalizationSource(initial.personalization_source ?? "unknown");
    setHold(initial.personalization_hold ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incomingPersonalization]);
  const [approved, setApproved] = useState(initial.approved);
  // "Approve all" on the page changes this without remounting the card.
  useEffect(() => {
    setApproved(initial.approved);
  }, [initial.approved]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // One draft per shop (v5 §E). A draft links to Shop Manager (editable); a live
  // listing to its public URL. The server resolves the URL (A4).
  const [publications, setPublications] = useState<Publication[]>(initial.publications);
  // Ticks can change from outside ("Mark all as done" on the page): follow them
  // without remounting, so text being edited here is kept.
  const incoming = JSON.stringify(initial.publications);
  useEffect(() => {
    setPublications(JSON.parse(incoming));
  }, [incoming]);
  const [publishState, setPublishState] = useState<"idle" | "publishing" | "paused" | "error">(
    "idle",
  );
  const [publishError, setPublishError] = useState<string | null>(null);
  // Drafts or go-lives that failed, per shop, with the reason: each can be tried
  // again in one click and carries on where it stopped. From the server (so it
  // survives a reload and a bulk run) and from the jobs this card watches.
  const [work, setWork] = useState<Work[]>(initial.work ?? []);
  const incomingWork = JSON.stringify(initial.work ?? []);
  useEffect(() => {
    setWork(JSON.parse(incomingWork));
  }, [incomingWork]);
  // Said while a job waits to run again by itself (Etsy asked for a pause, or did not answer).
  const [waiting, setWaiting] = useState<string | null>(null);
  // Shops this listing could not go to, and why.
  const [skipped, setSkipped] = useState<PublishSkipped[]>([]);
  // A job waiting for the daily Etsy reset: still queued, runs by itself later.
  const [pause, setPause] = useState<Pause | null>(null);

  // Where "Create draft" sends it: the review page's chosen shops, else its own shop.
  const wanted = targets ?? (initial.connection_id ? [initial.connection_id] : []);
  const drafted = new Set(publications.map((p) => p.connection_id));
  const missing = wanted.filter((shop) => !drafted.has(shop));

  // Every listing, whoever wrote it, is stopped only by what Etsy itself refuses for a
  // draft (lib/etsyText.ts); the app's quality rules are the server's notes on it
  // (content.check_listing, 2026-10-26: text a seller typed or rewrote was being held back).
  const errors = useMemo(() => draftErrors(title, tags, description), [title, tags, description]);
  const valid = errors.length === 0;
  const [notes, setNotes] = useState<string[]>(initial.validation?.warnings ?? []);

  // The search-readiness checklist. The server works it out with the app's own
  // rules (pipeline/checklist.py); while the seller edits, the unsaved text is
  // sent to be checked again (nothing is saved, nothing read from Etsy), so no
  // rule is copied here. It never blocks approval.
  const [checklist, setChecklist] = useState<Checklist | null>(initial.checklist ?? null);
  const [checking, setChecking] = useState(false);
  const checkedText = useRef(JSON.stringify([initial.title ?? "", initial.tags ?? [], initial.description ?? ""]));
  const checkRun = useRef(0);
  async function recheck() {
    const run = ++checkRun.current;
    const text = JSON.stringify([title, tags, description]);
    setChecking(true);
    try {
      const next = await api.previewChecklist(initial.id, { title, tags, description });
      if (run !== checkRun.current) return; // a newer edit is being checked
      checkedText.current = text;
      setChecklist(next);
    } catch {
      // The last checklist stays; the next edit tries again.
    } finally {
      if (run === checkRun.current) setChecking(false);
    }
  }
  useEffect(() => {
    if (JSON.stringify([title, tags, description]) === checkedText.current) return;
    const t = setTimeout(recheck, 500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [title, tags, description]);
  // A change from the page (a bulk action) brings a new checklist with the card.
  const incomingChecklist = JSON.stringify(initial.checklist ?? null);
  useEffect(() => {
    if (!dirty) setChecklist(JSON.parse(incomingChecklist));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incomingChecklist]);

  // What is typed is saved as it is typed (lib/cardSave.ts): shortly after typing
  // stops, before approving or drafting, before "Approve all" and the page's other
  // bulk actions, and when the card or the page is left. Only the changed fields go,
  // each with what the server had: another tab's edit is never overwritten.
  const textRef = useRef<CardText>({ title, tags, description });
  textRef.current = { title, tags, description };
  const savedRef = useRef<CardText>({ title: start.title, tags: start.tags, description: start.description });
  const inFlight = useRef<Promise<boolean> | null>(null);
  const [conflict, setConflict] = useState<string | null>(null);

  async function saveNow(opts: { keepalive?: boolean; keepMine?: boolean } = {}): Promise<boolean> {
    while (inFlight.current) await inFlight.current; // one save at a time, then what is left
    const before = savedRef.current;
    const patch = patchFor(textRef.current, before);
    if (!patch) {
      setDirty(false);
      return true;
    }
    const run = (async () => {
      setSaving(true);
      setMessage(null);
      try {
        const body = { ...(opts.keepMine ? { ...patch, base: undefined } : patch), ...(textShop ? { shop: textShop.id } : {}) };
        const res = await api.updateContent(initial.id, body, { keepalive: opts.keepalive });
        const server = { title: res.content.title ?? "", tags: res.content.tags ?? [], description: res.content.description ?? "" };
        savedRef.current = server;
        // The server's text (a hand-written description follows its title); what was typed meanwhile stays.
        const next = afterSave(patch, server, textRef.current, before);
        textRef.current = next;
        setTitle(next.title);
        setTags(next.tags);
        setDescription(next.description);
        setNotes(res.validation.warnings ?? []);
        if (res.content.checklist) {
          checkRun.current += 1; // the saved text's checklist wins over one still on its way
          checkedText.current = JSON.stringify([server.title, server.tags, server.description]);
          setChecklist(res.content.checklist);
          setChecking(false);
        }
        setDirty(patchFor(next, server) !== null);
        setConflict(null);
        setMessage("Saved");
        return true;
      } catch (e: any) {
        if (e instanceof ApiError && e.status === 409) setConflict(e.message);
        else setMessage(`Not saved: ${e.message ?? String(e)}`);
        return false;
      } finally {
        setSaving(false);
        inFlight.current = null;
      }
    })();
    inFlight.current = run;
    return run;
  }
  const save = () => saveNow();
  const saveRef = useRef(saveNow);
  saveRef.current = saveNow;

  // Typing stopped: save by itself (not while another tab's edit waits for the seller).
  useEffect(() => {
    if (!dirty || conflict) return;
    const t = setTimeout(() => saveRef.current(), SAVE_AFTER_MS);
    return () => clearTimeout(t);
  }, [title, tags, description, dirty, conflict]);

  // The page's bulk actions and leaving the page save every card first.
  useEffect(() => {
    const off = registerCard(initial.id, {
      flush: (keepalive) => saveRef.current({ keepalive }),
      unsaved: () => inFlight.current !== null || patchFor(textRef.current, savedRef.current) !== null,
    });
    return () => {
      off();
      // Left for another page of the app (or the card remounted): send what is typed.
      if (patchFor(textRef.current, savedRef.current)) saveRef.current({ keepalive: true });
    };
  }, [initial.id]);

  // "Use the saved one": the text another tab saved (an empty save returns it, changing nothing).
  async function useSaved() {
    try {
      const res = await api.updateContent(initial.id, textShop ? { shop: textShop.id } : {});
      const server = { title: res.content.title ?? "", tags: res.content.tags ?? [], description: res.content.description ?? "" };
      savedRef.current = server;
      textRef.current = server;
      setTitle(server.title);
      setTags(server.tags);
      setDescription(server.description);
      setNotes(res.validation.warnings ?? []);
      setDirty(false);
      setConflict(null);
    } catch (e: any) {
      setMessage(e.message ?? String(e));
    }
  }

  async function toggleApprove() {
    if (!(await saveNow())) return; // approving what is on the card, never older text
    try {
      const res = await api.approve(initial.id, !approved);
      setApproved(res.content.approved);
      onChange?.({ id: initial.id, approved: res.content.approved });
      setMessage(res.content.approved ? "Approved" : "Approval cleared");
    } catch (e: any) {
      setMessage(approveError(e.message ?? String(e)));
    }
  }

  // "Start from the profile's reference title and tags": the seller's own listing,
  // put in the empty fields to edit. Nothing is saved until they save.
  async function startFromReference() {
    setStarting(true);
    setMessage(null);
    try {
      const ref = await api.referenceText(initial.id);
      if (!title.trim()) setTitle(ref.title);
      if (tags.length === 0) setTags(ref.tags.slice(0, REQUIRED_TAGS));
      setDirty(true);
      setMessage(`Filled from ${ref.profile_name}'s reference listing: edit it for this design, then save`);
    } catch (e: any) {
      setMessage(e.message ?? String(e));
    } finally {
      setStarting(false);
    }
  }

  // Watch queued jobs (one per shop) until they settle. Each shop that lands
  // updates the card at once; one shop failing does not stop the others.
  type Watched = { job_id: string; connection_id: string | null; shop_name: string | null };
  async function watch(jobs: Watched[], kind: Work["kind"], timeoutMsg: string) {
    let current = publications;
    const failed: Work[] = [];
    const stalled: string[] = [];
    let paused: Pause | null = null;
    await Promise.all(
      jobs.map(async (j) => {
        const job = await waitForJob(j.job_id, setWaiting);
        const shop = j.shop_name ?? "this shop";
        if (job === null) stalled.push(`${shop}: ${timeoutMsg}`);
        else if (job.pause) paused = job.pause;
        else if (job.status === "succeeded" && job.listing_id && job.listing_url) {
          const pub: Publication = {
            connection_id: j.connection_id ?? "",
            shop_name: j.shop_name,
            etsy_listing_id: job.listing_id,
            state: job.is_draft ? "draft" : "active",
            listing_link: job.listing_url,
            manual_steps: job.manual_steps ?? [],
          };
          current = [...current.filter((p) => p.connection_id !== pub.connection_id), pub];
          setPublications(current);
        } else {
          failed.push({
            kind, connection_id: j.connection_id ?? "", shop_name: j.shop_name, job_id: j.job_id,
            status: "failed", error: job.error ?? "it failed without a reason", pause: null,
          });
        }
      }),
    );
    setWaiting(null);
    onChange?.({ id: initial.id, publications: current });
    const shops = new Set(jobs.map((j) => j.connection_id ?? ""));
    setWork((w) => [...w.filter((x) => !(x.kind === kind && shops.has(x.connection_id))), ...failed]);
    if (stalled.length) {
      setPublishError(stalled.join(" "));
      setPublishState("error");
    } else if (failed.length) {
      setPublishState("error");
    } else if (paused) {
      setPause(paused);
      setPublishState("paused");
    } else {
      setPublishState("idle");
    }
  }

  async function runJobs(start: () => Promise<BatchPublishResult>, kind: Work["kind"], timeoutMsg: string) {
    if (!(await saveNow())) return;
    setPublishState("publishing");
    setPublishError(null);
    setPause(null);
    try {
      const res = await start();
      setSkipped(res.skipped);
      await watch(res.jobs, kind, timeoutMsg);
    } catch (e: any) {
      setPublishError(e.message ?? String(e));
      setPublishState("error");
    }
  }

  // Jobs the server says are still going (queued behind others, or waiting to run
  // again by themselves): keep watching them, so the card follows without a reload.
  const watched = useRef(new Set<string>());
  useEffect(() => {
    const going = (JSON.parse(incomingWork) as Work[]).filter(
      (w) => w.status !== "failed" && !watched.current.has(w.job_id) && !(w.pause && w.pause.reason.endsWith("_quota")),
    );
    // During a bulk run the page watches every job and reloads the cards as they land.
    if (going.length === 0 || bulkRunning) return;
    going.forEach((w) => watched.current.add(w.job_id));
    setPublishState("publishing");
    for (const kind of ["draft", "publish"] as const) {
      const jobs = going.filter((w) => w.kind === kind);
      if (jobs.length) watch(jobs, kind, "still working after 15 minutes; reload to check.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incomingWork, bulkRunning]);

  // Step 1: create the drafts (never published automatically).
  const createDraftIn = (shops: string[]) =>
    runJobs(
      () =>
        // The shops only: every draft is built with the listing's own profile.
        api.publishContent(initial.id, { targets: shops.map((connection_id) => ({ connection_id })) }),
      "draft",
      "still working after 15 minutes; reload to check.",
    );
  const createDraft = () => createDraftIn(missing);
  // Step 2 (explicit): make one shop's reviewed, approved draft active.
  const publishNow = (shop: string) =>
    runJobs(
      () => api.publishLive(initial.id, [shop]),
      "publish",
      "still working after 15 minutes; reload to check whether it is live.",
    );
  // One click: the same job again. A draft carries on where it stopped; a second one is never made.
  const tryAgain = (w: Work) => (w.kind === "draft" ? createDraftIn([w.connection_id]) : publishNow(w.connection_id));
  const failures = work.filter((w) => w.status === "failed");

  // The seller confirms a Shop Manager setting on one draft; the page's summary follows.
  async function tickStep(shop: string, key: string, done: boolean) {
    const updated = await api.tickManualStep(initial.id, shop, key, done);
    const next = publications.map((p) => (p.connection_id === shop ? updated : p));
    setPublications(next);
    onChange?.({ id: initial.id, publications: next });
  }

  // Paused counts as busy: asking again would only return the same waiting job.
  const publishing = publishState === "publishing" || publishState === "paused";

  const change =
    <T,>(setter: (v: T) => void) =>
    (v: T) => {
      setter(v);
      setDirty(true);
    };

  const attributes = Object.entries(initial.attributes ?? {});
  // Out of range is a warning, not an error: amber, never red.
  const titleOut = title.length > 0 && (title.length < MIN_TITLE || title.length > MAX_TITLE);

  return (
    <div
      className={"card overflow-hidden " + (approved ? "border-emerald-300" : needs?.length && !valid ? "border-amber-400 ring-2 ring-amber-200" : "")}
      id={initial.id}
      data-testid="review-card"
    >
      <div className="grid gap-0 lg:grid-cols-[minmax(0,46%)_1fr]">
        {/* The design dominates: shown whole, on a neutral transparency backdrop. */}
        <div className="border-b border-slate-200 lg:border-b-0 lg:border-r">
          {onSelect && (
            <label key="select" className="flex min-h-[44px] cursor-pointer items-center gap-2 border-b border-slate-100 px-4 py-2 text-xs text-slate-600">
              <input type="checkbox" className="h-4 w-4" checked={!!selected} onChange={(e) => onSelect(e.target.checked)}
                aria-label={`Select ${initial.original_filename}`} data-testid="select-listing" />
              <span>Select</span>
            </label>
          )}
          {initial.profile_name && (
            // The listing is written with its profile (B3); where it goes is chosen in "Where and when".
            <p key="profile-top" className="border-b border-slate-100 px-4 py-2 text-xs text-slate-500" data-testid="card-profile-name">
              <span>Profile </span><span className="font-medium text-slate-800" translate="no">{initial.profile_name}</span>
            </p>
          )}
          {destination && (
            <p key="destination" className="border-b border-slate-100 px-4 py-2 text-xs text-slate-600" data-testid="card-destination">
              {destination}
            </p>
          )}
          <div className="checkerboard flex aspect-[4/5] items-center justify-center p-4 max-lg:aspect-auto max-lg:h-56">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={api.assetImage(initial.asset_id, "thumb")}
              alt={initial.original_filename}
              className="max-h-full max-w-full object-contain"
              loading="lazy"
              decoding="async"
            />
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-slate-200 p-4 text-xs">
            <Meta label="File" value={initial.original_filename} truncate />
            <Meta label="Rank" value={initial.rank == null ? "—" : String(initial.rank)} />
          </dl>
          <p className="border-t border-slate-200 px-4 py-3 text-xs text-slate-600" data-testid="card-profile">
            <span className="mb-1 block">
              <span>{inView ? "Profile in " : "Profile"}</span>
              {inView && <span key="shop" translate="no">{inView.name}</span>}
            </span>
            {profiles && onProfile ? (
              // Changing it here changes the listing's group, as on the batch page (no AI call); on
              // Etsy in another shop, the profile in the shop shown only.
              <ProfilePicker key="picker" profiles={profiles} shopId={null}
                value={profileIn(shopData, initial.id, inView?.id ?? null) ?? initial.profile_id ?? null}
                onChange={onProfile} emptyLabel="Choose a profile…" label="Profile" size="xs"
                disabled={bulkRunning || profileLocked(shopData, initial.id, inView?.id ?? null) || (!shopData && publications.length > 0)} />
            ) : (
              <span key="name" className="font-medium text-slate-800" translate="no">{initial.profile_name ?? "none chosen"}</span>
            )}
            {profileLocked(shopData, initial.id, inView?.id ?? null) && batchId && Object.keys(shopNames ?? {}).length > 1 ? (
              <span key="locked" className="mt-0.5 block text-slate-600" data-testid="card-profile-locked">
                <span>Already on Etsy here, built with this profile. </span>
                <Link href={`/batches/${batchId}?select=${initial.id}`} className="tap text-brand-700 underline">
                  Publish in another shop
                </Link>
              </span>
            ) : (
              <span key="from" className="mt-0.5 block text-slate-400">
                Price, variations, shipping, returns, size charts and the description come from it.
              </span>
            )}
          </p>
          <div className="border-t border-slate-200 px-4 py-3">
            <SkuEditor
              value={skuIn(shopData, initial.id, inView?.id ?? null, sku)}
              contentId={initial.id}
              shopId={inView?.id ?? null}
              shopName={inView?.name ?? null}
              save={(next) => api.setListingSku(initial.id, next, inView?.id ?? null)}
              onSaved={(res) => (res.connection_id ? onShopsChanged?.() : setSku(res.sku))}
            />
          </div>
        </div>

        {/* Editable fields */}
        <div className="flex flex-col">
          <div className="space-y-5 p-5">
            {Object.keys(shopNames ?? {}).length > 1 && (
              // Whose text this is: the shop picked in the menu (not in it yet), or the listing's.
              <p key="text-for" className={"rounded-md border px-3 py-2 text-xs " + (textShop ? "border-brand-200 bg-brand-50 text-brand-900" : "border-slate-200 bg-slate-50 text-slate-700")}
                data-testid="card-text-for">
                {textShop ? (
                  <span key="shop">
                    <span className="font-medium">Editing text for </span>
                    <span className="font-medium" translate="no">{textShop.name}</span>
                    <span>. The listing&apos;s own text, and the shops it is already in, stay as they are.</span>
                  </span>
                ) : inView ? (
                  <span key="in">
                    <span>Already in </span><span translate="no">{inView.name}</span>
                    <span>: its text there is edited on Etsy. Text edited here is the listing&apos;s, for shops it is not in yet.</span>
                  </span>
                ) : (
                  <span key="all">
                    <span>Editing the listing&apos;s text: it goes to every shop without text of its own. Shops it is already in are not changed. Pick a shop in the menu to edit its text only.</span>
                  </span>
                )}
              </p>
            )}
            {needs && needs.length > 0 && !approved && !valid && (
              // "Approve all" skipped it: said here, on the card, once.
              <p key="needs" role="status" className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-900" data-testid="card-needs">
                <span>Not approved yet: </span><span>{needs.join(", ")}</span>
              </p>
            )}
            {conflict && (
              <div key="conflict" role="alert" className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900" data-testid="card-conflict">
                <p>{conflict}</p>
                <div className="mt-1.5 flex flex-wrap gap-2">
                  <button type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={useSaved}>Use the saved one</button>
                  <button type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={() => saveNow({ keepMine: true })}>Keep what I typed</button>
                </div>
              </div>
            )}
            {(initial.findings ?? []).filter((f) => f.rule === "character_artwork").map((f, i) => (
              // The artwork, not the wording, is the risk: say so before anything is sent.
              <div
                key={i}
                role="alert"
                className={
                  "rounded-md border px-3 py-2 text-xs " +
                  (f.severity === "blocking"
                    ? "border-rose-200 bg-rose-50 text-rose-800"
                    : "border-amber-200 bg-amber-50 text-amber-900")
                }
              >
                <p className="font-medium">
                  {f.severity === "blocking" ? "Not sent to Etsy: recognisable characters" : "Recognisable characters in the artwork"}
                </p>
                <p className="mt-0.5">{f.detail}</p>
              </div>
            ))}
            {initial.image_limit && (
              <p key="image-limit" role="alert" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" data-testid="image-limit" translate="no">
                {limitNotice(initial.image_limit)}
              </p>
            )}
            {manual && (
              <div key="manual" className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700" data-testid="manual-note">
                <p>
                  <span className="font-medium text-slate-900">You write this one. </span>
                  <span>
                    No AI is used for its title and tags; everything else comes from its profile. It is saved as you type.
                    Only what Etsy itself refuses stops it: a title (140 characters at most, characters Etsy allows), up to
                    13 tags of 20 characters. The app&apos;s other checks are notes below the description.
                  </span>
                </p>
                {(!title.trim() || tags.length === 0) && (
                  <button key="start" type="button" className="btn-secondary mt-2 h-auto max-w-full whitespace-normal px-3 py-1.5 text-left text-xs" onClick={startFromReference}
                    disabled={starting} data-testid="manual-start">
                    {starting ? "Reading…" : "Start from the profile’s reference title and tags"}
                  </button>
                )}
              </div>
            )}
            <div>
              <span className="label">Title</span>
              {(initial.title_warnings ?? []).length > 0 && title === initial.title && (
                <div key="title-warnings" role="status" className="mb-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900"
                  data-testid="title-warnings">
                  <p className="font-medium">{TITLE_WARNING_HEAD}</p>
                  <ul className="mt-1 list-disc space-y-0.5 pl-4">
                    {(initial.title_warnings ?? []).map((w, i) => <li key={i}>{w}</li>)}
                  </ul>
                  <p className="mt-1">{TITLE_WARNING_TAIL}</p>
                </div>
              )}
              <div className="relative">
                {/* The whole title, wrapped: a one-line box showed only its first
                    words, on a phone about a quarter of it. A title has no line breaks. */}
                <textarea
                  rows={2}
                  className={
                    "field block resize-none leading-snug max-sm:min-h-[7rem] " +
                    (titleOut ? "border-amber-400 focus:border-amber-500 focus:ring-amber-500" : "")
                  }
                  value={title}
                  onChange={(e) => change(setTitle)(e.target.value.replace(/\s*[\r\n]+\s*/g, " "))}
                  onKeyDown={(e) => e.key === "Enter" && e.preventDefault()}
                  placeholder="Listing title"
                  aria-label="Listing title"
                />
                {/* The counter sits on the field's border rather than above it. */}
                <span
                  className={"counter " + (titleOut ? "text-amber-700" : "text-slate-400")}
                  title={`${MIN_TITLE}-${MAX_TITLE} characters`}
                >
                  <span><span>{title.length}</span> / <span>{MAX_TITLE}</span></span>
                </span>
              </div>
              {titleOut && (
                <p key="p-275-14" className="mt-1 text-xs text-amber-700">
                  {title.length < MIN_TITLE
                    ? `${MIN_TITLE - title.length} short of the ${MIN_TITLE} minimum`
                    : `${title.length - MAX_TITLE} over the ${MAX_TITLE} maximum`}
                </p>
              )}
            </div>

            <TagEditor tags={tags} onChange={change(setTags)} />

            {attributes.length > 0 && (
              <div key="attributes">
                <p className="label">Attributes</p>
                <ul className="flex flex-wrap gap-1.5">
                  {attributes.map(([name, value]) => (
                    <li key={name} className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-700">
                      <span className="text-slate-400">{name}</span><span>: </span><span>{value}</span>
                    </li>
                  ))}
                </ul>
                <p className="mt-1 text-xs text-slate-400">
                  From Etsy&apos;s own lists for this category; set on the draft when it is created.
                </p>
              </div>
            )}
            <ItemOptions key={`options-${optionsVersion}`} contentId={initial.id} onChanged={recheck} />
            {initial.listing_style === "search" && !manual && (
              <p key="search-note" className="text-xs text-slate-500">{SEARCH_CARD_NOTE}</p>
            )}

            <Description value={description} onChange={change(setDescription)} />

            <SearchChecklist checklist={checklist} updating={checking} />

            {/* One line: from its profile unless changed here (B5); "change" opens the editor. */}
            <p className="text-xs text-slate-600" data-testid="card-personalization">
              <span translate="no">{personalizationLine(personalization, personalizationSource)}</span>
              <span> · </span>
              <button type="button" className="tap text-brand-700 underline" onClick={() => setEditingPersonalization((v) => !v)}
                aria-expanded={editingPersonalization} data-testid="card-personalization-change">
                {editingPersonalization ? "done" : "change"}
              </button>
            </p>
            {hold && (
              <PersonalizationFix key="hold" hold={hold}
                scope={hold.source === "listing" ? "this listing" : `its profile (“${hold.profile_name ?? "profile"}”) and every listing that follows it`}
                onUse={async (body) => {
                  const res = await api.fixContentPersonalization(initial.id, body);
                  setPersonalization(res.content.personalization ?? null);
                  setPersonalizationSource(res.content.personalization_source ?? "unknown");
                  setHold(res.content.personalization_hold ?? null);
                  onChange?.({ id: initial.id, personalization: res.content.personalization, personalization_hold: res.content.personalization_hold });
                  return res.message;
                }} />
            )}
            {editingPersonalization && (
              <ListingPersonalization key="personalization"
                value={personalization}
                source={personalizationSource}
                onSave={async (setting) => {
                  const res = await api.updateContent(initial.id, { personalization: setting });
                  setPersonalization(res.content.personalization ?? null);
                  setPersonalizationSource(res.content.personalization_source ?? "unknown");
                  setHold(res.content.personalization_hold ?? null);
                }}
                onUseForAll={onPersonalizationForAll}
              />
            )}

            {!valid && (
              <ul key="ul-288-12" className="space-y-0.5 text-xs text-rose-700" data-testid="card-errors">
                {errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            )}
            {notes.length > 0 && (
              // The app's quality rules: notes on any listing, never a reason it cannot be approved.
              <div key="notes" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900" data-testid="card-notes">
                <p className="font-medium">Notes (they do not stop approval)</p>
                <ul className="mt-1 list-disc space-y-0.5 pl-4">
                  {notes.map((n, i) => <li key={i}>{n}</li>)}
                </ul>
              </div>
            )}
          </div>

          {/* Sticky action bar: stays reachable while this card is on screen. */}
          <div className="sticky bottom-0 mt-auto border-t border-slate-200 bg-white/95 p-4 backdrop-blur">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <button className="btn-secondary" onClick={save} disabled={saving || !dirty} translate="no">
                  {saving ? "Saving…" : dirty ? "Save now" : "Saved"}
                </button>
                <label className="flex cursor-pointer items-center gap-2 text-sm max-sm:min-h-[2.75rem]">
                  <input
                    type="checkbox"
                    checked={approved}
                    disabled={!valid}
                    onChange={toggleApprove}
                    className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                  />
                  <span className={approved ? "font-medium text-emerald-700" : "text-slate-600"}>
                    {approved ? "Approved" : "Approve for draft"}
                  </span>
                </label>
                {message && <span key="span-316-16" className="text-xs text-slate-400">{message}</span>}
              </div>

              <div className="flex items-center gap-2">
                {missing.length > 0 && (
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={createDraft}
                    disabled={!approved || publishing}
                    title={approved ? "Create Etsy draft listings" : "Approve first"}
                  >
                    {publishState === "paused"
                      ? "Draft queued"
                      : publishing
                        ? "Creating draft…"
                        : missing.length > 1
                          ? `Create drafts in ${missing.length} shops`
                          : shopNames?.[missing[0]]
                            ? `Create draft in ${shopNames[missing[0]]}`
                            : "Create draft"}
                  </button>
                )}
              </div>
            </div>
            {publications.length > 0 && (
              <ul key="ul-341-12" className="mt-3 divide-y divide-slate-100 rounded-lg border border-slate-200">
                {publications.map((p) => (
                  <li key={p.connection_id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                    <span className="min-w-0 flex-1 truncate text-sm text-slate-700">
                      <Txt>{p.shop_name ?? "Shop"}</Txt>
                      <span
                        className={
                          "ml-2 rounded-md border px-1.5 py-0.5 text-xs font-medium " +
                          (p.state === "active"
                            ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                            : "border-slate-200 bg-slate-50 text-slate-600")
                        }
                      >
                        {p.state === "active" ? (p.published_by === "seller_on_etsy" ? "Live · published in Shop Manager" : "Live") : "Draft"}
                      </span>
                    </span>
                    <a href={p.listing_link} target="_blank" rel="noreferrer" className="btn-secondary px-2.5 py-1 text-xs">
                      {p.state === "active" ? "View on Etsy ↗" : "Edit draft ↗"}
                    </a>
                    {p.state !== "active" && (
                      <button key="button-361-20"
                        type="button"
                        className="btn-primary px-2.5 py-1 text-xs"
                        onClick={() => publishNow(p.connection_id)}
                        disabled={publishing}
                        title="Make this draft active on Etsy"
                      >
                        {publishing ? "Publishing…" : "Publish now"}
                      </button>
                    )}
                    <ScheduleControl
                      contentId={initial.id}
                      publication={p}
                      approved={approved}
                      onChange={(patch) => {
                        const next = publications.map((x) =>
                          x.connection_id === p.connection_id ? { ...x, ...patch } : x,
                        );
                        setPublications(next);
                        // The page's bulk scheduling must see it too, or it would move it.
                        onChange?.({ id: initial.id, publications: next });
                      }}
                    />
                    {p.state !== "active" && p.manual_steps.length > 0 && (
                      <ManualSteps key="manualsteps-385-20"
                        steps={p.manual_steps}
                        link={p.listing_link}
                        onTick={(key, done) => tickStep(p.connection_id, key, done)}
                      />
                    )}
                  </li>
                ))}
              </ul>
            )}
            {(initial.deleted_on_etsy ?? []).length > 0 && (
              // Drafts the seller deleted in Shop Manager: the record stays, nothing is published there.
              <ul key="deleted-on-etsy" className="mt-2 space-y-1 text-xs text-slate-600" data-testid="card-deleted-on-etsy">
                {(initial.deleted_on_etsy ?? []).map((d) => (
                  <li key={`${d.connection_id}-${d.etsy_listing_id}`} className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
                    <span><Txt>{d.shop_name ? `${d.shop_name}: ` : ""}</Txt><span>{d.note}</span></span>
                  </li>
                ))}
              </ul>
            )}
            {skipped.length > 0 && (
              <ul key="ul-396-12" className="mt-2 space-y-0.5 text-right text-xs text-amber-800">
                {skipped.map((s, i) => (
                  <li key={i}>
                    <span><Txt>{s.shop_name ? `${s.shop_name}: ` : ""}</Txt>
                    <span>{s.reason}</span></span>
                  </li>
                ))}
              </ul>
            )}
            {waiting && publishState === "publishing" && (
              <p key="waiting" role="status" translate="no" className="mt-2 text-right text-xs text-amber-800">{waiting}</p>
            )}
            {failures.length > 0 && publishState !== "publishing" && (
              <ul key="failures" className="mt-2 space-y-2">
                {failures.map((w) => (
                  <li key={`${w.kind}-${w.connection_id}`} className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
                    <p>
                      <span className="font-medium">
                        <span>{w.kind === "draft" ? "Draft not created" : "Not published"}</span>
                        <Txt>{w.shop_name ? ` in ${w.shop_name}` : ""}</Txt>
                        <span>: </span>
                      </span>
                      <span translate="no">{w.error}</span>
                    </p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        className="rounded-md border border-rose-300 bg-white px-3 py-1.5 text-xs font-medium text-rose-800 hover:bg-rose-100 disabled:opacity-50 max-sm:min-h-[2.75rem] max-sm:px-4 max-sm:text-sm"
                        onClick={() => tryAgain(w)}
                        disabled={!approved || publishing}
                      >
                        Try again
                      </button>
                      <span className="text-rose-700/80">
                        {w.kind === "draft"
                          ? "It carries on where it stopped; a second draft is never created."
                          : "Nothing is published twice."}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            {publishError && <p key="p-406-12" className="mt-2 text-right text-xs text-rose-700">{publishError}</p>}
            {pause && publishState === "paused" && (
              <p key="p-407-12" role="status" translate="no" className="mt-2 text-right text-xs text-amber-800">
                <span>Queued, not failed. <span>{pause.message}</span> That is around <span>{resumeTime(pause.resumes_at, timeZone)}</span>.</span>
              </p>
            )}
            {publications.some((p) => p.state === "active") ? (
              <p className="mt-2 text-right text-xs text-slate-400">
                Published. To promote it, open{" "}
                <a
                  href="https://www.etsy.com/your/shops/me/tools/marketing"
                  target="_blank"
                  rel="noreferrer"
                  className="underline"
                >
                  Shop Manager → Marketing → Etsy Ads
                </a>
                . (Ads can&rsquo;t be managed from here — Etsy has no Ads API.)
              </p>
            ) : (
              <p className="mt-2 text-right text-xs text-slate-400">
                A <strong>draft</strong> is created first; it is never published automatically. You
                then publish it yourself with <strong>Publish now</strong>.
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** A refused approval says why in the server's own list; show the reasons, not the JSON. */
function approveError(message: string): string {
  try {
    const detail = JSON.parse(message);
    if (Array.isArray(detail?.errors) && detail.errors.length) return `Not approved: ${detail.errors.join("; ")}`;
  } catch {
    /* a plain sentence */
  }
  return message;
}

/**
 * Settings Etsy's API cannot make, listed on the draft as a recommendation: they
 * never block publishing (docs/duzeltmeler-v6.md §A2). The
 * seller ticks each one once it is set in Shop Manager. When all are ticked the
 * reminder collapses to one line, which can be reopened to undo a tick.
 */
function ManualSteps({
  steps,
  link,
  onTick,
}: {
  steps: ManualStep[];
  link: string;
  onTick: (key: string, done: boolean) => Promise<void>;
}) {
  const allDone = steps.every((s) => s.done);
  const [open, setOpen] = useState(!allDone);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Collapse whenever the last one gets ticked, here or by "Mark all as done".
  useEffect(() => {
    if (allDone) setOpen(false);
  }, [allDone]);

  async function tick(key: string, done: boolean) {
    setBusy(key);
    setError(null);
    try {
      await onTick(key, done);
      if (done && steps.every((s) => s.key === key || s.done)) setOpen(false);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(null);
    }
  }

  if (allDone && !open) {
    return (
      <p className="w-full text-xs text-slate-500">
        <span className="text-emerald-700">✓</span> Shop Manager settings confirmed ·{" "}
        <button type="button" className="underline hover:text-slate-800" onClick={() => setOpen(true)}>
          change
        </button>
      </p>
    );
  }
  return (
    <div className="w-full rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
      <p className="font-medium">
        Set in Shop Manager (optional; doesn&apos;t block publishing){" "}
        <a href={link} target="_blank" rel="noreferrer" className="tap font-normal underline">
          open the draft ↗
        </a>
      </p>
      <ul className="mt-1.5 space-y-1.5">
        {steps.map((s) => (
          <li key={s.key}>
            <label className="flex cursor-pointer items-start gap-2">
              <input
                type="checkbox"
                className="mt-0.5 h-3.5 w-3.5 rounded border-slate-300"
                checked={s.done}
                disabled={busy !== null}
                onChange={(e) => tick(s.key, e.target.checked)}
              />
              <span>
                <span className={"font-medium " + (s.done ? "line-through opacity-60" : "")}>
                  {s.label}
                </span>
                <span className="text-slate-500"><span> · <span>{s.detail}</span></span></span>
                <span className="block text-slate-500">I&apos;ve set this</span>
              </span>
            </label>
          </li>
        ))}
      </ul>
      {error && <p key="p-515-6" className="mt-1 text-rose-700">{error}</p>}
    </div>
  );
}

function Meta({ label, value, truncate }: { label: string; value: string; truncate?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-slate-400">{label}</dt>
      <dd
        className={"text-slate-700 " + (truncate ? "truncate" : "")}
        title={truncate ? value : undefined}
      >
        {value}
      </dd>
    </div>
  );
}

/** Collapsed to roughly three lines until opened. */
function Description({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <div className="mb-1 flex items-center justify-between">
        <span className="label mb-0">Description</span>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="tap text-xs font-medium text-brand-700 hover:text-brand-800"
        >
          {open ? "Collapse" : "Expand"}
        </button>
      </div>
      <textarea
        className={"field resize-y " + (open ? "min-h-[280px]" : "min-h-[76px]")}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Listing description"
        aria-label="Listing description"
      />
    </div>
  );
}
