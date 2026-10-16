"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import type { MockupPhoto } from "@/lib/types";
import {
  MIN_SIZE,
  SNAP_SCREEN_PX,
  across,
  axisAngle,
  designQuad,
  dragCorner,
  history,
  isRect,
  makeGuides,
  matrix3d,
  move,
  nudge,
  quadOf,
  record,
  rectOf,
  redo,
  resizeRect,
  rotate,
  rotateHandle,
  snapAngle,
  snapArea,
  snapPoint,
  toPx,
  toShares,
  turn,
  undo,
  type Guide,
  type History,
  type Pt,
  type Quad,
} from "@/lib/areaEditor";
import { EDITOR_HINT, FOLD_RANGE, LOOKS_RIGHT, OCCLUSION_LABEL, SAMPLE_DESIGN, SIZE_RANGE, SUGGESTION_NOTE, percent } from "@/lib/mockups";

/**
 * The print-area editor (backend api/mockups.py): one photo, large, with its suggested
 * area(s) drawn. The seller moves, resizes and turns each area (smart guides snap it to the
 * garment's axis, its collar line, the photo's centre and the other areas), adds an area by
 * tapping a garment, previews a design inside it, and presses "Looks right": only then is
 * the photo used for mockups. "Copy to other photos" gives the areas to colour variants,
 * each shown on its own photo to confirm. Full screen on a phone, at least 900 px wide on a
 * desktop; every control is at least 44 px. Nothing here calls Etsy or an AI model.
 */
type Area = { quad: Quad; suggested: Quad; source: string; kind?: string | null };
type Drag =
  | { kind: "move"; index: number; start: Pt; quad: Quad }
  | { kind: "corner"; index: number; corner: number; start: Pt; quad: Quad }
  | { kind: "rotate"; index: number; start: Pt; quad: Quad };

const CORNERS = ["Top left", "Top right", "Bottom right", "Bottom left"];
const GUIDE_COLOUR: Record<Guide["kind"], string> = { axis: "#db2777", collar: "#ea580c", area: "#2563eb", photo: "#16a34a" };

