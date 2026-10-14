"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { MockupTemplate } from "@/lib/types";
import {
  ACCURACY_NOTE,
  FOLD_RANGE,
  RIGHTS_STATEMENT,
  SIZE_RANGE,
  TEMPLATES_INTRO,
  moveCorner,
  percent,
  type Corner,
} from "@/lib/mockups";

/**
 * Mockup templates (backend api/mockups.py): the seller's blank garment photos.
 * Each has a print area (four corners, dragged or moved with the keyboard), a
 * size and a fold strength; a preview prints a sample design with the unsaved
 * settings. Nothing here calls Etsy or an AI model.
 */
export default function TemplatesPage() {
  const [templates, setTemplates] = useState<MockupTemplate[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.mockupTemplates().then(setTemplates).catch((e) => setError(String(e.message ?? e)));
  }, []);
  useEffect(load, [load]);

  async function move(id: string, by: -1 | 1) {
    if (!templates) return;
    const ids = templates.map((t) => t.id);
    const i = ids.indexOf(id);
    const j = i + by;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    try {
      setTemplates(await api.orderMockupTemplates(ids));
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  return (
    <div className="space-y-8">
      <div className="max-w-2xl space-y-2 text-sm text-slate-500">
        <p>{TEMPLATES_INTRO}</p>
        <p>{ACCURACY_NOTE}</p>
      </div>
      <AddTemplate onAdded={load} />
      {error && <p key="error" role="alert" className="card p-4 text-sm text-rose-700">{error}</p>}
      {templates === null ? (
        <div key="loading" className="card h-40 animate-pulse" />
      ) : templates.length === 0 ? (
        <p key="empty" className="card p-6 text-sm text-slate-500">No templates yet. Add a blank garment photo above.</p>
      ) : (
        <ol key="list" className="space-y-6" data-testid="templates">
          {templates.map((t, i) => (
            <li key={t.id}>
              <TemplateCard
                template={t}
                first={i === 0}
                last={i === templates.length - 1}
                onChange={(next) => setTemplates((all) => (all ?? []).map((x) => (x.id === next.id ? next : x)))}
                onMove={(by) => move(t.id, by)}
                onDeleted={load}
              />
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function AddTemplate({ onAdded }: { onAdded: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  async function add() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await api.createMockupTemplate(file, name, confirmed);
      setFile(null);
      setName("");
      setConfirmed(false);
      if (input.current) input.current.value = "";
      onAdded();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card space-y-4 p-5" data-testid="add-template">
      <h2 className="text-base font-semibold text-slate-900">Add a blank</h2>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block min-w-0">
          <span className="label">Blank garment photo</span>
          <input
            ref={input}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="field block w-full min-w-0 text-sm"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            data-testid="template-file"
          />
          <span className="mt-1 block text-xs text-slate-500">
            A plain background, or a PNG with a transparent background, works best.
          </span>
        </label>
        <label className="block min-w-0">
          <span className="label">Name</span>
          <input className="field w-full" value={name} maxLength={60} placeholder="White tee, front"
            onChange={(e) => setName(e.target.value)} />
        </label>
      </div>
      <label className="flex items-start gap-2.5 text-sm text-slate-700 max-sm:min-h-[2.75rem]">
        <input type="checkbox" className="mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300" checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)} data-testid="template-rights" />
        <span>{RIGHTS_STATEMENT}</span>
      </label>
      {error && <p key="error" role="alert" className="text-sm text-rose-700">{error}</p>}
      <button type="button" className="btn-primary" onClick={add} disabled={!file || !confirmed || busy} data-testid="template-add">
        {busy ? "Adding…" : "Add template"}
      </button>
    </section>
  );
}

function TemplateCard({
  template,
  first,
  last,
  onChange,
  onMove,
  onDeleted,
}: {
  template: MockupTemplate;
  first: boolean;
  last: boolean;
  onChange: (t: MockupTemplate) => void;
  onMove: (by: -1 | 1) => void;
  onDeleted: () => void;
}) {
  const [corners, setCorners] = useState<Corner[]>(template.print_area);
  const [size, setSize] = useState(template.size);
  const [fold, setFold] = useState(template.fold_strength);
  const [name, setName] = useState(template.name);
  const [preview, setPreview] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const saved = useRef(template);

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  async function save(body: Parameters<typeof api.updateMockupTemplate>[1]) {
    setMessage(null);
    try {
      const next = await api.updateMockupTemplate(template.id, body);
      saved.current = next;
      setCorners(next.print_area);
      setSize(next.size);
      setFold(next.fold_strength);
      onChange(next);
      if (preview) showPreview(next.print_area, next.size, next.fold_strength);
    } catch (e: any) {
      setCorners(saved.current.print_area);
      setMessage(String(e.message ?? e));
    }
  }

  async function showPreview(area = corners, s = size, f = fold) {
    setPreviewing(true);
    setMessage(null);
    try {
      const blob = await api.previewMockupTemplate(template.id, { print_area: area, size: s, fold_strength: f });
      setPreview((old) => {
        if (old) URL.revokeObjectURL(old);
        return URL.createObjectURL(blob);
      });
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    } finally {
      setPreviewing(false);
    }
  }

  async function remove() {
    if (!window.confirm(`Delete the template “${template.name}”? Mockups already made from it stay in their batches.`)) return;
    try {
      await api.deleteMockupTemplate(template.id);
      onDeleted();
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }

  const aspect = `${template.width} / ${template.height}`;
  return (
    <article className="card overflow-hidden" data-testid="template-card">
      <div className="grid gap-0 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="border-b border-slate-200 bg-slate-50 p-4 md:border-b-0 md:border-r">
          <PrintAreaEditor
            src={api.mockupTemplateImageUrl(template.id, 896)}
            aspect={aspect}
            corners={corners}
            onMove={setCorners}
            onDone={(next) => save({ print_area: next })}
          />
          <p className="mt-2 text-xs text-slate-500">
            <span>Drag the four corners onto the print area, or tap a corner and use the arrow keys. </span>
            <span translate="no">{template.print_area_source === "seller" ? "Set by you." : "Found from the garment's outline."}</span>
          </p>
        </div>
        <div className="min-w-0 space-y-4 p-4">
          <label className="block">
            <span className="label">Name</span>
            <input className="field w-full" value={name} maxLength={60} onChange={(e) => setName(e.target.value)}
              onBlur={() => name.trim() && name !== template.name && save({ name })} />
          </label>
          {template.notice && (
            <p key="notice" role="note" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              {template.notice}
            </p>
          )}
          <Slider label="Design size" hint="Share of the print area the design fills; its proportions are kept, top-aligned."
            value={size} range={SIZE_RANGE} onChange={setSize} onDone={(v) => save({ size: v })} />
          <Slider label="Fold strength" hint="How much the fabric's folds bend the print."
            value={fold} range={FOLD_RANGE} onChange={setFold} onDone={(v) => save({ fold_strength: v })} />
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-primary" onClick={() => showPreview()} disabled={previewing} data-testid="template-preview">
              {previewing ? "Drawing…" : preview ? "Preview again" : "Preview with a sample design"}
            </button>
            <button type="button" className="btn-secondary" onClick={() => save({ reset_print_area: true })}>
              Reset print area
            </button>
          </div>
          {message && <p key="message" role="alert" className="text-sm text-rose-700">{message}</p>}
          {preview && (
            <figure key="preview" className="space-y-1">
              <div className="overflow-hidden rounded-md border border-slate-200 bg-white" style={{ aspectRatio: aspect }}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={preview} alt={`${template.name} with a sample design`} className="h-full w-full object-contain" />
              </div>
              <figcaption className="text-xs text-slate-500">A sample design at preview size. Your mockups use the full photo.</figcaption>
            </figure>
          )}
          <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-4">
            <span className="text-xs text-slate-500">Order (the first is a listing&apos;s cover):</span>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => onMove(-1)} disabled={first}>Earlier</button>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => onMove(1)} disabled={last}>Later</button>
            <button type="button" className="btn-secondary ml-auto px-3 py-1.5 text-xs text-rose-700" onClick={remove}>Delete</button>
          </div>
        </div>
      </div>
    </article>
  );
}

function Slider({
  label,
  hint,
  value,
  range,
  onChange,
  onDone,
}: {
  label: string;
  hint: string;
  value: number;
  range: [number, number];
  onChange: (v: number) => void;
  onDone: (v: number) => void;
}) {
  return (
    <label className="block">
      <span className="flex items-baseline justify-between gap-2">
        <span className="label mb-0">{label}</span>
        <span className="text-xs tabular-nums text-slate-600" translate="no">{percent(value)}</span>
      </span>
      <input
        type="range"
        min={range[0]}
        max={range[1]}
        step={0.01}
        value={value}
        className="mt-1 block h-11 w-full accent-brand-600"
        onChange={(e) => onChange(Number(e.target.value))}
        onPointerUp={(e) => onDone(Number((e.target as HTMLInputElement).value))}
        onKeyUp={(e) => onDone(Number((e.target as HTMLInputElement).value))}
      />
      <span className="block text-xs text-slate-500">{hint}</span>
    </label>
  );
}

const CORNER_NAMES = ["Top left", "Top right", "Bottom right", "Bottom left"];

/** The blank with its print area: four handles, by pointer (mouse or finger) or keyboard. */
function PrintAreaEditor({
  src,
  aspect,
  corners,
  onMove,
  onDone,
}: {
  src: string;
  aspect: string;
  corners: Corner[];
  onMove: (c: Corner[]) => void;
  onDone: (c: Corner[]) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const dragging = useRef<number | null>(null);
  const latest = useRef(corners);
  latest.current = corners;

  function at(e: React.PointerEvent): [number, number] {
    const r = box.current!.getBoundingClientRect();
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
  }

  function key(index: number, e: React.KeyboardEvent) {
    const step = e.shiftKey ? 0.02 : 0.005;
    const d: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const by = d[e.key];
    if (!by) return;
    e.preventDefault();
    const [x, y] = corners[index];
    const next = moveCorner(corners, index, x + by[0], y + by[1]);
    onMove(next);
    onDone(next);
  }

  const points = corners.map(([x, y]) => `${x},${y}`).join(" ");
  return (
    <div ref={box} className="relative w-full select-none overflow-hidden rounded-md bg-white" style={{ aspectRatio: aspect }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt="" draggable={false} className="pointer-events-none h-full w-full object-fill" />
      <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 h-full w-full">
        <polygon points={points} className="fill-brand-500/15 stroke-brand-600" strokeWidth={0.004} vectorEffect="non-scaling-stroke" />
      </svg>
      {corners.map(([x, y], i) => (
        <button
          key={i}
          type="button"
          aria-label={`${CORNER_NAMES[i]} corner of the print area`}
          className="absolute flex h-11 w-11 -translate-x-1/2 -translate-y-1/2 touch-none items-center justify-center rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
          onPointerDown={(e) => {
            dragging.current = i;
            (e.target as HTMLElement).setPointerCapture(e.pointerId);
          }}
          onPointerMove={(e) => {
            if (dragging.current !== i) return;
            const [px, py] = at(e);
            onMove(moveCorner(latest.current, i, px, py));
          }}
          onPointerUp={() => {
            if (dragging.current !== i) return;
            dragging.current = null;
            onDone(latest.current);
          }}
          onKeyDown={(e) => key(i, e)}
          data-testid="print-corner"
        >
          <span aria-hidden className="h-4 w-4 rounded-full border-2 border-white bg-brand-600 shadow" />
        </button>
      ))}
    </div>
  );
}
