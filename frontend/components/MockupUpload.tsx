"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { MockupRun, MockupTemplate } from "@/lib/types";
import { MAX_TEMPLATES_PER_RUN, runLine } from "@/lib/mockups";

/**
 * "Make mockups from designs" on the Uploads page (backend api/mockups.py).
 *
 * The seller picks design files and the templates to print them on (in their
 * template order: the first is each listing's cover). The designs are uploaded
 * to a new batch, the worker makes the mockups, and each design's mockups become
 * its listing group. No Etsy call, no AI call; mockups do not count toward
 * Listings generated.
 */
export function MockupUpload({ shop }: { shop: string | null }) {
  const router = useRouter();
  const [templates, setTemplates] = useState<MockupTemplate[] | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [phase, setPhase] = useState<"idle" | "uploading" | "making" | "done">("idle");
  const [sent, setSent] = useState(0);
  const [run, setRun] = useState<MockupRun | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api
      .mockupTemplates()
      .then((all) => {
        setTemplates(all);
        setChosen(all.slice(0, MAX_TEMPLATES_PER_RUN).map((t) => t.id));
      })
      .catch(() => setTemplates([]));
  }, []);

  // The run's progress, until it is finished.
  useEffect(() => {
    if (!run || run.status === "succeeded" || run.status === "failed") return;
    const t = setTimeout(async () => {
      try {
        const next = await api.mockupRun(run.id);
        setRun(next);
        if (next.status === "succeeded" || next.status === "failed") setPhase("done");
      } catch (e: any) {
        setError(String(e.message ?? e));
      }
    }, 1200);
    return () => clearTimeout(t);
  }, [run]);

  function toggle(id: string, on: boolean) {
    setChosen((ids) => {
      const next = on ? [...ids, id] : ids.filter((x) => x !== id);
      // Kept in the seller's template order.
      return (templates ?? []).map((t) => t.id).filter((x) => next.includes(x)).slice(0, MAX_TEMPLATES_PER_RUN);
    });
  }

  async function make() {
    if (!files.length || !chosen.length) return;
    setError(null);
    setPhase("uploading");
    setSent(0);
    try {
      const batch = await api.createBatch(shop);
      setBatchId(batch.id);
      for (const file of files) {
        await api.addMockupDesign(batch.id, file);
        setSent((n) => n + 1);
      }
      setPhase("making");
      setRun(await api.startMockups(batch.id, chosen));
    } catch (e: any) {
      setError(String(e.message ?? e));
      setPhase("idle");
    }
  }

  if (templates === null) return <section className="card h-[11rem] animate-pulse" aria-hidden />;
  const busy = phase === "uploading" || phase === "making";
  const total = run?.total ?? 0;
  const progress = phase === "uploading" ? (files.length ? sent / files.length : 0) : total ? (run?.done ?? 0) / total : 0;
  return (
    <section className="card space-y-4 p-5" data-testid="mockup-upload">
      <div>
        <h2 className="text-base font-semibold text-slate-900">Make mockups from designs</h2>
        <p className="mt-1 text-sm text-slate-500">
          Each design is placed on your blank garment photos as if printed on the fabric, and its mockups become its
          listing, the first template&apos;s as the cover. Transparent PNG designs work best.
        </p>
      </div>
      {templates.length === 0 ? (
        <p key="none" className="text-sm text-slate-600">
          <span>You have no mockup templates yet. </span>
          <Link href="/templates" className="tap text-brand-700 underline">Add a blank garment photo</Link>
        </p>
      ) : (
        <div key="form" className="space-y-4">
          <fieldset className="space-y-1" disabled={busy}>
            <legend className="label">Print on (in your template order)</legend>
            <ul className="grid gap-1 sm:grid-cols-2">
              {templates.map((t, i) => (
                <li key={t.id}>
                  <label className="flex min-h-[2.75rem] items-center gap-2.5 text-sm text-slate-700">
                    <input type="checkbox" className="h-4 w-4 rounded border-slate-300" checked={chosen.includes(t.id)}
                      onChange={(e) => toggle(t.id, e.target.checked)} data-testid="mockup-template" />
                    <span className="min-w-0 truncate" title={t.name}>{t.name}</span>
                    {i === 0 && <span key="cover" className="shrink-0 text-xs text-slate-400">first in order</span>}
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
          <div className="flex flex-wrap items-center gap-2">
            <input ref={input} type="file" multiple accept="image/png,image/jpeg,image/webp" className="hidden"
              data-testid="mockup-designs" onChange={(e) => setFiles(Array.from(e.target.files ?? []))} />
            <button type="button" className="btn-secondary" onClick={() => input.current?.click()} disabled={busy}>
              Choose designs
            </button>
            <span className="min-w-0 text-sm text-slate-600" translate="no">
              {files.length ? `${files.length} ${files.length === 1 ? "design" : "designs"} × ${chosen.length} ${chosen.length === 1 ? "template" : "templates"}` : "No designs chosen"}
            </span>
            <button type="button" className="btn-primary sm:ml-auto" onClick={make} disabled={busy || !files.length || !chosen.length}
              data-testid="mockup-make">
              {busy ? "Working…" : "Make mockups"}
            </button>
          </div>
          {/* One line, the same height in every state: nothing below it moves. */}
          <div className="space-y-1.5" aria-live="polite">
            <div className="progress" aria-hidden><div className="progress-fill" style={{ width: `${Math.round(progress * 100)}%` }} /></div>
            <p className="min-h-[1.25rem] text-xs text-slate-600" translate="no" data-testid="mockup-progress">
              {phase === "uploading" ? `Uploading designs: ${sent} of ${files.length}` : run ? runLine(run) : ""}
            </p>
          </div>
          {run?.error && <p key="run-error" role="alert" className="text-sm text-rose-700">{run.error}</p>}
          {error && <p key="error" role="alert" className="text-sm text-rose-700">{error}</p>}
          {phase === "done" && batchId && (
            <button key="open" type="button" className="btn-primary" onClick={() => router.push(`/batches/${batchId}`)}>
              Open batch
            </button>
          )}
        </div>
      )}
    </section>
  );
}