export function PrintAreaEditor({
  photo,
  siblings,
  step,
  onConfirmed,
  onCopied,
  onChanged,
  onClose,
}: {
  photo: MockupPhoto;
  /** The other photos of the template (for "Copy to other photos"). */
  siblings: MockupPhoto[];
  /** "2 of 5" when the seller is going through several photos. */
  step?: { index: number; total: number } | null;
  onConfirmed: (photo: MockupPhoto) => void;
  onCopied: (photos: MockupPhoto[], source: MockupPhoto) => void;
  onChanged: (photo: MockupPhoto) => void;
  onClose: () => void;
}) {
  const W = photo.width ?? 1;
  const H = photo.height ?? 1;
  const initial = useMemo<Area[]>(
    () => photo.areas.map((a) => ({ quad: toPx(a.print_area, W, H), suggested: toPx(a.detected_area, W, H), source: a.source, kind: a.kind })),
    [photo.id, photo.version], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const [h, setH] = useState<History<Area[]>>(() => history(initial));
  const [live, setLive] = useState<Area[] | null>(null);
  const [selected, setSelected] = useState(0);
  const [free, setFree] = useState(() => initial.some((a) => !isRect(a.quad)));
  const [snapOn, setSnapOn] = useState(true);
  const [showDesign, setShowDesign] = useState(true);
  const [design, setDesign] = useState(SAMPLE_DESIGN);
  const [size, setSize] = useState(photo.size);
  const [tapping, setTapping] = useState(false);
  const [finding, setFinding] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [active, setActive] = useState<Guide[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copying, setCopying] = useState(false);
  const [copyIds, setCopyIds] = useState<string[]>([]);
  const [fabric, setFabric] = useState<string | null>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const stage = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const root = useRef<HTMLDivElement>(null);

  // A new photo (the next in the queue): start from its own areas.
  useEffect(() => {
    setH(history(initial));
    setLive(null);
    setSelected(0);
    setFree(initial.some((a) => !isRect(a.quad)));
    setSize(photo.size);
    setNote(null);
    setError(null);
    setCopying(false);
    setFabric(null);
    root.current?.focus();
  }, [initial, photo.size]);

  // The photo fits the stage, its proportions kept.
  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const fit = () => {
      const r = el.getBoundingClientRect();
      const scale = Math.min(r.width / W, r.height / H);
      setBox({ w: Math.max(1, Math.floor(W * scale)), h: Math.max(1, Math.floor(H * scale)) });
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [W, H]);

  useEffect(() => () => {
    if (design.url.startsWith("blob:")) URL.revokeObjectURL(design.url);
  }, [design.url]);
  useEffect(() => () => {
    if (fabric) URL.revokeObjectURL(fabric);
  }, [fabric]);

  const areas = live ?? h.present;
  const scale = box.w / W || 1;
  const reach = (alt: boolean) => (snapOn && !alt ? SNAP_SCREEN_PX / scale : 0);
  const guidesFor = useCallback(
    (index: number, list: Area[]) => makeGuides(W, H, list[index]?.suggested ?? null, list.filter((_, i) => i !== index).map((a) => a.quad)),
    [W, H],
  );
  const dirty = JSON.stringify(h.present) !== JSON.stringify(initial);

  function commit(next: Area[]) {
    setH((now) => record(now, next));
    setLive(null);
  }

  function at(e: { clientX: number; clientY: number }): Pt {
    const r = frame.current!.getBoundingClientRect();
    return [(e.clientX - r.left) / scale, (e.clientY - r.top) / scale];
  }

  function begin(e: React.PointerEvent, d: Drag) {
    e.preventDefault();
    e.stopPropagation();
    drag.current = d;
    setSelected(d.index);
    frame.current?.setPointerCapture(e.pointerId);
  }

  function onMove(e: React.PointerEvent) {
    const d = drag.current;
    if (!d) return;
    const p = at(e);
    const guides = guidesFor(d.index, areas);
    let quad: Quad;
    let shown: Guide[] = [];
    if (d.kind === "move") {
      const moved = move(d.quad, p[0] - d.start[0], p[1] - d.start[1]);
      const s = snapArea(moved, guides, reach(e.altKey));
      quad = s.value;
      shown = s.active;
    } else if (d.kind === "corner") {
      const s = snapPoint(p, guides, reach(e.altKey));
      quad = free ? dragCorner(d.quad, d.corner, s.value) : resizeRect(d.quad, d.corner, s.value, MIN_SIZE);
      shown = s.active;
    } else {
      const start = rectOf(d.quad).angle;
      const wanted = start + turn(d.quad, d.start, p);
      const sug = areas[d.index].suggested;
      const s = snapAngle(wanted, axisAngle(sug), snapOn && !e.altKey ? undefined : 0);
      quad = rotate(d.quad, s.angle - start);
      if (s.snapped === "axis") shown = guides.filter((g) => g.kind === "axis");
      if (s.snapped === "upright") {
        const c = rectOf(quad);
        shown = [{ a: [c.cx, 0], b: [c.cx, H], kind: "photo", dir: "v", features: ["centre"] }];
      }
    }
    setActive(shown);
    setLive(areas.map((a, i) => (i === d.index ? { ...a, quad } : a)));
  }

  function onUp() {
    if (!drag.current) return;
    drag.current = null;
    setActive([]);
    if (live) commit(live);
  }

  async function onStageDown(e: React.PointerEvent) {
    if (!tapping || finding) return;
    e.preventDefault();
    const [x, y] = at(e);
    if (x < 0 || y < 0 || x > W || y > H) return;
    setFinding(true);
    setNote(null);
    try {
      const found = await api.findMockupGarment(photo.id, x / W, y / H, areas.map((a) => toShares(a.quad, W, H)));
      const quad = toPx(found.print_area, W, H);
      const next = [...areas, { quad, suggested: quad, source: found.found ? "tap" : "seller", kind: found.kind }];
      commit(next);
      setSelected(next.length - 1);
      if (!found.found && found.note) setNote(found.note);
      setTapping(false);
    } catch (err: any) {
      setError(String(err.message ?? err));
    } finally {
      setFinding(false);
    }
  }

  function onKey(e: React.KeyboardEvent) {
    const key = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && key === "z") {
      e.preventDefault();
      setH((now) => (e.shiftKey ? redo(now) : undo(now)));
      return;
    }
    if ((e.ctrlKey || e.metaKey) && key === "y") {
      e.preventDefault();
      setH((now) => redo(now));
      return;
    }
    if ((e.target as HTMLElement).closest("input, select, textarea")) return;
    const by = nudge(e.key, e.shiftKey, W);
    if (!by || !areas[selected]) return;
    e.preventDefault();
    commit(areas.map((a, i) => (i === selected ? { ...a, quad: move(a.quad, by[0], by[1]) } : a)));
  }

  function reset() {
    commit(h.present.map((a) => ({ ...a, quad: a.suggested })));
    setFree(h.present.some((a) => !isRect(a.suggested)));
  }

  function remove(index: number) {
    if (areas.length <= 1) return;
    commit(areas.filter((_, i) => i !== index));
    setSelected(Math.max(0, Math.min(selected, areas.length - 2)));
  }

  function toggleFree() {
    if (free) {
      // Back to rectangles: each area becomes the rectangle that fits its corners best.
      commit(areas.map((a) => ({ ...a, quad: quadOf(rectOf(a.quad)) })));
    }
    setFree(!free);
  }

  function payload() {
    return h.present.map((a) => {
      const shares = toShares(a.quad, W, H);
      const suggested = toShares(a.suggested, W, H);
      const unchanged = JSON.stringify(shares) === JSON.stringify(suggested);
      return { print_area: shares, detected_area: suggested, source: unchanged ? a.source : "seller", kind: a.kind ?? null };
    });
  }

  async function confirm() {
    setSaving(true);
    setError(null);
    try {
      onConfirmed(await api.saveMockupAreas(photo.id, payload(), true, size));
    } catch (err: any) {
      setError(String(err.message ?? err));
    } finally {
      setSaving(false);
    }
  }

  async function copy() {
    if (!copyIds.length) return;
    setSaving(true);
    setError(null);
    try {
      // Copying says these areas are right: this photo is confirmed with them.
      const saved = await api.saveMockupAreas(photo.id, payload(), true, size);
      const copied = await api.copyMockupAreas(photo.id, copyIds);
      onCopied(copied, saved);
    } catch (err: any) {
      setError(String(err.message ?? err));
    } finally {
      setSaving(false);
    }
  }

  async function showFabric() {
    if (fabric) {
      setFabric(null);
      return;
    }
    setError(null);
    try {
      const blob = await api.previewMockupPhoto(photo.id, { areas: h.present.map((a) => toShares(a.quad, W, H)), size });
      setFabric(URL.createObjectURL(blob));
    } catch (err: any) {
      setError(String(err.message ?? err));
    }
  }

  async function setting(body: Parameters<typeof api.updateMockupPhoto>[1]) {
    try {
      onChanged(await api.updateMockupPhoto(photo.id, body));
    } catch (err: any) {
      setError(String(err.message ?? err));
    }
  }

  function chooseDesign(file: File | undefined) {
    if (!file) return;
    const url = URL.createObjectURL(file); // shown here only; nothing is uploaded
    const img = new Image();
    img.onload = () => setDesign({ url, width: img.naturalWidth || 1000, height: img.naturalHeight || 1000 });
    img.src = url;
  }

  function close() {
    if (dirty && !window.confirm("Close without saving? Your changes to this photo's print area will be lost.")) return;
    onClose();
  }

  const others = siblings.filter((p) => p.id !== photo.id && p.status === "ready");
  const sel = areas[selected];
  const handleGap = 34 / scale;
  const px = (p: Pt) => ({ left: `${p[0] * scale}px`, top: `${p[1] * scale}px` });

  // On the page's body, so no container of the page (a transformed column, say) can
  // shrink it: full screen on a phone, centred over everything on a desktop.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-stretch justify-center bg-slate-900/60 lg:items-center lg:p-4" role="dialog"
      aria-modal="true" aria-label={`Print area of ${photo.name}`} data-testid="area-editor">
      <div ref={root} tabIndex={-1} onKeyDown={onKey}
        className="flex h-full w-full flex-col overflow-hidden bg-white outline-none lg:h-[min(56rem,calc(100vh-2rem))] lg:w-[min(80rem,calc(100vw-2rem))] lg:min-w-[900px] lg:rounded-xl lg:shadow-2xl">
        <header className="flex min-w-0 items-center gap-2 border-b border-slate-200 px-3 py-2 sm:px-4">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-base font-semibold text-slate-900" title={photo.name} translate="no">{photo.name}</h2>
            <p className="truncate text-xs text-slate-500" translate="no">
              {step && step.total > 1 ? `Photo ${step.index + 1} of ${step.total} to check` : photo.confirmed ? "Confirmed" : "Needs your check"}
            </p>
          </div>
          <button type="button" className="btn-secondary min-h-11 px-4" onClick={close} data-testid="editor-close">Close</button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          <div ref={stage} className="relative flex min-h-[50vh] min-w-0 flex-1 items-center justify-center overflow-hidden bg-slate-100 p-2 lg:min-h-0">
            <div ref={frame} className={`relative touch-none select-none ${tapping ? "cursor-crosshair" : ""}`}
              style={{ width: box.w, height: box.h }}
              onPointerDown={onStageDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
              data-testid="editor-stage">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={api.mockupPhotoImageUrl(photo.id)} alt="" draggable={false}
                className="pointer-events-none absolute inset-0 h-full w-full" />
              {showDesign && !fabric && areas.map((a, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img key={`d${i}`} src={design.url} alt="" draggable={false} aria-hidden
                  className="pointer-events-none absolute left-0 top-0 max-w-none opacity-90"
                  style={{ width: design.width, height: design.height, transformOrigin: "0 0",
                    transform: matrix3d(design.width, design.height, designQuad(a.quad, design.width, design.height, size).map(([x, y]) => [x * scale, y * scale]) as Quad) }}
                  data-testid="editor-design" />
              ))}
              {fabric && (
                // eslint-disable-next-line @next/next/no-img-element
                <img key="fabric" src={fabric} alt="The photo with a sample design printed" className="pointer-events-none absolute inset-0 h-full w-full" />
              )}
              <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="absolute inset-0 h-full w-full overflow-hidden">
                {active.map((g, i) => {
                  const [a, b] = across(g, W, H);
                  return (
                    <line key={`g${i}`} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke={GUIDE_COLOUR[g.kind]} strokeWidth={1.5}
                      vectorEffect="non-scaling-stroke" data-testid="editor-guide" data-kind={g.kind} />
                  );
                })}
                {areas.map((a, i) => (
                  <polygon key={`a${i}`} points={a.quad.map((p) => p.join(",")).join(" ")}
                    className={i === selected ? "fill-brand-500/10 stroke-brand-600" : "fill-white/10 stroke-slate-600"}
                    strokeWidth={i === selected ? 2 : 1.5} strokeDasharray={i === selected ? undefined : "6 4"}
                    vectorEffect="non-scaling-stroke" style={{ cursor: tapping ? "crosshair" : "move" }}
                    onPointerDown={(e) => !tapping && begin(e, { kind: "move", index: i, start: at(e), quad: a.quad })}
                    data-testid="editor-area" />
                ))}
                {sel && (
                  <line key="stalk" x1={(sel.quad[0][0] + sel.quad[1][0]) / 2} y1={(sel.quad[0][1] + sel.quad[1][1]) / 2}
                    x2={rotateHandle(sel.quad, handleGap)[0]} y2={rotateHandle(sel.quad, handleGap)[1]}
                    className="stroke-brand-600" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
                )}
              </svg>
              {areas.map((a, i) => (
                <span key={`n${i}`} className="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 rounded bg-slate-900/70 px-1.5 text-xs font-semibold text-white"
                  style={px([(a.quad[0][0] + a.quad[2][0]) / 2, (a.quad[0][1] + a.quad[2][1]) / 2])} translate="no">
                  {`${i + 1}`}
                </span>
              ))}
              {sel && !tapping && sel.quad.map((p, c) => (
                <button key={`c${c}`} type="button" aria-label={`${CORNERS[c]} corner`}
                  className="absolute flex h-11 w-11 -translate-x-1/2 -translate-y-1/2 touch-none items-center justify-center rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                  style={px(p)} onPointerDown={(e) => begin(e, { kind: "corner", index: selected, corner: c, start: at(e), quad: sel.quad })}
                  data-testid="editor-corner">
                  <span aria-hidden className={`h-4 w-4 border-2 border-white bg-brand-600 shadow ${free ? "rotate-45" : "rounded-full"}`} />
                </button>
              ))}
              {sel && !tapping && (
                <button key="rotate" type="button" aria-label="Turn the print area"
                  className="absolute flex h-11 w-11 -translate-x-1/2 -translate-y-1/2 touch-none items-center justify-center rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                  style={px(rotateHandle(sel.quad, handleGap))}
                  onPointerDown={(e) => begin(e, { kind: "rotate", index: selected, start: at(e), quad: sel.quad })}
                  data-testid="editor-rotate">
                  <span aria-hidden className="flex h-6 w-6 items-center justify-center rounded-full border-2 border-white bg-brand-600 text-xs text-white shadow">↻</span>
                </button>
              )}
            </div>
            {(tapping || finding) && (
              <p key="tap" role="status" className="absolute left-1/2 top-3 -translate-x-1/2 rounded-md bg-slate-900/80 px-3 py-2 text-sm text-white" translate="no">
                {finding ? "Finding that garment…" : "Tap the garment to add its print area"}
              </p>
            )}
          </div>

          <aside className="flex max-h-[45vh] min-w-0 flex-col overflow-y-auto border-t border-slate-200 lg:max-h-none lg:w-80 lg:border-l lg:border-t-0">
            <div className="space-y-4 p-3 sm:p-4">
              <p className="text-sm text-slate-600">{SUGGESTION_NOTE}</p>
              <div className="grid grid-cols-3 gap-2" role="toolbar" aria-label="Edit the print area">
                <button type="button" className="btn-secondary min-h-11 px-2" onClick={() => setH((n) => undo(n))} disabled={!h.past.length} data-testid="editor-undo">Undo</button>
                <button type="button" className="btn-secondary min-h-11 px-2" onClick={() => setH((n) => redo(n))} disabled={!h.future.length} data-testid="editor-redo">Redo</button>
                <button type="button" className="btn-secondary min-h-11 px-2" onClick={reset} data-testid="editor-reset">Reset</button>
                <button type="button" className={`min-h-11 px-2 ${tapping ? "btn-primary" : "btn-secondary"}`} aria-pressed={tapping}
                  onClick={() => { setTapping(!tapping); setNote(null); }} disabled={finding || areas.length >= 24} data-testid="editor-add">
                  Add area
                </button>
                <button type="button" className="btn-secondary min-h-11 px-2 text-rose-700" onClick={() => remove(selected)}
                  disabled={areas.length <= 1} data-testid="editor-delete">
                  Delete area
                </button>
                <button type="button" className={`min-h-11 px-2 ${free ? "btn-primary" : "btn-secondary"}`} aria-pressed={free} onClick={toggleFree}
                  data-testid="editor-free">
                  Free corners
                </button>
                <button type="button" className={`min-h-11 px-2 ${snapOn ? "btn-primary" : "btn-secondary"}`} aria-pressed={snapOn}
                  onClick={() => setSnapOn(!snapOn)} data-testid="editor-snap">
                  Snap
                </button>
                <button type="button" className={`min-h-11 px-2 ${showDesign ? "btn-primary" : "btn-secondary"}`} aria-pressed={showDesign}
                  onClick={() => setShowDesign(!showDesign)} data-testid="editor-preview">
                  Preview
                </button>
                <button type="button" className={`min-h-11 px-2 ${fabric ? "btn-primary" : "btn-secondary"}`} aria-pressed={!!fabric} onClick={showFabric}
                  data-testid="editor-fabric">
                  On fabric
                </button>
              </div>
              <p className="text-xs text-slate-500">{EDITOR_HINT}</p>
              {areas.length > 1 && (
                <div key="areas" className="flex flex-wrap gap-2" aria-label="Print areas">
                  {areas.map((_, i) => (
                    <button key={i} type="button" aria-pressed={i === selected}
                      className={`min-h-11 min-w-11 rounded-md border px-3 text-sm ${i === selected ? "border-brand-600 bg-brand-50 text-brand-800" : "border-slate-300 text-slate-700"}`}
                      onClick={() => setSelected(i)} data-testid="editor-area-pick">
                      <span>{`Area ${i + 1}`}</span>
                    </button>
                  ))}
                </div>
              )}
              {note && <p key="note" role="status" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">{note}</p>}
              <label className="block">
                <span className="flex items-baseline justify-between gap-2">
                  <span className="label mb-0">Design size</span>
                  <span className="text-xs tabular-nums text-slate-600" translate="no">{percent(size)}</span>
                </span>
                <input type="range" min={SIZE_RANGE[0]} max={SIZE_RANGE[1]} step={0.01} value={size}
                  className="mt-1 block h-11 w-full accent-brand-600" onChange={(e) => setSize(Number(e.target.value))} data-testid="editor-size" />
              </label>
              <label className="block">
                <span className="label">Preview with your design</span>
                <input type="file" accept="image/png,image/jpeg,image/webp" className="field block w-full min-w-0 text-sm"
                  onChange={(e) => chooseDesign(e.target.files?.[0])} data-testid="editor-design-file" />
                <span className="mt-1 block text-xs text-slate-500">Shown here only, nothing is uploaded.</span>
              </label>
              <details className="rounded-md border border-slate-200">
                <summary className="flex min-h-11 cursor-pointer items-center px-3 text-sm font-medium text-slate-700">More settings</summary>
                <div className="space-y-3 border-t border-slate-200 p-3">
                  <label className="block">
                    <span className="label">Shows the</span>
                    <select className="field w-full" value={photo.view === "back" ? "back" : "front"}
                      onChange={(e) => setting({ view: e.target.value as "front" | "back" })} data-testid="editor-view">
                      <option value="front">Front</option>
                      <option value="back">Back</option>
                    </select>
                  </label>
                  <label className="block">
                    <span className="flex items-baseline justify-between gap-2">
                      <span className="label mb-0">Fold strength</span>
                      <span className="text-xs tabular-nums text-slate-600" translate="no">{percent(photo.fold_strength)}</span>
                    </span>
                    <input type="range" min={FOLD_RANGE[0]} max={FOLD_RANGE[1]} step={0.01} defaultValue={photo.fold_strength}
                      className="mt-1 block h-11 w-full accent-brand-600"
                      onPointerUp={(e) => setting({ fold_strength: Number((e.target as HTMLInputElement).value) })}
                      onKeyUp={(e) => setting({ fold_strength: Number((e.target as HTMLInputElement).value) })} />
                  </label>
                  {photo.has_occluder && (
                    <label key="occlusion" className="flex min-h-11 items-start gap-2.5 text-sm text-slate-700">
                      <input type="checkbox" className="mt-1 h-5 w-5 shrink-0 rounded border-slate-300" checked={photo.occlusion !== false}
                        onChange={(e) => setting({ occlusion: e.target.checked })} />
                      <span>{OCCLUSION_LABEL}</span>
                    </label>
                  )}
                </div>
              </details>
              {others.length > 0 && (
                <div key="copy" className="space-y-2 rounded-md border border-slate-200 p-3" data-testid="editor-copy">
                  {copying ? (
                    <div key="choose" className="space-y-2">
                      <p className="text-sm font-medium text-slate-700">Copy this area to:</p>
                      <ul className="max-h-48 space-y-1 overflow-y-auto">
                        {others.map((p) => (
                          <li key={p.id}>
                            <label className="flex min-h-11 min-w-0 items-center gap-2.5 text-sm text-slate-700">
                              <input type="checkbox" className="h-5 w-5 shrink-0 rounded border-slate-300" checked={copyIds.includes(p.id)}
                                onChange={(e) => setCopyIds((ids) => (e.target.checked ? [...ids, p.id] : ids.filter((x) => x !== p.id)))}
                                data-testid="editor-copy-pick" />
                              {/* eslint-disable-next-line @next/next/no-img-element */}
                              <img src={api.mockupPhotoImageUrl(p.id, 112)} alt="" className="h-9 w-9 shrink-0 rounded object-cover" loading="lazy" />
                              <span className="min-w-0 truncate" title={p.name}>{p.name}</span>
                            </label>
                          </li>
                        ))}
                      </ul>
                      <p className="text-xs text-slate-500">Each photo then opens with the area on it for you to confirm. This photo is confirmed too.</p>
                      <div className="flex flex-wrap gap-2">
                        <button type="button" className="btn-primary min-h-11" onClick={copy} disabled={!copyIds.length || saving} data-testid="editor-copy-go">
                          <span translate="no">{`Copy to ${copyIds.length} ${copyIds.length === 1 ? "photo" : "photos"}`}</span>
                        </button>
                        <button type="button" className="btn-secondary min-h-11" onClick={() => setCopying(false)}>Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <button key="open" type="button" className="btn-secondary h-auto min-h-11 w-full whitespace-normal"
                      onClick={() => { setCopying(true); setCopyIds(others.filter((p) => !p.confirmed).map((p) => p.id)); }}
                      data-testid="editor-copy-open">
                      Copy this area to other photos in the template
                    </button>
                  )}
                </div>
              )}
              {error && <p key="error" role="alert" className="text-sm text-rose-700">{error}</p>}
            </div>
          </aside>
        </div>

        <footer className="flex items-center gap-2 border-t border-slate-200 bg-white px-3 py-2 sm:px-4">
          <span className="hidden min-w-0 flex-1 truncate text-xs text-slate-500 sm:block" translate="no">
            {`${areas.length} ${areas.length === 1 ? "print area" : "print areas"}${snapOn ? "" : " · snapping off"}`}
          </span>
          <button type="button" className="btn-primary min-h-12 flex-1 text-base sm:flex-none sm:px-10" onClick={confirm} disabled={saving}
            data-testid="editor-confirm">
            {saving ? "Saving…" : LOOKS_RIGHT}
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
