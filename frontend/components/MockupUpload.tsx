"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { MockupDesign, MockupEdits, MockupOutput, MockupRun, MockupTemplate, MockupZip, SlotIn } from "@/lib/types";
import { DesignEditor } from "@/components/DesignEditor";
import { MockupTile } from "@/components/MockupTile";
import { editorTiles, keepPolling, pairKey, stampsOf, tileState, updatingLine, type Optimistic, type TileState } from "@/lib/mockupTiles";
import { announceRun } from "@/components/MockupRunWatcher";
import { designAnchor } from "@/lib/mockupRuns";
import { FileDrop } from "@/components/FileDrop";
import { filesFromDrop, kindOf, pick as pickFiles } from "@/lib/fileDrop";
import {
  GARMENT_MODE_LABELS,
  LIMIT_NOTE,
  MAX_PHOTOS_PER_RUN,
  NEEDS_CHECK,
  SIDE_LABELS,
  type SlotFile,
  USE_ON_LABELS,
  countLine,
  designNote,
  designSlotsLine,
  filesBySlot,
  megabytes,
  mockupCount,
  printable,
  runLine,
  selectionLine,
  skippedLine,
  uploadSlots,
} from "@/lib/mockups";

import { Txt } from "@/components/Txt";
/**
 * "Make mockups from designs" on the Uploads page (backend api/mockups.py).
 *
 * 1. The seller picks one or more templates, and uploads one or many designs (each up
 *    to 30 MB, with progress; too-large files are refused before anything is sent).
 *    Each design's SKU, read from its file name, can be changed.
 * 2. The worker places every design on every confirmed photo of the chosen templates
 *    (10 photos: 10 mockups per design); photos still waiting for the seller's check are
 *    left out, and the page says how many. Progress per design.
 * 3. Per design or for all: "Use for listings" (a listing group per design, the
 *    chosen colour as its cover) and/or "Download" (a ZIP, private, kept 24 hours).
 *
 * When a chosen template is split into subgroups ("White", "Black"), there is a slot per
 * subgroup: files dropped on a slot are for it; files dropped together pair into one design by
 * name plus each subgroup's suffix ("BR100-white", "BR100-black"); a file that pairs with
 * nothing goes to the slot its ink suggests (light ink: dark garments). Files can be dragged
 * (or moved with "Move to") between slots. One design still makes one listing; each
 * subgroup's mockups use its own file (pipeline/subgroups.py).
 *
 * No Etsy call, no AI call; mockups do not count toward Listings generated.
 */
type Upload = { key: string; file: File; pct: number; state: "waiting" | "sending" | "done" | "error"; message?: string };

const STALL_MS = 5 * 60_000;

