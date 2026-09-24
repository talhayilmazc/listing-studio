"use client";

import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import type {
  BatchPublishResult,
  Content,
  ManualStep,
  Pause,
  Publication,
  PublishSkipped,
} from "@/lib/types";
import { resumeTime } from "@/lib/format";
import { waitForJob } from "@/lib/jobs";
import { TagEditor } from "./TagEditor";

const MIN_TITLE = 110;
const MAX_TITLE = 140;
const MAX_TAG = 20;
const REQUIRED_TAGS = 13;

/** Mirror of the backend validation, for instant feedback while editing. */
function validate(title: string, tags: string[], description: string): string[] {
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
  shopNames,
}: {
  initial: Content;
  /** Shops chosen on the review page; omitted = the listing's own shop. */
  targets?: string[];
  shopNames?: Record<string, string>;
  /** Tell the page what changed (approval, a draft created or published), so its
   * bulk actions update without a reload (docs/duzeltmeler-v5.md §C). */
  onChange?: (change: Partial<Content> & { id: string }) => void;
}) {
  const [title, setTitle] = useState(initial.title ?? "");
  const [tags, setTags] = useState<string[]>(initial.tags ?? []);
  const [description, setDescription] = useState(initial.description ?? "");
  const [approved, setApproved] = useState(initial.approved);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // One draft per shop (v5 §E). A draft links to Shop Manager (editable); a live
  // listing to its public URL. The server resolves the URL (A4).
  const [publications, setPublications] = useState<Publication[]>(initial.publications);
  const [publishState, setPublishState] = useState<"idle" | "publishing" | "paused" | "error">(
    "idle",
  );
  const [publishError, setPublishError] = useState<string | null>(null);
  // Shops this listing could not go to, and why.
  const [skipped, setSkipped] = useState<PublishSkipped[]>([]);
  // A job waiting for the daily Etsy reset: still queued, runs by itself later.
  const [pause, setPause] = useState<Pause | null>(null);

  // Where "Create draft" sends it: the review page's chosen shops, else its own shop.
  const wanted = targets ?? (initial.connection_id ? [initial.connection_id] : []);
  const drafted = new Set(publications.map((p) => p.connection_id));
  const missing = wanted.filter((shop) => !drafted.has(shop));

  const errors = useMemo(() => validate(title, tags, description), [title, tags, description]);
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

  // Run queued publish jobs (one per shop) and watch them settle. Each shop that
  // lands updates the card at once; one shop failing does not stop the others.
  async function runJobs(start: () => Promise<BatchPublishResult>, timeoutMsg: string) {
    if (dirty) await save();
    setPublishState("publishing");
    setPublishError(null);
    setPause(null);
    try {
      const res = await start();
      setSkipped(res.skipped);
      let current = publications;
      const failures: string[] = [];
      let paused: Pause | null = null;
      await Promise.all(
        res.jobs.map(async (j) => {
          const job = await waitForJob(j.job_id);
          const shop = j.shop_name ?? "this shop";
          if (job === null) failures.push(`${shop}: ${timeoutMsg}`);
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
          } else failures.push(`${shop}: ${job.error ?? "the job failed"}`);
        }),
      );
      onChange?.({ id: initial.id, publications: current });
      if (failures.length) {
        setPublishError(failures.join(" "));
        setPublishState("error");
      } else if (paused) {
        setPause(paused);
        setPublishState("paused");
      } else {
        setPublishState("idle");
      }
    } catch (e: any) {
      setPublishError(e.message ?? String(e));
      setPublishState("error");
    }
  }

  // Step 1: create the drafts (never published automatically).
  const createDraft = () =>
    runJobs(
      () =>
        api.publishContent(initial.id, {
          targets: missing.map((connection_id) => ({ connection_id })),
        }),
      "still working after 15 minutes; reload to check.",
    );
  // Step 2 (explicit): make one shop's reviewed, approved draft active.
  const publishNow = (shop: string) =>
    runJobs(
      () => api.publishLive(initial.id, [shop]),
      "still working after 15 minutes; reload to check whether it is live.",
    );

  // Paused counts as busy: asking again would only return the same waiting job.
  const publishing = publishState === "publishing" || publishState === "paused";

  const change =
    <T,>(setter: (v: T) => void) =>
    (v: T) => {
      setter(v);
      setDirty(true);
    };

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
          <div className="checkerboard flex aspect-[4/5] items-center justify-center p-4">
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
            <Meta label="SKU" value={initial.parsed_sku ?? "—"} />
            <Meta label="Rank" value={initial.rank == null ? "—" : String(initial.rank)} />
            {initial.model_used && <Meta label="Model" value={initial.model_used} truncate />}
          </dl>
        </div>

        {/* Editable fields */}
        <div className="flex flex-col">
          <div className="space-y-5 p-5">
            <div>
              <span className="label">Title</span>
              <div className="relative">
                <input
                  className={
                    "field " +
                    (titleOut ? "border-amber-400 focus:border-amber-500 focus:ring-amber-500" : "")
                  }
                  value={title}
                  onChange={(e) => change(setTitle)(e.target.value)}
                  placeholder="Listing title"
                  aria-label="Listing title"
                />
                {/* The counter sits on the field's border rather than above it. */}
                <span
                  className={"counter " + (titleOut ? "text-amber-700" : "text-slate-400")}
                  title={`${MIN_TITLE}-${MAX_TITLE} characters`}
                >
                  {title.length} / {MAX_TITLE}
                </span>
              </div>
              {titleOut && (
                <p className="mt-1 text-xs text-amber-700">
                  {title.length < MIN_TITLE
                    ? `${MIN_TITLE - title.length} short of the ${MIN_TITLE} minimum`
                    : `${title.length - MAX_TITLE} over the ${MAX_TITLE} maximum`}
                </p>
              )}
            </div>

            <TagEditor tags={tags} onChange={change(setTags)} />

            <Description value={description} onChange={change(setDescription)} />

            {!valid && (
              <ul className="space-y-0.5 text-xs text-rose-700">
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
                <label className="flex cursor-pointer items-center gap-2 text-sm">
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
                {message && <span className="text-xs text-slate-400">{message}</span>}
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
                          : publications.length
                            ? `Create draft in ${shopNames?.[missing[0]] ?? "this shop"}`
                            : "Create draft"}
                  </button>
                )}
              </div>
            </div>
            {publications.length > 0 && (
              <ul className="mt-3 divide-y divide-slate-100 rounded-lg border border-slate-200">
                {publications.map((p) => (
                  <li key={p.connection_id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                    <span className="min-w-0 flex-1 truncate text-sm text-slate-700">
                      {p.shop_name ?? "Shop"}
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
                      <button
                        type="button"
                        className="btn-primary px-2.5 py-1 text-xs"
                        onClick={() => publishNow(p.connection_id)}
                        disabled={publishing}
                        title="Make this draft active on Etsy"
                      >
                        {publishing ? "Publishing…" : "Publish now"}
                      </button>
                    )}
                    {p.state !== "active" && p.manual_steps.length > 0 && (
                      <ManualSteps steps={p.manual_steps} link={p.listing_link} />
                    )}
                  </li>
                ))}
              </ul>
            )}
            {skipped.length > 0 && (
              <ul className="mt-2 space-y-0.5 text-right text-xs text-amber-800">
                {skipped.map((s, i) => (
                  <li key={i}>
                    {s.shop_name ? `${s.shop_name}: ` : ""}
                    {s.reason}
                  </li>
                ))}
              </ul>
            )}
            {publishError && <p className="mt-2 text-right text-xs text-rose-700">{publishError}</p>}
            {pause && publishState === "paused" && (
              <p role="status" className="mt-2 text-right text-xs text-amber-800">
                Queued, not failed. {pause.message} That is around {resumeTime(pause.resumes_at)} your time.
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
 * Settings Etsy's API cannot make, listed on the draft before "Publish now" so the
 * seller sets them in Shop Manager first rather than finding out at publish time.
 */
function ManualSteps({ steps, link }: { steps: ManualStep[]; link: string }) {
  return (
    <div className="w-full rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
      <p className="font-medium">
        Set in Shop Manager before publishing{" "}
        <a href={link} target="_blank" rel="noreferrer" className="font-normal underline">
          open the draft ↗
        </a>
      </p>
      <ul className="mt-1 space-y-1">
        {steps.map((s) => (
          <li key={s.key}>
            <span className="font-medium">{s.label}</span>
            <span className="text-amber-800"> · {s.detail}</span>
          </li>
        ))}
      </ul>
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
          className="text-xs font-medium text-brand-700 hover:text-brand-800"
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
