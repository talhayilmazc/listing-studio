"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type {
  Content,
  Pause,
  PublishJob,
  PublishPreview,
  PublishSkipped,
  PublishTarget,
} from "@/lib/types";
import { resumeTime } from "@/lib/format";
import { waitForJob } from "@/lib/jobs";
import { applyChange, cardKey, pendingManualSteps, reviewActions } from "@/lib/review";
import { ReviewCard } from "@/components/ReviewCard";
import { useShops } from "@/components/ShopProvider";

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
}

export default function ReviewPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const [items, setItems] = useState<Content[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [busy, setBusy] = useState(false);
  const [skipped, setSkipped] = useState<PublishSkipped[]>([]);

  // Where drafts go (v5 §E): each listing to the shop it was written for, or to
  // the shops chosen here (each with its own profile; null = chosen for you).
  const { shops } = useShops();
  const [mode, setMode] = useState<"own" | "chosen">("own");
  const [chosen, setChosen] = useState<Record<string, string | null>>({});
  const [preview, setPreview] = useState<PublishPreview | null>(null);
  const targets: PublishTarget[] | undefined =
    mode === "chosen"
      ? Object.entries(chosen).map(([connection_id, profile_id]) => ({ connection_id, profile_id }))
      : undefined;
  const targetKey = JSON.stringify(targets ?? null);
  const shopNames = Object.fromEntries((shops ?? []).map((s) => [s.id, s.name]));

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

  // The estimate before anything is queued: drafts per shop, the Etsy requests they
  // take, and whether that fits what can still be spent today (v5 §E).
  const approvedKey = (items ?? []).filter((c) => c.approved).map((c) => c.id).join(",");
  useEffect(() => {
    if (!approvedKey || (mode === "chosen" && !targets?.length)) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    api
      .publishPreview(id, { targets })
      .then((p) => !cancelled && setPreview(p))
      .catch(() => !cancelled && setPreview(null));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, approvedKey, targetKey, mode]);

  // Watch a set of queued jobs until each settles. Every job that lands refreshes
  // the list at once, so its card and the bulk buttons change without a reload.
  async function pollJobs(jobs: PublishJob[], label: string, skipped: number) {
    setProgress({
      label, total: jobs.length, done: 0, failed: 0, skipped, running: 0, paused: 0, pause: null,
    });
    await Promise.all(
      jobs.map(async (j) => {
        const s = await waitForJob(j.job_id);
        if (s === null) {
          setProgress((p) => p && { ...p, running: p.running + 1 });
        } else if (s.pause) {
          const pause = s.pause;
          setProgress((p) => p && { ...p, paused: p.paused + 1, pause });
        } else if (s.status === "succeeded") {
          setProgress((p) => p && { ...p, done: p.done + 1 });
        } else {
          setProgress((p) => p && { ...p, failed: p.failed + 1 });
        }
        load();
      }),
    );
  }

  async function runBulk(
    label: string,
    call: () => Promise<{ jobs: PublishJob[]; skipped: PublishSkipped[] }>,
  ) {
    setBusy(true);
    setError(null);
    try {
      const res = await call();
      setSkipped(res.skipped);
      await pollJobs(res.jobs, label, res.skipped.length);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  const createDraftsAll = () =>
    runBulk("Creating drafts", () => api.publishBatch(id, { targets }));
  const publishAll = () => runBulk("Publishing", () => api.publishBatchLive(id));

  const actions = reviewActions(
    items ?? [],
    targets?.map((t) => t.connection_id),
  );
  const overBudget = preview !== null && !preview.fits;
  // Settings Etsy's API cannot make, still to be set on the drafts in Shop Manager.
  const manual = pendingManualSteps(items ?? []);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0">
          <Link href={`/batches/${id}`} className="text-sm text-slate-400 hover:text-slate-600">
            ← Batch {id.slice(0, 8)}
          </Link>
          <p className="mt-1 max-w-2xl text-sm text-slate-500">
            Edit and approve each listing, then create drafts. Publishing is always a separate,
            explicit step.
          </p>
        </div>
        {items && items.length > 0 && (
          <div className="flex items-center gap-3">
            <span className="text-xs tabular-nums text-slate-500">
              <span className="font-medium text-slate-700">{actions.approved}</span> of{" "}
              {items.length} approved
            </span>
            {/* Each action appears when it has work, from the listings as they are now. */}
            {actions.toDraft > 0 && (
              <button
                className="btn-secondary"
                onClick={createDraftsAll}
                disabled={busy || overBudget || (mode === "chosen" && !targets?.length)}
                title={overBudget ? preview?.message ?? undefined : undefined}
              >
                Create drafts for all ({actions.toDraft})
              </button>
            )}
            {actions.toPublish > 0 && (
              <button className="btn-primary" onClick={publishAll} disabled={busy}>
                Publish all ({actions.toPublish})
              </button>
            )}
          </div>
        )}
      </div>

      {items && items.length > 0 && shops && shops.length > 1 && (
        <TargetPanel
          shops={shops.map((s) => ({ id: s.id, name: s.name }))}
          mode={mode}
          setMode={setMode}
          chosen={chosen}
          setChosen={setChosen}
          preview={preview}
        />
      )}
      {items && items.length > 0 && (!shops || shops.length <= 1) && preview && !preview.fits && (
        <div className="card p-3 text-sm text-amber-800">{preview.message}</div>
      )}

      {manual.length > 0 && (
        <div role="status" className="card border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-medium">
            Before {actions.toPublish > 1 ? "“Publish all”" : "publishing"}: set these on the
            drafts in Shop Manager. Etsy&apos;s API cannot set them for you.
          </p>
          <ul className="mt-1.5 space-y-1 text-xs">
            {manual.map((m) => (
              <li key={m.key}>
                <span className="font-medium">{m.label}</span>
                <span className="text-amber-800">
                  {" "}
                  · {m.drafts} draft{m.drafts === 1 ? "" : "s"}. Each listing below links to its draft.
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {skipped.length > 0 && (
        <div className="card space-y-1 p-3 text-xs text-amber-800">
          <p className="font-medium">Not sent:</p>
          <ul className="space-y-0.5">
            {skipped.map((s, i) => (
              <li key={i}>
                {items?.find((c) => c.id === s.content_id)?.original_filename ?? "A listing"}
                {s.shop_name ? ` → ${s.shop_name}` : ""}: {s.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {progress && (
        <div className="card space-y-2 p-3">
          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-700">{progress.label}…</span>
            <span className="text-xs text-slate-500">
              {progress.done + progress.failed + progress.paused + progress.running}/{progress.total}
              {progress.failed > 0 && ` · ${progress.failed} failed`}
              {progress.paused > 0 && ` · ${progress.paused} waiting`}
              {progress.running > 0 && ` · ${progress.running} still running`}
              {progress.skipped > 0 && ` · ${progress.skipped} skipped`}
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
            <p role="status" className="text-xs text-amber-800">
              {progress.paused} {progress.paused === 1 ? "listing is" : "listings are"} queued, not
              failed. {progress.pause.message} That is around{" "}
              {resumeTime(progress.pause.resumes_at)} your time.
            </p>
          )}
        </div>
      )}

      {error && <div className="card p-4 text-sm text-rose-700">{error}</div>}

      {items === null && !error && <p className="text-sm text-slate-400">Loading…</p>}

      {items && items.length === 0 && (
        <div className="card flex flex-col items-center gap-3 p-12 text-center">
          <p className="text-slate-500">No generated content yet for this batch.</p>
          <Link href={`/batches/${id}`} className="btn-primary">
            Generate content
          </Link>
        </div>
      )}

      {items && items.length > 0 && (
        <div className="space-y-5">
          {items.map((c) => (
            <ReviewCard
              key={cardKey(c)}
              initial={c}
              onChange={onCardChange}
              targets={targets?.map((t) => t.connection_id)}
              shopNames={shopNames}
            />
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Where "Create drafts" sends the approved listings (v5 §E). Each shop builds its
 * draft from its own profile; a shop that cannot take a listing says why, and the
 * estimate is checked against what can still be spent today before anything runs.
 */
function TargetPanel({
  shops,
  mode,
  setMode,
  chosen,
  setChosen,
  preview,
}: {
  shops: { id: string; name: string }[];
  mode: "own" | "chosen";
  setMode: (m: "own" | "chosen") => void;
  chosen: Record<string, string | null>;
  setChosen: (c: Record<string, string | null>) => void;
  preview: PublishPreview | null;
}) {
  const byShop = Object.fromEntries((preview?.shops ?? []).map((s) => [s.connection_id, s]));
  const toggle = (id: string) => {
    const next = { ...chosen };
    if (id in next) delete next[id];
    else next[id] = null;
    setChosen(next);
  };
  return (
    <section className="card space-y-4 p-5" aria-labelledby="targets-heading">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <h2 id="targets-heading" className="text-sm font-medium text-slate-700">
          Send drafts to
        </h2>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="radio" checked={mode === "own"} onChange={() => setMode("own")} />
          Each listing&apos;s own shop
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input type="radio" checked={mode === "chosen"} onChange={() => setMode("chosen")} />
          These shops
        </label>
      </div>

      {mode === "chosen" && (
        <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
          {shops.map((shop) => {
            const on = shop.id in chosen;
            const info = byShop[shop.id];
            const needsChoice = info?.blocked.some((b) => b.reason.includes("choose one"));
            return (
              <li key={shop.id} className="flex flex-wrap items-center gap-3 px-3 py-2.5">
                <label className="flex min-w-0 flex-1 items-center gap-2 text-sm text-slate-800">
                  <input type="checkbox" checked={on} onChange={() => toggle(shop.id)} />
                  <span className="truncate">{shop.name}</span>
                </label>
                {on && info && (
                  <span className="text-xs text-slate-500">
                    {info.ready} ready
                    {info.blocked.length > 0 && ` · ${info.blocked.length} cannot go`}
                  </span>
                )}
                {on && info && info.profiles.length > 0 && (
                  <select
                    className="field w-48 py-1 text-xs"
                    aria-label={`Profile for ${shop.name}`}
                    value={chosen[shop.id] ?? ""}
                    onChange={(e) => setChosen({ ...chosen, [shop.id]: e.target.value || null })}
                  >
                    <option value="">{needsChoice ? "Choose a profile…" : "Matching profile"}</option>
                    {info.profiles.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                        {p.is_fresh ? "" : " (needs refresh)"}
                      </option>
                    ))}
                  </select>
                )}
                {on && info && info.blocked.length > 0 && (
                  <p className="w-full text-xs text-amber-800">
                    {Array.from(new Set(info.blocked.map((b) => b.reason))).join("; ")}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {preview && (
        <p className={"text-xs " + (preview.fits ? "text-slate-500" : "text-amber-800")}>
          {preview.fits
            ? `${preview.drafts} draft${preview.drafts === 1 ? "" : "s"} ≈ ${preview.estimated_calls.toLocaleString()} Etsy requests (about ${preview.calls_per_draft} each); ${preview.budget_remaining.toLocaleString()} can still be spent today.`
            : preview.message}
        </p>
      )}
    </section>
  );
}