export function MockupUpload({ shop, initialTemplates, initialRun, initialDesign }: {
  shop: string | null; initialTemplates?: string[];
  /** A run opened from its notification (lib/mockupRuns.ts), and the design to scroll to. */
  initialRun?: string | null; initialDesign?: string | null;
}) {
  const router = useRouter();
  const [templates, setTemplates] = useState<MockupTemplate[] | null>(null);
  const [chosen, setChosen] = useState<string[]>(initialTemplates ?? []);
  const [sides, setSides] = useState<"both" | "front" | "back">("both");
  const [garmentMode, setGarmentMode] = useState<"same" | "order">("same");
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [designs, setDesigns] = useState<MockupDesign[]>([]);
  // The designs ticked for mockups (D3); null: every design.
  const [picked, setPicked] = useState<string[] | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [phase, setPhase] = useState<"idle" | "uploading" | "ready" | "making" | "done">("idle");
  const [run, setRun] = useState<MockupRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stalled, setStalled] = useState(false);
  const lastProgress = useRef({ done: -1, at: 0 });
  const batchRef = useRef<Promise<string> | null>(null);

  useEffect(() => {
    api.mockupTemplates()
      // Templates named in the link ("Add designs") start chosen, if they have a confirmed photo.
      .then((all) => {
        setTemplates(all);
        setChosen((now) => now.filter((id) => all.some((t) => t.id === id && t.confirmed > 0)));
      })
      .catch((e) => {
        setTemplates([]);
        setError(String(e.message ?? e));
      });
  }, []);

  // A run opened from its notification: its batch's designs and its results, scrolled to the design.
  useEffect(() => {
    if (!initialRun) return;
    let gone = false;
    api.mockupRun(initialRun).then(async (r) => {
      if (gone) return;
      setRun(r);
      setBatchId(r.batch_id);
      // As it was made: its templates chosen and its designs ticked.
      if (r.template_ids?.length) setChosen(r.template_ids);
      setPicked(r.designs.map((d) => d.design_id));
      batchRef.current = Promise.resolve(r.batch_id);
      setPhase(r.status === "succeeded" || r.status === "failed" ? "done" : "making");
      try {
        setDesigns(await api.batchDesigns(r.batch_id));
      } catch {
        // the results still show
      }
    }).catch((e) => setError(String(e.message ?? e)));
    return () => {
      gone = true;
    };
  }, [initialRun]);

  // The run's progress, until it is finished and no mockup is being made again; said when it seems stuck.
  useEffect(() => {
    if (!run || !keepPolling(run)) return;
    const t = setTimeout(async () => {
      try {
        const next = await api.mockupRun(run.id);
        const now = Date.now();
        if (next.done !== lastProgress.current.done) lastProgress.current = { done: next.done, at: now };
        setStalled(now - lastProgress.current.at > STALL_MS);
        setRun(next);
        if (next.status === "succeeded" || next.status === "failed") setPhase("done");
      } catch (e: any) {
        setError(String(e.message ?? e));
      }
    }, 1200);
    return () => clearTimeout(t);
  }, [run]);

  const toggle = (id: string, on: boolean) =>
    setChosen((now) => (templates ?? []).map((t) => t.id).filter((x) => (x === id ? on : now.includes(x))));

  // The chosen templates' subgroups, when any is split: a slot each.
  const slots: SlotIn[] = uploadSlots((templates ?? []).filter((t) => chosen.includes(t.id)));
  // A design coming back again (a second file paired into it) replaces itself in the list.
  const upsert = (list: MockupDesign[]) =>
    setDesigns((all) => [...all.filter((d) => !list.some((x) => x.id === d.id)), ...list]);
  // A design deleted (it and its files are gone on the server): out of the list, and out of
  // the run's results when mockups were made.
  async function removed(id: string) {
    setDesigns((all) => all.filter((d) => d.id !== id));
    if (run) {
      try {
        setRun(await api.mockupRun(run.id));
      } catch {
        // the results refresh on the next poll
      }
    }
  }

  // Designs are sent as soon as they are chosen or dropped: each with its own progress,
  // and one that fails (too small, not an image, over 30 MB) says why without stopping the rest.
  function choose(files: File[], slot?: string) {
    setError(null);
    const picked = pickFiles(files, { zip: true }).map(({ file, problem }, i) => ({
      key: `${Date.now()}-${i}-${file.name}-${file.size}`, file, pct: 0,
      state: problem ? "error" : "waiting", message: problem ?? undefined,
    }) as Upload);
    setUploads((now) => [...now.filter((u) => u.state !== "done"), ...picked]);
    send(picked.filter((u) => u.state === "waiting"), slot);
  }
  const update = (key: string, patch: Partial<Upload>) => setUploads((all) => all.map((u) => (u.key === key ? { ...u, ...patch } : u)));
  const [failed, setFailed] = useState<{ filename: string; reason: string }[]>([]);

  async function send(waiting: Upload[], slot?: string) {
    if (!waiting.length) return;
    setError(null);
    setPhase("uploading");
    try {
      // One batch for every drop: a second drop while the first is creating it waits for it.
      batchRef.current ??= api.createBatch(shop).then((b) => b.id);
      const id = await batchRef.current;
      setBatchId(id);
      for (const u of waiting) {
        update(u.key, { state: "sending", pct: 0 });
        try {
          if (kindOf(u.file) === "zip") {
            // A ZIP: every image in it becomes a design; what fails comes back with why.
            const res = await api.addMockupDesignArchive(id, u.file, (pct) => update(u.key, { pct }), slots);
            upsert(res.added);
            if (res.failed.length) setFailed((all) => [...all, ...res.failed]);
          } else {
            const design = await api.addMockupDesign(id, u.file, (pct) => update(u.key, { pct }),
              slots.length ? { subgroup: slot ?? null, subgroups: slots } : undefined);
            upsert([design]);
          }
          update(u.key, { state: "done", pct: 100 });
        } catch (e: any) {
          update(u.key, { state: "error", message: String(e.message ?? e) });
        }
      }
      setPhase("ready");
    } catch (e: any) {
      batchRef.current = null; // the batch was not made: the next drop tries again
      setError(String(e.message ?? e));
      setPhase("idle");
    }
  }

  async function make() {
    if (!batchId || !designs.length || !chosen.length) return;
    setError(null);
    setStalled(false);
    lastProgress.current = { done: -1, at: Date.now() };
    try {
      setPhase("making");
      const started = await api.startMockups(batchId, chosen, { sides, garment_mode: garmentMode, design_ids: ticked.length === designs.length ? null : ticked.map((d) => d.id) });
      setRun(started);
      announceRun(started); // a notification when they are done, from any page
    } catch (e: any) {
      setError(String(e.message ?? e));
      setPhase("ready");
    }
  }

  // The photos the run prints on (confirmed, by the side each shows), those it leaves out,
  // and whether any photo has several garments (then the seller says how designs fill them).
  const { photos: printed, skipped, skippedNames } = printable(templates ?? [], chosen, sides);
  const tooMany = printed.length > MAX_PHOTOS_PER_RUN;
  const multiGarment = printed.some((p) => p.areas.length > 1);
  // The designs ticked (all until the seller unticks one), and how many mockups they make.
  const ticked = designs.filter((d) => picked === null || picked.includes(d.id));
  const pick = (id: string, on: boolean) =>
    setPicked((now) => {
      const base = now ?? designs.map((d) => d.id);
      return on ? Array.from(new Set([...base, id])) : base.filter((x) => x !== id);
    });
  const mockups = mockupCount(ticked, printed, templates ?? []);

  if (templates === null) return <section className="card h-[11rem] animate-pulse" aria-hidden />;
  const busy = phase === "uploading" || phase === "making";
  const total = run?.total ?? 0;
  const progress = total ? (run?.done ?? 0) / total : 0;
  return (
    <section className="card space-y-4 p-5" data-testid="mockup-upload">
      <div>
        <h2 className="text-base font-semibold text-slate-900">Make mockups from designs</h2>
        <p className="mt-1 text-sm text-slate-500">
          Every design is placed on every photo of the templates you pick, as if printed on the fabric. Afterwards, use
          them as each design&apos;s listing photos, download them as a ZIP, or both.
        </p>
      </div>
      {templates.length === 0 ? (
        <p key="none" className="text-sm text-slate-600">
          <span>You have no mockup templates yet. </span>
          <Link href="/templates" className="tap text-brand-700 underline">Add blank garment photos</Link>
        </p>
      ) : (
        <div key="form" className="space-y-5">
          <fieldset className="space-y-2" disabled={busy || phase === "done"}>
            <legend className="label">1. Print on</legend>
            <ul className="grid gap-x-3 sm:grid-cols-2" data-testid="mockup-templates">
              {templates.map((t) => {
                const ready = t.photos.filter((p) => p.status === "ready");
                return (
                  <li key={t.id} className="min-w-0">
                    <label className="flex min-h-[2.75rem] min-w-0 items-center gap-2.5 text-sm text-slate-700">
                      <input type="checkbox" className="h-4 w-4 shrink-0 rounded border-slate-300" checked={chosen.includes(t.id)}
                        onChange={(e) => toggle(t.id, e.target.checked)} disabled={!ready.length} data-testid="mockup-template" />
                      <span className="min-w-0 truncate font-medium" title={t.name}>{t.name}</span>
                      <span className="shrink-0 text-xs text-slate-500" translate="no">
                        <span>{`${t.confirmed} of ${t.photos.length} ${t.photos.length === 1 ? "photo" : "photos"}`}</span>
                      </span>
                    </label>
                    {t.needs_check > 0 && (
                      <p key="check" className="-mt-1 mb-1 pl-6 text-xs text-amber-800" translate="no">
                        <span>{`${NEEDS_CHECK}: ${t.needs_check}. `}</span>
                        <Link href="/templates" className="tap underline">Check them</Link>
                      </p>
                    )}
                  </li>
                );
              })}
            </ul>
            {templates.some((t) => t.photos.some((p) => p.status === "preparing")) && (
              <p key="preparing" className="text-xs text-slate-500">Photos still being prepared are used once they are ready and you have checked them.</p>
            )}
          </fieldset>

          <div className="space-y-2">
            <p className="label">2. Designs</p>
            <p className="text-xs text-slate-500"><span><span>{LIMIT_NOTE}</span> Each design is named by its file name and its SKU read from it; you can change it.</span></p>
            <FileDrop onFiles={choose} disabled={busy || phase === "done"} zip testId="mockup-designs"
              label="Drop designs, a folder or a ZIP here"
              hint="Each design becomes its own listing; every one lands in the confirmed print areas." />
            {uploads.length > 0 && (
              <ul key="uploads" className="space-y-1.5">
                {uploads.filter((u) => u.state !== "done").map((u) => (
                  <li key={u.key} className="text-xs">
                    <div className="flex min-w-0 items-center justify-between gap-2">
                      <span className="min-w-0 truncate text-slate-700" title={u.file.name}>{u.file.name}</span>
                      <span className="shrink-0 tabular-nums text-slate-500" translate="no">
                        {u.state === "sending" ? `${u.pct}%` : u.state === "error" ? "Not uploaded" : megabytes(u.file.size)}
                      </span>
                    </div>
                    <div className="progress mt-1" aria-hidden><div className="progress-fill" style={{ width: `${u.pct}%` }} /></div>
                    {u.message && <p key="m" className="mt-0.5 text-rose-700">{u.message}</p>}
                  </li>
                ))}
              </ul>
            )}
            {failed.length > 0 && (
              <ul key="failed" className="space-y-1 rounded-md border border-rose-200 bg-rose-50 p-2.5 text-xs text-rose-800" data-testid="mockup-design-failed">
                {failed.map((f, i) => (
                  <li key={`${f.filename}-${i}`}><span className="font-medium" translate="no">{f.filename}</span><span>{`: ${f.reason}`}</span></li>
                ))}
              </ul>
            )}
            {slots.length > 0 && (
              <SlotBoard key="slots" slots={slots} designs={designs} disabled={busy || phase === "done"} removing={busy}
                onFiles={(files, slot) => choose(files, slot)}
                onChange={(next) => setDesigns((all) => all.map((x) => (x.id === next.id ? next : x)))}
                onRemoved={removed} />
            )}
            {designs.length > 0 && (
              <div key="pick" className="flex flex-wrap items-center gap-2 text-xs text-slate-600" data-testid="mockup-design-pick">
                <span className="min-w-0 flex-1" translate="no" data-testid="mockup-design-count">{selectionLine(ticked.length, designs.length)}</span>
                <button type="button" className="btn-secondary min-h-11 px-3 text-xs" onClick={() => setPicked(null)}
                  disabled={busy || phase === "done" || ticked.length === designs.length} data-testid="mockup-select-all">Select all</button>
                <button type="button" className="btn-secondary min-h-11 px-3 text-xs" onClick={() => setPicked([])}
                  disabled={busy || phase === "done" || !ticked.length} data-testid="mockup-select-none">Select none</button>
              </div>
            )}
            {designs.length > 0 && (
              <ul key="designs" className="divide-y divide-slate-100 rounded-md border border-slate-200" data-testid="mockup-design-list">
                {designs.map((d) => (
                  <DesignRow key={d.id} design={d} disabled={busy || phase === "done"} split={slots.length > 0} removing={busy}
                    picked={picked === null || picked.includes(d.id)} onPick={(on) => pick(d.id, on)}
                    onChange={(next) => setDesigns((all) => all.map((x) => (x.id === next.id ? next : x)))}
                    onRemoved={() => removed(d.id)} />
                ))}
              </ul>
            )}
          </div>

          <div className="space-y-2">
            <div className="grid gap-2 sm:grid-cols-2">
              <label className="block min-w-0">
                <span className="label">Print on</span>
                <select className="field w-full" value={sides} onChange={(e) => setSides(e.target.value as typeof sides)}
                  disabled={busy || phase === "done"} data-testid="mockup-sides">
                  {(Object.keys(SIDE_LABELS) as (keyof typeof SIDE_LABELS)[]).map((k) => (
                    <option key={k} value={k}>{SIDE_LABELS[k]}</option>
                  ))}
                </select>
              </label>
              {multiGarment && (
                <label key="garment-mode" className="block min-w-0">
                  <span className="label">Photos with several garments</span>
                  <select className="field w-full" value={garmentMode} onChange={(e) => setGarmentMode(e.target.value as typeof garmentMode)}
                    disabled={busy || phase === "done"} data-testid="mockup-garment-mode">
                    {(Object.keys(GARMENT_MODE_LABELS) as (keyof typeof GARMENT_MODE_LABELS)[]).map((k) => (
                      <option key={k} value={k}>{GARMENT_MODE_LABELS[k]}</option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 text-sm text-slate-600" translate="no" data-testid="mockup-count">
                {countLine(ticked.length, printed.length, mockups)}
              </span>
              <button type="button" className="btn-primary sm:ml-auto" onClick={make} data-testid="mockup-make"
                disabled={busy || phase === "done" || !ticked.length || !printed.length || tooMany}>
                {phase === "making" ? "Making…" : "3. Create mockups"}
              </button>
            </div>
            {chosen.length > 0 && skipped > 0 && (
              <div key="skipped" role="status" className="text-sm text-amber-800" data-testid="mockup-skipped">
                <p>{skippedLine(skipped)}</p>
                <p className="mt-0.5 text-xs" translate="no">{`Not used: ${(run?.skipped_photos?.length ? run.skipped_photos : skippedNames).join(", ")}`}</p>
              </div>
            )}
            {tooMany && (
              <p key="too-many" role="alert" className="text-sm text-rose-700">{`One run prints on up to ${MAX_PHOTOS_PER_RUN} photos; choose fewer templates.`}</p>
            )}
            {/* One line, the same height in every state: nothing below it moves. */}
            <div className="space-y-1.5" aria-live="polite">
              <div className="progress" aria-hidden><div className="progress-fill" style={{ width: `${Math.round(progress * 100)}%` }} /></div>
              <p className="min-h-[1.25rem] text-xs text-slate-600" translate="no" data-testid="mockup-progress">
                {run ? runLine(run) : ""}
              </p>
            </div>
            {stalled && (
              <p key="stalled" role="status" className="text-sm text-amber-800">
                This is taking longer than expected. You can leave the page; the mockups keep being made and appear in the batch.
              </p>
            )}
            {run?.error && <p key="run-error" role="alert" className="text-sm text-rose-700">{run.error}</p>}
            {error && <p key="error" role="alert" className="text-sm text-rose-700">{error}</p>}
          </div>

          {run && run.designs.length > 0 && (
            <Results key="results" run={run} onRun={setRun} onOpen={() => batchId && router.push(`/batches/${batchId}`)} focus={initialDesign ?? null} />
          )}
        </div>
      )}
    </section>
  );
}

function DesignRow({ design, disabled, split, removing, picked = true, onPick, onChange, onRemoved }: {
  design: MockupDesign; disabled: boolean; split?: boolean; removing?: boolean; onChange: (d: MockupDesign) => void;
  onRemoved: () => void;
  /** Ticked for mockups (D3). */
  picked?: boolean;
  onPick?: (on: boolean) => void;
}) {
  const [sku, setSku] = useState(design.parsed_sku ?? "");
  const [message, setMessage] = useState<string | null>(null);
  async function save() {
    if ((design.parsed_sku ?? "") === sku.trim()) return;
    setMessage(null);
    try {
      const next = await api.updateMockupDesign(design.id, sku.trim() || null);
      setSku(next.parsed_sku ?? "");
      onChange(next);
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }
  async function addBack(file: File | undefined) {
    if (!file) return;
    setMessage(null);
    try {
      onChange(await api.setDesignBack(design.id, file));
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }
  async function remove() {
    const files = 1 + (design.versions?.length ?? 0) + (design.has_back ? 1 : 0);
    if (!window.confirm(`Delete “${design.original_filename}”${files > 1 ? ` and its ${files} files` : ""}? Mockups made from it are deleted too.`)) return;
    setMessage(null);
    try {
      await api.deleteMockupDesign(design.id);
      onRemoved();
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }
  async function removeBack() {
    setMessage(null);
    try {
      onChange(await api.removeDesignBack(design.id));
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }
  return (
    <li className="flex flex-wrap items-center gap-2 p-2.5 text-sm">
      {onPick && (
        <input key="pick" type="checkbox" className="h-4 w-4 shrink-0 rounded border-slate-300" checked={picked} disabled={disabled}
          onChange={(e) => onPick(e.target.checked)} aria-label={`Use ${design.original_filename}`} data-testid="design-pick" />
      )}
      <span className="min-w-0 flex-1 truncate text-slate-700" title={design.original_filename}>{design.original_filename}</span>
      {split && (
        <span key="slots" className="min-w-0 max-w-full truncate text-xs text-slate-500" translate="no" title={designSlotsLine(design)}>
          {`Files: ${designSlotsLine(design)}`}
        </span>
      )}
      {designNote(design) && <span key="note" className="w-full text-xs text-amber-800 sm:order-last">{designNote(design)}</span>}
      <label className="flex items-center gap-1.5 text-xs text-slate-500">
        <span>SKU</span>
        <input className="field w-36" value={sku} placeholder="none" maxLength={32} disabled={disabled}
          onChange={(e) => setSku(e.target.value)} onBlur={save} aria-label={`SKU for ${design.original_filename}`} />
      </label>
      <span className="flex items-center gap-1.5 text-xs text-slate-500">
        {design.has_back ? (
          <span key="has" className="flex items-center gap-1.5">
            <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-800" data-testid="design-back-added">Back added</span>
            <button type="button" className="tap underline" disabled={disabled} onClick={removeBack}>Remove back</button>
          </span>
        ) : (
          <label key="add" className="tap cursor-pointer underline" title="Printed on back areas (front & back photos)">
            <span>Add back design</span>
            <input type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" disabled={disabled}
              onChange={(e) => addBack(e.target.files?.[0])} data-testid="design-back-file" />
          </label>
        )}
      </span>
      <button type="button" className="btn-secondary min-h-11 px-3 text-xs text-rose-700" onClick={remove} disabled={removing}
        aria-label={`Remove ${design.original_filename}`} data-testid="design-remove">
        Remove
      </button>
      {message && <p key="m" role="alert" className="w-full text-xs text-rose-700">{message}</p>}
    </li>
  );
}

/** A slot per subgroup: drop files on one (they are for it), drag a file to another, or "Move to". */
function SlotBoard({ slots, designs, disabled, removing, onFiles, onChange, onRemoved }: {
  slots: SlotIn[];
  designs: MockupDesign[];
  disabled: boolean;
  removing: boolean;
  onFiles: (files: File[], slot: string) => void;
  onChange: (design: MockupDesign) => void;
  onRemoved: (id: string) => void;
}) {
  const files = filesBySlot(designs, slots);
  const [dragging, setDragging] = useState<SlotFile | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const names = [...slots.map((s) => s.name), ...(files[""].length ? [""] : [])];

  async function move(file: SlotFile, to: string) {
    setMessage(null);
    try {
      if (file.versionId) {
        if (!to) return; // a second file is always for a subgroup
        onChange(await api.moveMockupDesignVersion(file.versionId, to));
      } else {
        onChange(await api.moveMockupDesign(file.design.id, to || null));
      }
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }

  // One file out of a design: a version, or its own file (a version then takes its place;
  // with none left, the whole design goes).
  async function removeFile(file: SlotFile) {
    if (!window.confirm(`Remove “${file.filename}”?`)) return;
    setMessage(null);
    try {
      if (file.versionId) {
        onChange(await api.deleteMockupDesignVersion(file.versionId));
      } else {
        const res = await api.deleteMockupDesign(file.design.id, true);
        if (res.design) onChange(res.design);
        else onRemoved(file.design.id);
      }
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }

  return (
    <div className="space-y-2" data-testid="mockup-slots">
      <p className="text-xs text-slate-500">
        Files dropped together pair into one design by name and each subgroup&apos;s suffix; a file that pairs with nothing goes where its ink colour suggests. Drag a file to another slot to change it.
      </p>
      <ul className="grid gap-3 sm:grid-cols-2">
        {names.map((name) => {
          const slot = slots.find((s) => s.name === name);
          return (
            <li key={name || "~every"} className={`min-w-0 rounded-lg border-2 border-dashed p-3 ${over === name ? "border-brand-500 bg-brand-50" : "border-slate-300 bg-white"}`}
              onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(name); }}
              onDragLeave={() => setOver(null)}
              onDrop={async (e) => {
                e.preventDefault();
                setOver(null);
                if (disabled) return;
                if (dragging) {
                  const f = dragging;
                  setDragging(null);
                  await move(f, name);
                  return;
                }
                const dropped = await filesFromDrop(e.dataTransfer);
                if (dropped.length && name) onFiles(dropped, name);
              }}
              data-testid="mockup-slot" data-slot={name}>
              <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
                <p className="min-w-0 truncate text-sm font-medium text-slate-800" title={name || "Every subgroup"} translate="no">{name || "Every subgroup"}</p>
                <span className="text-xs text-slate-500" translate="no">
                  {slot ? [USE_ON_LABELS[slot.use_on as keyof typeof USE_ON_LABELS] ?? "", slot.suffix ? `suffix ${slot.suffix}` : ""].filter(Boolean).join(" · ") : "printed for every subgroup"}
                </span>
              </div>
              <ul className="mt-2 space-y-1.5">
                {files[name].map((f) => (
                  <li key={`${f.design.id}-${f.versionId ?? "own"}`} draggable={!disabled}
                    onDragStart={(e) => { e.dataTransfer.effectAllowed = "move"; setDragging(f); }}
                    onDragEnd={() => setDragging(null)}
                    className="flex min-w-0 cursor-grab flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs"
                    data-testid="mockup-slot-file">
                    <span className="min-w-[8rem] flex-1 truncate text-slate-700" title={f.filename} translate="no">{f.filename}</span>
                    <label className="w-full min-w-0 sm:w-auto sm:max-w-[50%]">
                      <span className="sr-only">{`Move ${f.filename} to`}</span>
                      <select className="field w-full text-xs" value={name} disabled={disabled}
                        onChange={(e) => move(f, e.target.value)} data-testid="mockup-slot-move">
                        {slots.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
                        {!f.versionId && <option key="~every" value="">Every subgroup</option>}
                      </select>
                    </label>
                    <button type="button" className="btn-secondary min-h-11 shrink-0 px-2 text-xs text-rose-700" onClick={() => removeFile(f)}
                      disabled={removing} aria-label={`Remove ${f.filename}`} data-testid="mockup-slot-remove">
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
              {name && (
                <label key="choose" className="btn-secondary mt-2 min-h-11 w-full cursor-pointer text-sm">
                  <span>{`Add ${name} files`}</span>
                  <input type="file" multiple accept="image/png,image/jpeg,image/webp" className="sr-only" disabled={disabled}
                    onChange={(e) => { const chosen = Array.from(e.target.files ?? []); e.target.value = ""; if (chosen.length) onFiles(chosen, name); }}
                    data-testid="mockup-slot-files" />
                </label>
              )}
            </li>
          );
        })}
      </ul>
      {message && <p key="m" role="alert" className="text-xs text-rose-700">{message}</p>}
    </div>
  );
}

/** After a run: per design or for all, "Use for listings" (with a cover) and/or "Download". */
function Results({ run, onRun, onOpen, focus }: { run: MockupRun; onRun: (r: MockupRun) => void; onOpen: () => void; focus?: string | null }) {
  // Opened from the notification: scrolled to that design's results and highlighted for a moment.
  const [highlight, setHighlight] = useState<string | null>(null);
  useEffect(() => {
    if (!focus || !run.designs.some((d) => d.design_id === focus)) return;
    const el = document.getElementById(designAnchor(focus));
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
    setHighlight(focus);
    const t = setTimeout(() => setHighlight(null), 4000);
    return () => clearTimeout(t);
  }, [focus, run.designs.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const finished = run.status === "succeeded" || run.status === "failed";
  // Mockups can be opened in the editor once the run was made, also while some are being made again.
  const editable = finished || (run.refreshing ?? []).length > 0;
  const [selected, setSelected] = useState<string[]>(run.designs.map((d) => d.design_id));
  const [covers, setCovers] = useState<Record<string, string>>({});
  const [layout, setLayout] = useState<"folders" | "flat">("folders");
  const [busy, setBusy] = useState<"listings" | "zip" | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [zip, setZip] = useState<MockupZip | null>(null);
  // "Adjust": one design on one photo, in the design editor (Part B). The run (from the page),
  // the edits and the mockups just asked for are the one state the tiles here and the editor's
  // strip both read (lib/mockupTiles.ts): whatever is open shows the latest.
  const [edits, setEdits] = useState<MockupEdits | null>(null);
  const [editing, setEditing] = useState<{ designId: string; photoId: string } | null>(null);
  const [optimistic, setOptimistic] = useState<Optimistic>({});
  const [touched, setTouched] = useState<string[]>([]);
  async function adjust(designId: string, photoId: string) {
    setError(null);
    try {
      setEdits(await api.mockupEdits(run.id));
      setTouched([]);
      setEditing({ designId, photoId });
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }
  /** Edits of these mockups changed: reload the edits (the previews draw them) and show the
   * tiles as updating until the server answers. */
  async function edited(pairs: [string, string][]) {
    setOptimistic((now) => ({ ...now, ...stampsOf(run, pairs) }));
    setTouched((now) => Array.from(new Set([...now, ...pairs.map(([d, p]) => pairKey(d, p))])));
    try {
      setEdits(await api.mockupEdits(run.id));
    } catch {
      // the tiles show "Updating…" over the earlier image
    }
  }
  /** The server's answer (or null when the request failed): its run is the state from now on. */
  function ran(next: MockupRun | null) {
    setOptimistic({});
    if (next) onRun(next);
  }
  async function retry(designId: string, photoId: string) {
    setError(null);
    setOptimistic((now) => ({ ...now, ...stampsOf(run, [[designId, photoId]]) }));
    try {
      ran(await api.rerenderMockups(run.id, [[designId, photoId]]));
    } catch (e: any) {
      ran(null);
      setError(String(e.message ?? e));
    }
  }
  const stateOf = (designId: string, o: MockupOutput): TileState => tileState(run, designId, o, optimistic);

  useEffect(() => {
    if (!zip || zip.status === "ready" || zip.status === "failed" || zip.status === "expired") return;
    const t = setTimeout(async () => {
      try {
        setZip(await api.mockupZip(zip.id));
      } catch (e: any) {
        setError(String(e.message ?? e));
      }
    }, 1200);
    return () => clearTimeout(t);
  }, [zip]);

  const ids = selected.length === run.designs.length ? null : selected;
  async function useForListings() {
    setBusy("listings");
    setError(null);
    try {
      const res = await api.useMockupsForListings(run.id, ids, covers);
      setSaid(`${res.groups} ${res.groups === 1 ? "listing" : "listings"} ready to write, ${res.photos} photos.`);
      onRun(await api.mockupRun(run.id));
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(null);
    }
  }
  async function download() {
    setBusy("zip");
    setError(null);
    try {
      setZip(await api.requestMockupZip(run.id, ids, layout));
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3 border-t border-slate-200 pt-4" data-testid="mockup-results">
      <p className="label">4. Your mockups</p>
      <ul className="space-y-2">
        {run.designs.map((d) => (
          <li key={d.design_id} id={designAnchor(d.design_id)}
            className={`rounded-md border p-2.5 text-sm transition-shadow ${highlight === d.design_id ? "border-brand-500 ring-4 ring-brand-200" : "border-slate-200"}`}
            data-testid="mockup-result" data-highlight={highlight === d.design_id ? "true" : undefined}>
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex min-h-[2.75rem] min-w-0 flex-1 items-center gap-2.5">
                <input type="checkbox" className="h-4 w-4 shrink-0 rounded border-slate-300" disabled={!finished}
                  checked={selected.includes(d.design_id)}
                  onChange={(e) => setSelected((all) => (e.target.checked ? [...all, d.design_id] : all.filter((x) => x !== d.design_id)))} />
                <span className="min-w-0 truncate font-medium text-slate-800" title={d.original_filename}>{d.sku ?? d.original_filename}</span>
              </label>
              <span className="shrink-0 text-xs tabular-nums text-slate-500" translate="no">
                <span><Txt>{d.in_listings ? "in listings · " : ""}</Txt><span>{d.done}</span><span> / </span><span>{d.total}</span></span>
              </span>
            </div>
            {d.outputs.length > 0 && (
              <ul key="thumbs" className="mt-2 flex gap-2 overflow-x-auto pb-1" data-testid="mockup-thumbs">
                {d.outputs.map((o) => (
                  <li key={o.id} className="shrink-0">
                    <MockupTile runId={run.id} designId={d.design_id} output={o} state={stateOf(d.design_id, o)} edits={edits}
                      label={`${d.sku ?? d.original_filename} on ${o.photo_name ?? o.template_name}`}
                      disabled={!editable || !o.photo_id || !o.available}
                      onOpen={() => o.photo_id && adjust(d.design_id, o.photo_id)}
                      onRetry={() => o.photo_id && retry(d.design_id, o.photo_id)} />
                  </li>
                ))}
              </ul>
            )}
            {finished && d.outputs.length > 0 && (
              <label key="cover" className="mt-1 flex min-w-0 items-center gap-2 text-xs text-slate-600">
                <span className="shrink-0">Cover</span>
                <select className="field min-w-0 flex-1 text-xs" value={covers[d.design_id] ?? d.outputs[0].photo_id ?? ""}
                  onChange={(e) => setCovers((all) => ({ ...all, [d.design_id]: e.target.value }))} data-testid="mockup-cover">
                  {d.outputs.map((o) => (
                    <option key={o.id} value={o.photo_id ?? ""} disabled={!o.photo_id}>
                      {o.photo_name && o.photo_name !== o.template_name ? `${o.template_name} – ${o.photo_name}` : o.template_name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </li>
        ))}
      </ul>
      {finished && (
        <div key="actions" className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn-primary" onClick={useForListings} disabled={!!busy || !selected.length} data-testid="mockup-use">
            {busy === "listings" ? "Working…" : "Use for listings"}
          </button>
          <label className="flex items-center gap-1.5 text-xs text-slate-600">
            <span>ZIP</span>
            <select className="field text-xs" value={layout} onChange={(e) => setLayout(e.target.value as "folders" | "flat")}>
              <option value="folders">A folder per design</option>
              <option value="flat">All in one folder</option>
            </select>
          </label>
          <button type="button" className="btn-secondary" onClick={download} disabled={!!busy || !selected.length} data-testid="mockup-zip">
            {busy === "zip" ? "Working…" : "Download"}
          </button>
        </div>
      )}
      {said && (
        <div key="said" role="status" className="flex flex-wrap items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2">
          <p className="min-w-0 flex-1 text-sm font-medium text-emerald-900">{said}</p>
          <button type="button" className="btn-primary min-h-11" onClick={onOpen} data-testid="mockup-open-batch">Open batch</button>
        </div>
      )}
      {zip && (
        <p key="zip" role="status" className="text-sm text-slate-700" translate="no" data-testid="mockup-zip-status">
          {zip.status === "ready" ? (
            <span key="ready">
              <a className="btn-primary min-h-11" href={api.mockupZipUrl(zip.id)} download data-testid="mockup-zip-download">Download the ZIP</a>
              <span className="ml-2">{` ${zip.files} files, ${megabytes(zip.byte_size ?? 0)}. Kept 24 hours, then deleted.`}</span>
            </span>
          ) : zip.status === "failed" ? (
            <span key="failed" className="text-rose-700">{zip.error ?? "The ZIP could not be made."}</span>
          ) : zip.status === "expired" ? (
            <span key="expired">This download has expired; make a new one.</span>
          ) : (
            <span key="building">Making the ZIP…</span>
          )}
        </p>
      )}
      {error && <p key="error" role="alert" className="text-sm text-rose-700">{error}</p>}
      {editing && edits && (
        <DesignEditor key={`${editing.designId}-${editing.photoId}`} runId={run.id} edits={edits} designId={editing.designId} photoId={editing.photoId}
          onClose={() => setEditing(null)} onEdited={edited} onRun={ran}
          strip={<EditorStrip run={run} edits={edits} designId={editing.designId} touched={touched} stateOf={stateOf} onRetry={retry} />} />
      )}
    </div>
  );
}

/** In the design editor: this design's mockups and every other one changed here, each updating
 * live (a preview at once, then the new file) while the editor stays open. */
function EditorStrip({ run, edits, designId, touched, stateOf, onRetry }: {
  run: MockupRun;
  edits: MockupEdits | null;
  designId: string;
  touched: string[];
  stateOf: (designId: string, o: MockupOutput) => TileState;
  onRetry: (designId: string, photoId: string) => void;
}) {
  const tiles = editorTiles(run, designId, touched);
  const states = tiles.map((t) => stateOf(t.designId, t.output));
  if (!tiles.length) return null;
  return (
    <section className="space-y-2" aria-label="Mockups" data-testid="design-editor-tiles">
      <div className="flex items-baseline justify-between gap-2">
        <p className="label">Mockups</p>
        <p className="min-w-0 truncate text-xs text-slate-500" translate="no" data-testid="design-editor-tiles-line">{updatingLine(states)}</p>
      </div>
      <ul className="flex gap-2 overflow-x-auto pb-1">
        {tiles.map((t, i) => (
          <li key={`${t.designId}-${t.output.id}`} className="shrink-0">
            <MockupTile runId={run.id} designId={t.designId} output={t.output} state={states[i]} edits={edits} small
              label={`${run.designs.find((d) => d.design_id === t.designId)?.sku ?? ""} on ${t.output.photo_name ?? t.output.template_name}`}
              onRetry={() => t.output.photo_id && onRetry(t.designId, t.output.photo_id)} />
          </li>
        ))}
      </ul>
    </section>
  );
}
