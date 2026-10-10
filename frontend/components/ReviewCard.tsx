"use client";

import { ScheduleControl } from "./ScheduleControl";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type {
  BatchPublishResult,
  Content,
  ManualStep,
  Pause,
  Publication,
  PublishSkipped,
  Work,
} from "@/lib/types";
import { resumeTime } from "@/lib/format";
import { waitForJob } from "@/lib/jobs";
import { TagEditor } from "./TagEditor";
import { ShopBadge } from "./ShopPicker";

import { Txt } from "@/components/Txt";
import { ItemOptions } from "@/components/ItemOptions";
import { SkuEditor } from "@/components/SkuEditor";
import { ListingPersonalization } from "./ListingPersonalization";
import { useSession } from "./SessionProvider";
// The classic bounds; a listing carries its own profile's (title_min/max_length).
const DEFAULT_MIN_TITLE = 110;
const DEFAULT_MAX_TITLE = 140;
const MAX_TAG = 20;
const REQUIRED_TAGS = 13;

/** Mirror of the backend validation, for instant feedback while editing. */
function validate(
  title: string,
  tags: string[],
  description: string,
  MIN_TITLE: number,
  MAX_TITLE: number,
): string[] {
  const errors: string[] = [];
  if (!title.trim()) errors.push("Title is empty");
  else if (title.length < MIN_TITLE) errors.push(`Title must be at least ${MIN_TITLE} characters`);
  if (title.length > MAX_TITLE) errors.push(`Title exceeds ${MAX_TITLE} characters`);
  if (tags.length !== REQUIRED_TAGS) errors.push(`Need exactly ${REQUIRED_TAGS} tags`);
  const seen = new Set<string>();
  for (const t of tags) {
    if (!t.trim()) errors.push("A tag is empty");
    if (t.length > MAX_TAG) errors.push(`Tag over ${MAX_TAG} chars: "${t}"`);
    if (t.includes(",")) errors.push(`Tag has a comma: "${t}"`);
    const key = t.trim().toLowerCase();
    if (seen.has(key)) errors.push(`Duplicate tag: "${t}"`);
    seen.add(key);
  }
  if (!description.trim()) errors.push("Description is empty");
  return errors;
}

export function ReviewCard({
  initial,
  onChange,
  targets,
  profileFor,
  shopNames,
  bulkRunning,
  selected,
  onSelect,
  optionsVersion = 0,
}: {
  /** Ticked for "Set for selected" on the review page. */
  selected?: boolean;
  onSelect?: (on: boolean) => void;
  /** Bumped by the page after a bulk change, so the card reads its options again. */
  optionsVersion?: number;
  initial: Content;
  /** Shops ticked for this listing on the review page; omitted = its own shop. */
  targets?: string[];
  /** The profile chosen for a shop's drafts on the review page, by shop. */
  profileFor?: Record<string, string | null>;
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
  const [title, setTitle] = useState(initial.title ?? "");
  const [tags, setTags] = useState<string[]>(initial.tags ?? []);
  const [description, setDescription] = useState(initial.description ?? "");
  // The listing's SKU, prefilled from the file or folder name; editable here.
  const [sku, setSku] = useState<string | null>(initial.parsed_sku ?? null);
  useEffect(() => setSku(initial.parsed_sku ?? null), [initial.parsed_sku]);
  const [personalization, setPersonalization] = useState(initial.personalization ?? null);
  const [personalizationSource, setPersonalizationSource] = useState(initial.personalization_source ?? "unknown");
  // "Set for all" on the page changes it without remounting the card.
  const incomingPersonalization = JSON.stringify([initial.personalization, initial.personalization_source]);
  useEffect(() => {
    setPersonalization(initial.personalization ?? null);
    setPersonalizationSource(initial.personalization_source ?? "unknown");
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

  const errors = useMemo(
    () => validate(title, tags, description, MIN_TITLE, MAX_TITLE),
    [title, tags, description, MIN_TITLE, MAX_TITLE],
  );
  const valid = errors.length === 0;

  async function save() {
    setSaving(true);
    setMessage(null);
    try {
      await api.updateContent(initial.id, { title, tags, description });
      setDirty(false);
      setMessage("Saved");
    } catch (e: any) {
      setMessage(e.message ?? String(e));
    } finally {
      setSaving(false);
    }
  }

  async function toggleApprove() {
    if (dirty) await save();
    try {
      const res = await api.approve(initial.id, !approved);
      setApproved(res.content.approved);
      onChange?.({ id: initial.id, approved: res.content.approved });
      setMessage(res.content.approved ? "Approved" : "Approval cleared");
    } catch (e: any) {
      setMessage(e.message ?? String(e));
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
    if (dirty) await save();
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
        api.publishContent(initial.id, {
          targets: shops.map((connection_id) => ({ connection_id, profile_id: profileFor?.[connection_id] ?? undefined })),
        }),
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
      className={"card overflow-hidden " + (approved ? "border-emerald-300" : "")}
      id={initial.id}
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
          {initial.connection_id && shopNames?.[initial.connection_id] && (
            <p key="shop" className="flex items-center gap-1.5 border-b border-slate-100 px-4 py-2 text-xs text-slate-500">
              <span>Written for</span>
              <ShopBadge name={shopNames[initial.connection_id]} />
            </p>
          )}
          <div className="checkerboard flex aspect-[4/5] items-center justify-center p-4 max-lg:aspect-auto max-lg:h-56">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={api.assetImage(initial.asset_id, 896)}
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
          <div className="border-t border-slate-200 px-4 py-3">
            <SkuEditor
              value={sku}
              contentId={initial.id}
              save={(next) => api.setListingSku(initial.id, next)}
              onSaved={(res) => setSku(res.sku)}
            />
          </div>
        </div>

        {/* Editable fields */}
        <div className="flex flex-col">
          <div className="space-y-5 p-5">
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
            <div>
              <span className="label">Title</span>
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
            <ItemOptions key={`options-${optionsVersion}`} contentId={initial.id} />
            {initial.listing_style === "search" && (
              <p key="search-note" className="text-xs text-slate-500">
                Written to be optimised for search matching: a short title, with the other keywords in the tags,
                the description&apos;s opening and the attributes. This is not a promise of ranking.
              </p>
            )}

            <Description value={description} onChange={change(setDescription)} />

            <ListingPersonalization
              value={personalization}
              source={personalizationSource}
              onSave={async (setting) => {
                const res = await api.updateContent(initial.id, { personalization: setting });
                setPersonalization(res.content.personalization ?? null);
                setPersonalizationSource(res.content.personalization_source ?? "unknown");
              }}
            />

            {!valid && (
              <ul key="ul-288-12" className="space-y-0.5 text-xs text-rose-700">
                {errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            )}
          </div>

          {/* Sticky action bar: stays reachable while this card is on screen. */}
          <div className="sticky bottom-0 mt-auto border-t border-slate-200 bg-white/95 p-4 backdrop-blur">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <button className="btn-secondary" onClick={save} disabled={saving || !dirty}>
                  {saving ? "Saving…" : dirty ? "Save changes" : "Saved"}
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
                        {p.state === "active" ? "Live" : "Draft"}
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
        Recommended in Shop Manager (optional, doesn&apos;t block publishing){" "}
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
