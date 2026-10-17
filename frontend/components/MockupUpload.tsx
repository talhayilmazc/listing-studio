"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { MockupDesign, MockupRun, MockupTemplate, MockupZip } from "@/lib/types";
import { FileDrop } from "@/components/FileDrop";
import { kindOf, pick as pickFiles } from "@/lib/fileDrop";
import {
  GARMENT_MODE_LABELS,
  LIMIT_NOTE,
  MAX_PHOTOS_PER_RUN,
  NEEDS_CHECK,
  SIDE_LABELS,
  designNote,
  megabytes,
  printable,
  runLine,
  skippedLine,
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
 * No Etsy call, no AI call; mockups do not count toward Listings generated.
 */
type Upload = { key: string; file: File; pct: number; state: "waiting" | "sending" | "done" | "error"; message?: string };

const STALL_MS = 5 * 60_000;

export function MockupUpload({ shop, initialTemplates }: { shop: string | null; initialTemplates?: string[] }) {
  const router = useRouter();
  const [templates, setTemplates] = useState<MockupTemplate[] | null>(null);
  const [chosen, setChosen] = useState<string[]>(initialTemplates ?? []);
  const [sides, setSides] = useState<"both" | "front" | "back">("both");
  const [garmentMode, setGarmentMode] = useState<"same" | "order">("same");
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [designs, setDesigns] = useState<MockupDesign[]>([]);
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

  // The run's progress, until it is finished; said when it seems stuck.
  useEffect(() => {
    if (!run || run.status === "succeeded" || run.status === "failed") return;
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

  // Designs are sent as soon as they are chosen or dropped: each with its own progress,
  // and one that fails (too small, not an image, over 30 MB) says why without stopping the rest.
  function choose(files: File[]) {
    setError(null);
    const picked = pickFiles(files, { zip: true }).map(({ file, problem }, i) => ({
      key: `${Date.now()}-${i}-${file.name}-${file.size}`, file, pct: 0,
      state: problem ? "error" : "waiting", message: problem ?? undefined,
    }) as Upload);
    setUploads((now) => [...now.filter((u) => u.state !== "done"), ...picked]);
    send(picked.filter((u) => u.state === "waiting"));
  }
  const update = (key: string, patch: Partial<Upload>) => setUploads((all) => all.map((u) => (u.key === key ? { ...u, ...patch } : u)));
  const [failed, setFailed] = useState<{ filename: string; reason: string }[]>([]);

  async function send(waiting: Upload[]) {
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
            const res = await api.addMockupDesignArchive(id, u.file, (pct) => update(u.key, { pct }));
            setDesigns((all) => [...all, ...res.added]);
            if (res.failed.length) setFailed((all) => [...all, ...res.failed]);
          } else {
            const design = await api.addMockupDesign(id, u.file, (pct) => update(u.key, { pct }));
            setDesigns((all) => [...all, design]);
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
      setRun(await api.startMockups(batchId, chosen, { sides, garment_mode: garmentMode }));
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
            {designs.length > 0 && (
              <ul key="designs" className="divide-y divide-slate-100 rounded-md border border-slate-200" data-testid="mockup-design-list">
                {designs.map((d) => (
                  <DesignRow key={d.id} design={d} disabled={busy || phase === "done"}
                    onChange={(next) => setDesigns((all) => all.map((x) => (x.id === next.id ? next : x)))} />
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
              <span className="min-w-0 text-sm text-slate-600" translate="no">
                {`${designs.length} ${designs.length === 1 ? "design" : "designs"} × ${printed.length} ${printed.length === 1 ? "photo" : "photos"} = ${designs.length * printed.length} mockups`}
              </span>
              <button type="button" className="btn-primary sm:ml-auto" onClick={make} data-testid="mockup-make"
                disabled={busy || phase === "done" || !designs.length || !printed.length || tooMany}>
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
            <Results key="results" run={run} onRun={setRun} onOpen={() => batchId && router.push(`/batches/${batchId}`)} />
          )}
        </div>
      )}
    </section>
  );
}

function DesignRow({ design, disabled, onChange }: { design: MockupDesign; disabled: boolean; onChange: (d: MockupDesign) => void }) {
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
      <span className="min-w-0 flex-1 truncate text-slate-700" title={design.original_filename}>{design.original_filename}</span>
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
      {message && <p key="m" role="alert" className="w-full text-xs text-rose-700">{message}</p>}
    </li>
  );
}

/** After a run: per design or for all, "Use for listings" (with a cover) and/or "Download". */
function Results({ run, onRun, onOpen }: { run: MockupRun; onRun: (r: MockupRun) => void; onOpen: () => void }) {
  const finished = run.status === "succeeded" || run.status === "failed";
  const [selected, setSelected] = useState<string[]>(run.designs.map((d) => d.design_id));
  const [covers, setCovers] = useState<Record<string, string>>({});
  const [layout, setLayout] = useState<"folders" | "flat">("folders");
  const [busy, setBusy] = useState<"listings" | "zip" | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [zip, setZip] = useState<MockupZip | null>(null);

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
          <li key={d.design_id} className="rounded-md border border-slate-200 p-2.5 text-sm">
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
        <p key="said" role="status" className="text-sm text-emerald-700">
          <span><span><span>{said}</span> </span></span>
          <button type="button" className="tap text-brand-700 underline" onClick={onOpen}>Open batch</button>
        </p>
      )}
      {zip && (
        <p key="zip" role="status" className="text-sm text-slate-700" translate="no" data-testid="mockup-zip-status">
          {zip.status === "ready" ? (
            <span key="ready">
              <a className="tap text-brand-700 underline" href={api.mockupZipUrl(zip.id)} download>Download the ZIP</a>
              <span>{` (${zip.files} files, ${megabytes(zip.byte_size ?? 0)}). Kept 24 hours, then deleted.`}</span>
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
    </div>
  );
}
