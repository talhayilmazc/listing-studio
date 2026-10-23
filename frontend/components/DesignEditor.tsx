"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import type { MockupAdjustmentRow, MockupEdits, MockupRecolourRow, MockupRun } from "@/lib/types";
import { apply as applyH, homography as homographyOf, matrix3d, type Pt, type Quad } from "@/lib/areaEditor";
import {
  APPLY_SCOPES,
  LOOK_SLIDERS,
  NEUTRAL,
  RANGES,
  type Adjust,
  type ApplyScope,
  type Region,
  clean,
  fileFor,
  full,
  photoPoints,
  printPixels,
  toUnit,
  trimOn,
} from "@/lib/designEdits";

/**
 * One design on one mockup photo, adjusted by the seller (backend api/mockup_edits.py,
 * pipeline/design_edits.py; seller feedback Part B). Designs differ in size and proportions,
 * so each can be moved (drag, arrow keys, Shift for bigger steps), scaled (proportions kept
 * by default: "Keep proportions", Shift switches it while dragging), turned and given free
 * corners (perspective), with "Reset" back to the automatic fit. Its look (opacity,
 * brightness, contrast) has small ranges. Parts of it can be recoloured (a rectangle, a lasso
 * or a click with a tolerance) to black, white or a picked colour, for every subgroup or one
 * (white text on dark garments, black on light). "Apply this change to…" gives the change to
 * this design on all photos, all designs on this photo, or all designs on all photos of the
 * template, each on top of its own fit, and can be undone.
 *
 * The preview uses the same arithmetic as the renderer (lib/designEdits.ts). Every edit is a
 * render-time layer: the uploaded file never changes. Every save, apply, undo and reset goes the
 * same way (``remake``): the page first reloads the edits and marks the mockups as updating (each
 * tile, here in the strip and in the results behind, previews the change at once), then asks for
 * them to be made again and swaps each new file in as it is ready, the editor staying open.
 * Nothing here calls Etsy or an AI model.
 */
type Tool = "move" | "rect" | "lasso" | "wand";
type Drag =
  | { kind: "move"; start: Pt; base: Adjust }
  | { kind: "corner"; index: number; start: Pt; base: Adjust; centre: Pt; shift: boolean }
  | { kind: "rotate"; start: Pt; base: Adjust; centre: Pt }
  | { kind: "rect"; start: Pt; end: Pt }
  | { kind: "lasso"; points: Pt[] };

const COLOURS = [{ label: "Black", value: "#000000" }, { label: "White", value: "#ffffff" }];
const STEP = { small: 0.005, big: 0.05 };

export function DesignEditor({ runId, edits, designId, photoId, onClose, onEdited, onRun, strip }: {
  runId: string;
  edits: MockupEdits;
  designId: string;
  photoId: string;
  onClose: () => void;
  /** The edits of these mockups changed: reload the edits and show them as updating at once. */
  onEdited: (pairs: [string, string][]) => Promise<void>;
  /** The run as the server returned it once they were asked to be made again (null: the request
   * failed, nothing is being made again). */
  onRun: (run: MockupRun | null) => void;
  /** The mockups of this design (and every other one touched here), updating live. */
  strip?: ReactNode;
}) {
  const photo = edits.photos.find((p) => p.id === photoId)!;
  const design = edits.designs.find((d) => d.id === designId)!;
  const W = photo.width;
  const H = photo.height;
  const [areaIndex, setAreaIndex] = useState(0);
  const area = photo.areas[areaIndex] ?? photo.areas[0];
  const areaPx = useMemo(() => area.print_area.map(([x, y]) => [x * W, y * H]) as Quad, [area, W, H]);
  // The file this area prints: its subgroup's, else the design's own (as the renderer chooses).
  const file = fileFor(design.files, area.subgroup);
  const trim = trimOn(file, file.width, file.height);
  const tw = Math.max(1, trim[2] - trim[0]);
  const th = Math.max(1, trim[3] - trim[1]);

  const stored = (k: number) => (edits.adjustments.find((a) => a.design_id === designId && a.photo_id === photoId && a.area === k)?.data ?? null) as Partial<Adjust> | null;
  const [saved, setSaved] = useState<Adjust | null>(() => clean(stored(0)));
  const [adjust, setAdjust] = useState<Adjust>(() => full(stored(0)));
  useEffect(() => {
    setSaved(clean(stored(areaIndex)));
    setAdjust(full(stored(areaIndex)));
  }, [areaIndex]); // eslint-disable-line react-hooks/exhaustive-deps

  const subgroupNames = Array.from(new Set(photo.areas.map((a) => a.subgroup).filter(Boolean) as string[]));
  const [recolourFor, setRecolourFor] = useState<string>("");
  const recolourRows = (sub: string) => (edits.recolours.find((r) => r.design_id === designId && r.subgroup === sub)?.regions ?? []) as Region[];
  const [regions, setRegions] = useState<Region[]>(() => recolourRows(""));
  const [regionsDirty, setRegionsDirty] = useState(false);
  useEffect(() => {
    setRegions(recolourRows(recolourFor));
    setRegionsDirty(false);
  }, [recolourFor]); // eslint-disable-line react-hooks/exhaustive-deps
  // What the preview recolours with: the subgroup's own regions, else every subgroup's.
  const previewRegions = recolourFor ? regions : (regions.length ? regions : recolourRows(area.subgroup ?? ""));

  const [tool, setTool] = useState<Tool>("move");
  const [colour, setColour] = useState("#ffffff");
  const [tolerance, setTolerance] = useState(20);
  const [keepRatio, setKeepRatio] = useState(true);
  const [free, setFree] = useState(() => !!full(stored(0)).corners);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastApply, setLastApply] = useState<{ adjust?: MockupAdjustmentRow[]; recolour?: MockupRecolourRow[]; pairs: [string, string][] } | null>(null);

  // The photo fits the stage, its proportions kept.
  const stage = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ w: 1, h: 1 });
  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const fit = () => {
      const r = el.getBoundingClientRect();
      const s = Math.min(r.width / W, r.height / H);
      setBox({ w: Math.max(1, Math.floor(W * s)), h: Math.max(1, Math.floor(H * s)) });
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    root.current?.focus();
    return () => ro.disconnect();
  }, [W, H]);
  const scale = box.w / W || 1;

  // The design's pixels as the renderer prints them: recoloured, trimmed, with its look.
  const source = useRef<ImageData | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [loaded, setLoaded] = useState(0);
  useEffect(() => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      const ctx = c.getContext("2d", { willReadFrequently: true })!;
      ctx.drawImage(img, 0, 0);
      source.current = ctx.getImageData(0, 0, c.width, c.height);
      setLoaded((n) => n + 1);
    };
    img.src = api.mockupDesignFileUrl(design.id, file.version_id, file.stamp);
  }, [design.id, file.version_id]);
  useEffect(() => {
    const src = source.current;
    const out = canvas.current;
    if (!src || !out) return;
    const printed = printPixels(src, trim, previewRegions, adjust);
    out.width = printed.width;
    out.height = printed.height;
    out.getContext("2d")!.putImageData(new ImageData(printed.data, printed.width, printed.height), 0, 0);
  }, [loaded, previewRegions, adjust.opacity, adjust.brightness, adjust.contrast, tw, th]); // eslint-disable-line react-hooks/exhaustive-deps

  const points = photoPoints(areaPx, tw, th, photo.size, adjust);
  const centre: Pt = [points.reduce((s, p) => s + p[0], 0) / 4, points.reduce((s, p) => s + p[1], 0) / 4];
  const handle: Pt = (() => {
    const top: Pt = [(points[0][0] + points[1][0]) / 2, (points[0][1] + points[1][1]) / 2];
    const dx = top[0] - centre[0];
    const dy = top[1] - centre[1];
    const l = Math.hypot(dx, dy) || 1;
    return [top[0] + (dx / l) * (28 / scale), top[1] + (dy / l) * (28 / scale)];
  })();

  const drag = useRef<Drag | null>(null);
  const [lasso, setLasso] = useState<Pt[] | null>(null);
  const [rect, setRect] = useState<[Pt, Pt] | null>(null);
  function at(e: { clientX: number; clientY: number }): Pt {
    const r = frame.current!.getBoundingClientRect();
    return [(e.clientX - r.left) / scale, (e.clientY - r.top) / scale];
  }
  /** A photo point as a share of the design file (where a selection lands on the design). */
  function toShare(p: Pt): [number, number] {
    const back = homographyOf(points, [[0, 0], [tw, 0], [tw, th], [0, th]]);
    const [x, y] = applyH(back, p);
    return [clamp01((x + trim[0]) / file.width), clamp01((y + trim[1]) / file.height)];
  }

  function down(e: React.PointerEvent, d: Drag) {
    e.preventDefault();
    e.stopPropagation();
    drag.current = d;
    frame.current?.setPointerCapture(e.pointerId);
  }
  function onStageDown(e: React.PointerEvent) {
    const p = at(e);
    if (tool === "wand") {
      addRegion({ kind: "wand", seed: toShare(p), tolerance, colour });
      return;
    }
    if (tool === "rect") down(e, { kind: "rect", start: p, end: p });
    else if (tool === "lasso") down(e, { kind: "lasso", points: [p] });
  }
  function onMove(e: React.PointerEvent) {
    const d = drag.current;
    if (!d) return;
    const p = at(e);
    if (d.kind === "move") {
      const [u0, v0] = toUnit(areaPx, d.start);
      const [u, v] = toUnit(areaPx, p);
      setAdjust({ ...d.base, dx: clip("dx", d.base.dx + u - u0), dy: clip("dy", d.base.dy + v - v0) });
    } else if (d.kind === "corner") {
      if (free) {
        const [u0, v0] = toUnit(areaPx, d.start);
        const [u, v] = toUnit(areaPx, p);
        const corners = (d.base.corners ?? [[0, 0], [0, 0], [0, 0], [0, 0]]).map((c) => [...c] as [number, number]);
        corners[d.index] = [corners[d.index][0] + u - u0, corners[d.index][1] + v - v0];
        setAdjust({ ...d.base, corners });
        return;
      }
      const proportional = keepRatio !== e.shiftKey;
      if (proportional) {
        const r = Math.hypot(p[0] - d.centre[0], p[1] - d.centre[1]) / Math.max(1e-6, Math.hypot(d.start[0] - d.centre[0], d.start[1] - d.centre[1]));
        setAdjust({ ...d.base, scale: clip("scale", d.base.scale * r) });
      } else {
        const t = (-d.base.angle * Math.PI) / 180;
        const rot = ([x, y]: Pt): Pt => [x * Math.cos(t) - y * Math.sin(t), x * Math.sin(t) + y * Math.cos(t)];
        const a = rot([d.start[0] - d.centre[0], d.start[1] - d.centre[1]]);
        const b = rot([p[0] - d.centre[0], p[1] - d.centre[1]]);
        setAdjust({ ...d.base, sx: clip("sx", d.base.sx * Math.abs(b[0]) / Math.max(1e-6, Math.abs(a[0]))),
          sy: clip("sy", d.base.sy * Math.abs(b[1]) / Math.max(1e-6, Math.abs(a[1]))) });
      }
    } else if (d.kind === "rotate") {
      const a0 = Math.atan2(d.start[1] - d.centre[1], d.start[0] - d.centre[0]);
      const a1 = Math.atan2(p[1] - d.centre[1], p[0] - d.centre[0]);
      let angle = d.base.angle + ((a1 - a0) * 180) / Math.PI;
      if (angle > 180) angle -= 360;
      if (angle < -180) angle += 360;
      setAdjust({ ...d.base, angle: Math.round(angle * 10) / 10 });
    } else if (d.kind === "rect") {
      drag.current = { ...d, end: p };
      setRect([d.start, p]);
    } else if (d.kind === "lasso") {
      d.points.push(p);
      setLasso([...d.points]);
    }
  }
  function onUp() {
    const d = drag.current;
    drag.current = null;
    if (d?.kind === "rect") {
      setRect(null);
      if (Math.abs(d.end[0] - d.start[0]) * scale > 3 && Math.abs(d.end[1] - d.start[1]) * scale > 3) {
        addRegion({ kind: "rect", points: [toShare(d.start), toShare(d.end)], colour });
      }
    } else if (d?.kind === "lasso") {
      setLasso(null);
      if (d.points.length >= 3) addRegion({ kind: "lasso", points: simplify(d.points, 2 / scale).map(toShare), colour });
    }
  }
  function addRegion(r: Region) {
    setRegions((all) => [...all, r]);
    setRegionsDirty(true);
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      onClose();
      return;
    }
    const step = e.shiftKey ? STEP.big : STEP.small;
    const by: Record<string, Pt> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const m = by[e.key];
    if (!m || (e.target as HTMLElement).closest("input,select,textarea")) return;
    e.preventDefault();
    setAdjust((a) => ({ ...a, dx: clip("dx", a.dx + m[0]), dy: clip("dy", a.dy + m[1]) }));
  }

  async function persist(): Promise<void> {
    await api.saveAdjustment({ design_id: designId, photo_id: photoId, area: areaIndex, data: clean(adjust) });
    if (regionsDirty) {
      await api.saveRecolour(designId, { subgroup: recolourFor, regions });
      setRegionsDirty(false);
    }
  }
  /** The one way a change reaches the mockups: shown as updating at once, then made again. */
  async function remake(pairs: [string, string][]): Promise<void> {
    await onEdited(pairs);
    try {
      onRun(await api.rerenderMockups(runId, pairs));
    } catch (e) {
      onRun(null);
      throw e;
    }
  }
  async function save() {
    setBusy(true);
    setError(null);
    try {
      await persist();
      setSaved(clean(adjust));
      await remake([[designId, photoId]]);
      setNote("Saved. The mockup is being made again.");
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }
  async function applyTo(scope: ApplyScope) {
    setBusy(true);
    setError(null);
    try {
      await persist();
      const res = await api.applyAdjustment({ run_id: runId, design_id: designId, photo_id: photoId, area: areaIndex,
        before: saved, after: clean(adjust), scope });
      let recolour: MockupRecolourRow[] | undefined;
      if (regions.length && scope !== "design_photos") {
        recolour = (await api.saveRecolour(designId, { subgroup: recolourFor, regions, scope })).undo;
      }
      const pairs = dedupe([[designId, photoId], ...res.undo.map((r) => [r.design_id, r.photo_id] as [string, string]),
        ...(recolour ?? []).flatMap((r) => edits.photos.map((p) => [r.design_id, p.id] as [string, string]))]);
      setSaved(clean(adjust));
      setLastApply({ adjust: res.undo, recolour, pairs });
      await remake(pairs);
      setNote(`Applied to ${res.changed} more ${res.changed === 1 ? "placement" : "placements"}; the mockups are being made again.`);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }
  async function undoApply() {
    if (!lastApply) return;
    setBusy(true);
    setError(null);
    try {
      if (lastApply.adjust?.length) await api.restoreAdjustments(lastApply.adjust);
      if (lastApply.recolour?.length) await api.restoreRecolours(lastApply.recolour.filter((r) => r.design_id !== designId));
      await remake(lastApply.pairs);
      setLastApply(null);
      setNote("Undone: the other mockups are back as they were.");
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  const px = (p: Pt) => [p[0] * scale, p[1] * scale] as Pt;
  const screen = points.map(px) as Quad;
  const lookChanged = adjust.opacity !== 1 || adjust.brightness !== 0 || adjust.contrast !== 0;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-stretch justify-center bg-slate-900/60 lg:items-center lg:p-4" role="dialog"
      aria-modal="true" aria-label={`Adjust ${design.original_filename} on ${photo.name}`} data-testid="design-editor">
      <div ref={root} tabIndex={-1} onKeyDown={onKey}
        className="flex h-full w-full flex-col overflow-hidden bg-white outline-none lg:h-[min(56rem,calc(100vh-2rem))] lg:w-[min(80rem,calc(100vw-2rem))] lg:min-w-[900px] lg:rounded-xl lg:shadow-2xl">
        <header className="flex min-w-0 items-center gap-2 border-b border-slate-200 px-3 py-2 sm:px-4">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-base font-semibold text-slate-900" title={design.original_filename} translate="no">
              {design.sku ?? design.original_filename}
            </h2>
            <p className="truncate text-xs text-slate-500" translate="no">{`on ${photo.name}`}</p>
          </div>
          <button type="button" className="btn-secondary min-h-11 px-4" onClick={onClose} data-testid="design-editor-close">Close</button>
        </header>
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          <div className="flex min-w-0 flex-1 flex-col lg:min-h-0">
          <div ref={stage} className="relative flex min-h-[45vh] min-w-0 flex-1 items-center justify-center overflow-hidden bg-slate-100 p-2 lg:min-h-0">
            <div ref={frame} className={`relative touch-none select-none ${tool === "move" ? "" : "cursor-crosshair"}`}
              style={{ width: box.w, height: box.h }}
              onPointerDown={onStageDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
              data-testid="design-editor-stage">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={api.mockupPhotoImageUrl(photo.id, "medium")} alt="" draggable={false} className="pointer-events-none absolute inset-0 h-full w-full" />
              <canvas ref={canvas} aria-hidden className="pointer-events-none absolute left-0 top-0 max-w-none"
                style={{ width: tw, height: th, transformOrigin: "0 0", transform: matrix3d(tw, th, screen) }} data-testid="design-editor-design" />
              <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-visible">
                <polygon points={areaPx.map((p) => p.join(",")).join(" ")} className="fill-none stroke-slate-500" strokeDasharray="6 4"
                  strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
                <polygon points={points.map((p) => p.join(",")).join(" ")} className="fill-transparent stroke-brand-600" strokeWidth={2}
                  vectorEffect="non-scaling-stroke" style={{ cursor: tool === "move" ? "move" : "crosshair" }}
                  onPointerDown={(e) => tool === "move" ? down(e, { kind: "move", start: at(e), base: adjust }) : undefined}
                  data-testid="design-editor-box" />
                {tool === "move" && (
                  <line key="stalk" x1={(points[0][0] + points[1][0]) / 2} y1={(points[0][1] + points[1][1]) / 2} x2={handle[0]} y2={handle[1]}
                    className="stroke-brand-600" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
                )}
                {rect && <polygon key="rect" points={[rect[0], [rect[1][0], rect[0][1]], rect[1], [rect[0][0], rect[1][1]]].map((p) => p.join(",")).join(" ")}
                  className="fill-brand-500/20 stroke-brand-600" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />}
                {lasso && <polyline key="lasso" points={lasso.map((p) => p.join(",")).join(" ")} className="fill-brand-500/20 stroke-brand-600"
                  strokeWidth={1.5} vectorEffect="non-scaling-stroke" />}
              </svg>
              {tool === "move" && screen.map((p, i) => (
                <button key={`c${i}`} type="button" aria-label={`${free ? "Move" : "Resize from"} corner ${i + 1}`}
                  className="absolute h-11 w-11 -translate-x-1/2 -translate-y-1/2 rounded-full"
                  style={{ left: p[0], top: p[1], touchAction: "none" }}
                  onPointerDown={(e) => down(e, { kind: "corner", index: i, start: at(e), base: adjust, centre, shift: e.shiftKey })}
                  data-testid="design-editor-corner">
                  <span className={`mx-auto block h-4 w-4 rounded-full border-2 border-white ${free ? "bg-amber-500" : "bg-brand-600"} shadow`} />
                </button>
              ))}
              {tool === "move" && (
                <button key="rotate" type="button" aria-label="Turn the design" className="absolute h-11 w-11 -translate-x-1/2 -translate-y-1/2 rounded-full"
                  style={{ left: px(handle)[0], top: px(handle)[1], touchAction: "none" }}
                  onPointerDown={(e) => down(e, { kind: "rotate", start: at(e), base: adjust, centre })} data-testid="design-editor-rotate">
                  <span className="mx-auto flex h-7 w-7 items-center justify-center rounded-full bg-brand-600 text-sm text-white shadow">↻</span>
                </button>
              )}
            </div>
          </div>
          {/* Under the photo, never scrolled away: the mockups updating live while the editor stays open. */}
          {strip && <div key="strip" className="shrink-0 border-t border-slate-200 bg-white px-3 py-2">{strip}</div>}
          </div>

          <aside className="min-h-0 w-full space-y-4 overflow-y-auto border-t border-slate-200 p-3 text-sm lg:w-80 lg:border-l lg:border-t-0">
            {photo.areas.length > 1 && (
              <div key="areas" className="flex flex-wrap gap-2" role="tablist" aria-label="Print area">
                {photo.areas.map((a, i) => (
                  <button key={i} type="button" role="tab" aria-selected={i === areaIndex}
                    className={`min-h-11 min-w-11 px-3 ${i === areaIndex ? "btn-primary" : "btn-secondary"}`} onClick={() => setAreaIndex(i)}
                    data-testid="design-editor-area">
                    <span translate="no">{`Area ${i + 1}${a.subgroup ? ` · ${a.subgroup}` : ""}`}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <button type="button" aria-pressed={keepRatio} className={`min-h-11 px-2 ${keepRatio ? "btn-primary" : "btn-secondary"}`}
                onClick={() => setKeepRatio(!keepRatio)} data-testid="design-editor-ratio">
                Keep proportions
              </button>
              <button type="button" aria-pressed={free} className={`min-h-11 px-2 ${free ? "btn-primary" : "btn-secondary"}`}
                onClick={() => setFree(!free)} data-testid="design-editor-free">
                Free corners
              </button>
              <button type="button" className="btn-secondary col-span-2 min-h-11" onClick={() => { setAdjust({ ...NEUTRAL }); setFree(false); }}
                data-testid="design-editor-reset">
                Reset to the automatic fit
              </button>
            </div>
            <p className="text-xs text-slate-500">
              Drag the design to move it (arrow keys nudge it; Shift for bigger steps), a corner to resize it, the round handle to turn it.
              Shift switches Keep proportions while you drag.
            </p>

            <fieldset className="space-y-2">
              <legend className="label">Look</legend>
              {LOOK_SLIDERS.map((s) => (
                <label key={s.key} className="block">
                  <span className="flex justify-between text-xs text-slate-600">
                    <span>{s.label}</span><span translate="no">{fmt(s.key, adjust[s.key])}</span>
                  </span>
                  <input type="range" className="w-full" min={RANGES[s.key][0]} max={RANGES[s.key][1]} step={s.step} value={adjust[s.key]}
                    onChange={(e) => setAdjust((a) => ({ ...a, [s.key]: Number(e.target.value) }))} data-testid={`design-editor-${s.key}`} />
                </label>
              ))}
              {lookChanged && (
                <button key="look-reset" type="button" className="btn-secondary min-h-11 w-full" onClick={() => setAdjust((a) => ({ ...a, opacity: 1, brightness: 0, contrast: 0 }))}>
                  Reset the look
                </button>
              )}
            </fieldset>

            <fieldset className="space-y-2" data-testid="design-editor-recolour">
              <legend className="label">Recolour part of the design</legend>
              <div className="grid grid-cols-4 gap-1">
                {(["move", "rect", "lasso", "wand"] as Tool[]).map((t) => (
                  <button key={t} type="button" aria-pressed={tool === t} className={`min-h-11 px-1 text-xs ${tool === t ? "btn-primary" : "btn-secondary"}`}
                    onClick={() => setTool(t)} data-testid={`design-editor-tool-${t}`}>
                    {{ move: "Move", rect: "Rectangle", lasso: "Lasso", wand: "Wand" }[t]}
                  </button>
                ))}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {COLOURS.map((c) => (
                  <button key={c.value} type="button" aria-pressed={colour === c.value} className={`min-h-11 px-3 ${colour === c.value ? "btn-primary" : "btn-secondary"}`}
                    onClick={() => setColour(c.value)}>
                    {c.label}
                  </button>
                ))}
                <label className="flex min-h-11 items-center gap-1.5 text-xs text-slate-600">
                  <span>Pick</span>
                  <input type="color" value={colour} onChange={(e) => setColour(e.target.value)} className="h-9 w-11" aria-label="Pick a colour" />
                </label>
              </div>
              {tool === "wand" && (
                <label key="tolerance" className="block">
                  <span className="flex justify-between text-xs text-slate-600"><span>Tolerance</span><span translate="no">{tolerance}</span></span>
                  <input type="range" min={0} max={100} step={1} value={tolerance} onChange={(e) => setTolerance(Number(e.target.value))} className="w-full"
                    data-testid="design-editor-tolerance" />
                </label>
              )}
              <label className="block">
                <span className="label">For</span>
                <select className="field w-full" value={recolourFor} onChange={(e) => setRecolourFor(e.target.value)} data-testid="design-editor-recolour-for">
                  <option value="">Every subgroup</option>
                  {subgroupNames.map((n) => <option key={n} value={n}>{`${n} only`}</option>)}
                </select>
              </label>
              {regions.length > 0 && (
                <ul key="regions" className="space-y-1" data-testid="design-editor-regions">
                  {regions.map((r, i) => (
                    <li key={i} className="flex items-center gap-2 text-xs">
                      <span className="h-4 w-4 shrink-0 rounded border border-slate-300" style={{ background: r.colour }} aria-hidden />
                      <span className="min-w-0 flex-1" translate="no">{`${{ rect: "Rectangle", lasso: "Lasso", wand: "Wand" }[r.kind]} → ${r.colour}`}</span>
                      <button type="button" className="tap underline" onClick={() => { setRegions((all) => all.filter((_, j) => j !== i)); setRegionsDirty(true); }}>
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-xs text-slate-500">Choose a tool, then draw on the design. The colour replaces the selected pixels; their transparency stays. Your file is never changed.</p>
            </fieldset>

            <fieldset className="space-y-2" data-testid="design-editor-apply">
              <legend className="label">Apply this change to…</legend>
              {(Object.keys(APPLY_SCOPES) as ApplyScope[]).map((s) => (
                <button key={s} type="button" className="btn-secondary min-h-11 w-full text-left" disabled={busy || !edits.editable}
                  onClick={() => applyTo(s)} data-testid={`design-editor-apply-${s}`}>
                  {APPLY_SCOPES[s]}
                </button>
              ))}
              <p className="text-xs text-slate-500">Each one moves, scales and turns by the same amount from its own fit.</p>
              {lastApply && (
                <button key="undo" type="button" className="btn-secondary min-h-11 w-full" onClick={undoApply} disabled={busy} data-testid="design-editor-undo">
                  Undo the last apply
                </button>
              )}
            </fieldset>
            {!edits.editable && <p key="locked" className="text-xs text-amber-800">A listing of this batch is written, so its mockups no longer change.</p>}
            {note && <p key="note" role="status" className="text-xs text-emerald-800">{note}</p>}
            {error && <p key="error" role="alert" className="text-xs text-rose-700">{error}</p>}
          </aside>
        </div>
        <footer className="flex items-center gap-2 border-t border-slate-200 px-3 py-2 sm:px-4">
          <span className="min-w-0 flex-1 truncate text-xs text-slate-500" translate="no">
            {`${Math.round(adjust.scale * 100)}% · ${Math.round(adjust.angle)}°${adjust.corners ? " · free corners" : ""}`}
          </span>
          <button type="button" className="btn-primary min-h-11 px-5" onClick={save} disabled={busy || !edits.editable} data-testid="design-editor-save">
            {busy ? "Saving…" : "Save and update mockup"}
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}

// --- helpers ---------------------------------------------------------------------------------------

const clamp01 = (v: number) => Math.min(1, Math.max(0, Math.round(v * 1e6) / 1e6));
const clip = (k: keyof typeof RANGES, v: number) => Math.min(RANGES[k][1], Math.max(RANGES[k][0], v));
const fmt = (k: string, v: number) => (k === "opacity" ? `${Math.round(v * 100)}%` : `${v > 0 ? "+" : ""}${Math.round(v * 100)}`);

function dedupe(pairs: [string, string][]): [string, string][] {
  const seen = new Set<string>();
  return pairs.filter(([a, b]) => (seen.has(`${a}|${b}`) ? false : (seen.add(`${a}|${b}`), true)));
}

/** Lasso points thinned to one every ``d`` photo pixels (a long drag stays a short region). */
function simplify(points: Pt[], d: number): Pt[] {
  const out: Pt[] = [];
  for (const p of points) {
    const last = out[out.length - 1];
    if (!last || Math.hypot(p[0] - last[0], p[1] - last[1]) >= d) out.push(p);
  }
  return out.slice(0, 400);
}
