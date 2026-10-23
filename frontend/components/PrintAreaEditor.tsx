"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import type { MockupPhoto, MockupSubgroup } from "@/lib/types";
import { ColourToggle } from "./ColourToggle";
import { trimmedDesign } from "@/lib/designTrim";
import {
  MIN_SIZE,
  SNAP_SCREEN_PX,
  across,
  angleDegrees,
  angleKey,
  axisGuide,
  copyToFound,
  foundNote,
  designQuad,
  fitAreaToDesign,
  dragCorner,
  garmentAxis,
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
  duplicateQuad,
  edgeHandles,
  resizeEdge,
  resizeKeep,
  resizeRect,
  subgroupColour,
  rotate,
  rotateHandle,
  snapAngle,
  snapArea,
  snapPoint,
  toPx,
  toShares,
  turn,
  turnTo,
  undo,
  type Guide,
  type History,
  type Pt,
  type Quad,
} from "@/lib/areaEditor";
import {
  BRUSH_HINT, EDITOR_HINT, FOLD_RANGE, LAYOUT_HINT, LAYOUT_LABELS, LOOKS_RIGHT, OCCLUSION_LABEL, ROLE_LABELS, SAMPLE_DESIGN, SIZE_RANGE,
  SUGGESTION_NOTE, ANY_SUBGROUP, areaSubgroup, percent, type PhotoLayout,
} from "@/lib/mockups";

/**
 * The print-area editor (backend api/mockups.py): one photo, large, with its suggested
 * area(s) drawn. The seller moves, resizes and turns each area (smart guides snap it to the
 * garment's axis, its collar line, the photo's centre and the other areas), adds an area by
 * tapping a garment, previews a design inside it, and presses "Looks right": only then is
 * the photo used for mockups. "Copy to other photos" gives the areas to colour variants,
 * each shown on its own photo to confirm. Full screen on a phone, at least 900 px wide on a
 * desktop; every control is at least 44 px. Nothing here calls Etsy or an AI model.
 */
/** ``axis``: the garment's own axis (radians) a turn snaps to and "Match garment angle" uses. */
type Area = {
  quad: Quad; suggested: Quad; source: string; kind?: string | null; axis: number | null; role: "front" | "back";
  /** The subgroup the seller linked it to (null: by the garment's tone), and that tone. */
  subgroup?: string | null; tone?: string | null;
  /** Linked to "Any": the design's own file, whatever the garment's tone. */
  subgroupAny?: boolean;
  /** "Design goes behind hair and arms" here, and why the analysis turned it off. */
  occlusion: boolean; occlusionNote?: string | null;
};
type Drag =
  | { kind: "move"; index: number; start: Pt; quad: Quad }
  | { kind: "corner"; index: number; corner: number; start: Pt; quad: Quad }
  | { kind: "rotate"; index: number; start: Pt; quad: Quad }
  | { kind: "edge"; index: number; edge: number; start: Pt; quad: Quad };

const CORNERS = ["Top left", "Top right", "Bottom right", "Bottom left"];
const GUIDE_COLOUR: Record<Guide["kind"], string> = { axis: "#db2777", collar: "#ea580c", area: "#2563eb", photo: "#16a34a" };

export function PrintAreaEditor({
  photo,
  siblings,
  subgroups = [],
  step,
  onConfirmed,
  onCopied,
  onChanged,
  onClose,
}: {
  photo: MockupPhoto;
  /** The other photos of the template (for "Copy to other photos"). */
  siblings: MockupPhoto[];
  /** The template's subgroups: with two or more, each area says which one it prints for. */
  subgroups?: MockupSubgroup[];
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
    () => photo.areas.map((a) => {
      const suggested = toPx(a.detected_area, W, H);
      return {
        quad: toPx(a.print_area, W, H), suggested, source: a.source, kind: a.kind, axis: garmentAxis(a.axis_angle, suggested),
        role: (a.role === "back" ? "back" : "front") as "front" | "back",
        subgroup: a.subgroup_id ?? null, tone: a.tone ?? null, subgroupAny: !!a.subgroup_any,
        occlusion: a.occlusion !== false, occlusionNote: a.occlusion_note ?? null,
      };
    }),
    [photo.id, photo.version], // eslint-disable-line react-hooks/exhaustive-deps
  );
  const [h, setH] = useState<History<Area[]>>(() => history(initial));
  const [live, setLive] = useState<Area[] | null>(null);
  const [selected, setSelected] = useState(0);
  const [free, setFree] = useState(() => initial.some((a) => !isRect(a.quad)));
  // Corner handles keep the area's proportions (Shift switches it while dragging); edge handles move one side.
  const [keepRatio, setKeepRatio] = useState(true);
  // "Print on all garments": the same design on every area of this photo.
  const [printAll, setPrintAll] = useState(!!photo.print_all);
  const [snapOn, setSnapOn] = useState(true);
  const [showDesign, setShowDesign] = useState(true);
  // What the analysis put in front of the garment, tinted over the photo.
  const [showFront, setShowFront] = useState(false);
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
  // "Keep in front": the seller paints what must stay in front of the design (hair, a hand)
  // where the analysis missed it, or erases what it wrongly put in front. Painted on an
  // off-screen gray canvas (white in front, black not, mid gray as found) at screen size;
  // saved to our server, which stores it at the photo's size (backend api/mockups.py).
  const [brushing, setBrushing] = useState(false);
  const [brushMode, setBrushMode] = useState<"front" | "behind">("front");
  const [brushSize, setBrushSize] = useState(28);
  const [brushDirty, setBrushDirty] = useState(false);
  const tint = useRef<HTMLCanvasElement>(null);
  const brushCanvas = useRef<HTMLCanvasElement | null>(null);
  const painting = useRef(false);
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
      quad = free ? dragCorner(d.quad, d.corner, s.value)
        : keepRatio !== e.shiftKey ? resizeKeep(d.quad, d.corner, s.value, MIN_SIZE) : resizeRect(d.quad, d.corner, s.value, MIN_SIZE);
      shown = s.active;
    } else if (d.kind === "edge") {
      const s = snapPoint(p, guides, reach(e.altKey));
      quad = resizeEdge(d.quad, d.edge, s.value, MIN_SIZE);
      shown = s.active;
    } else {
      const start = rectOf(d.quad).angle;
      const wanted = start + turn(d.quad, d.start, p);
      const s = snapAngle(wanted, areas[d.index].axis, snapOn && !e.altKey ? undefined : 0);
      quad = rotate(d.quad, s.angle - start);
      if (s.snapped === "axis") shown = [axisGuide(quad, s.angle)];
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

  // The brush opens on what is in front now: the stored brush (gray) and, tinted, the
  // occluder every mockup uses. Both at the photo's on-screen size.
  useEffect(() => {
    if (!brushing || !box.w || !box.h) return;
    let cancelled = false;
    const off = document.createElement("canvas");
    off.width = box.w;
    off.height = box.h;
    const octx = off.getContext("2d")!;
    octx.fillStyle = "rgb(128,128,128)";
    octx.fillRect(0, 0, box.w, box.h);
    brushCanvas.current = off;
    const load = (src: string) => new Promise<HTMLImageElement | null>((resolve) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = src;
    });
    (async () => {
      const [stored, combined] = await Promise.all([
        load(api.mockupFrontUrl(photo.id, "brush", photo.version)),
        photo.has_occluder ? load(api.mockupFrontUrl(photo.id, "combined", photo.version)) : Promise.resolve(null),
      ]);
      if (cancelled) return;
      if (stored) {
        octx.imageSmoothingEnabled = false;
        octx.drawImage(stored, 0, 0, box.w, box.h);
      }
      const view = tint.current?.getContext("2d");
      if (!view) return;
      view.clearRect(0, 0, box.w, box.h);
      if (combined) {
        view.drawImage(combined, 0, 0, box.w, box.h);
        const data = view.getImageData(0, 0, box.w, box.h);
        for (let i = 0; i < data.data.length; i += 4) {
          const a = data.data[i];
          data.data[i] = 220; data.data[i + 1] = 38; data.data[i + 2] = 38; data.data[i + 3] = a;
        }
        view.putImageData(data, 0, 0);
      }
    })();
    return () => { cancelled = true; };
  }, [brushing, box.w, box.h, photo.id, photo.version, photo.has_occluder]);

  function paintAt(e: React.PointerEvent) {
    const r = frame.current!.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    const off = brushCanvas.current?.getContext("2d");
    const view = tint.current?.getContext("2d");
    if (!off || !view) return;
    off.globalCompositeOperation = "source-over";
    off.fillStyle = brushMode === "front" ? "rgb(255,255,255)" : "rgb(0,0,0)";
    off.beginPath();
    off.arc(x, y, brushSize / 2, 0, Math.PI * 2);
    off.fill();
    view.globalCompositeOperation = brushMode === "front" ? "source-over" : "destination-out";
    view.fillStyle = "rgb(220,38,38)";
    view.beginPath();
    view.arc(x, y, brushSize / 2, 0, Math.PI * 2);
    view.fill();
    view.globalCompositeOperation = "source-over";
    setBrushDirty(true);
  }

  async function saveBrush() {
    const off = brushCanvas.current;
    if (!off) return;
    setSaving(true);
    setError(null);
    try {
      const blob = await new Promise<Blob | null>((resolve) => off.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("The brush could not be read.");
      onChanged(await api.saveFrontBrush(photo.id, blob));
      setBrushing(false);
      setBrushDirty(false);
    } catch (err: any) {
      setError(String(err.message ?? err));
    } finally {
      setSaving(false);
    }
  }

  async function clearBrush() {
    setError(null);
    try {
      onChanged(await api.clearFrontBrush(photo.id));
      setBrushing(false);
      setBrushDirty(false);
    } catch (err: any) {
      setError(String(err.message ?? err));
    }
  }

  async function onStageDown(e: React.PointerEvent) {
    if (!tapping || finding) return;
    e.preventDefault();
    const [x, y] = at(e);
    if (x < 0 || y < 0 || x > W || y > H) return;
    setFinding(true);
    setNote(null);
    try {
      const last = areas[areas.length - 1];
      const found = await api.findMockupGarment(photo.id, x / W, y / H, areas.map((a) => toShares(a.quad, W, H)),
        last ? toShares(last.quad, W, H) : null);
      const quad = toPx(found.print_area, W, H);
      const next = [...areas, { quad, suggested: quad, source: found.found ? "tap" : "seller", kind: found.kind, axis: null, role: "front" as const, occlusion: true }];
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
    const turnBy = angleKey(e.code, e.shiftKey);
    if (turnBy !== null && areas[selected]) {
      e.preventDefault();
      commit(areas.map((a, i) => (i === selected ? { ...a, quad: rotate(a.quad, turnBy) } : a)));
      return;
    }
    const by = nudge(e.key, e.shiftKey, W);
    if (!by || !areas[selected]) return;
    e.preventDefault();
    commit(areas.map((a, i) => (i === selected ? { ...a, quad: move(a.quad, by[0], by[1]) } : a)));
  }

  /** "Copy this area to all garments": a copy on each garment the analysis found, by that garment's
   *  own geometry; never where no garment was found (lib/areaEditor.ts ``copyToFound``). */
  function copyToAll() {
    const src = areas[selected];
    if (!src) return;
    const found = (photo.found_areas ?? []).map((q) => toPx(q, W, H));
    const done = copyToFound(areas, selected, found, (quad, garment) => ({
      ...src, quad, suggested: garment, source: "copied", kind: src.kind ?? null, axis: null,
    }));
    if (done.copied) commit(done.areas.map((a, i) => (i === selected || a === areas[i] ? a : { ...a, source: a.source === "copied" ? "copied" : "seller" })));
    setNote(done.note);
  }

  function setSubgroup(id: string | null) {
    // "Any": the design's own file; a subgroup: its file; null: by the garment's colour.
    commit(areas.map((a, i) => (i === selected ? { ...a, subgroup: id === ANY_SUBGROUP.id ? null : id, subgroupAny: id === ANY_SUBGROUP.id } : a)));
  }

  /** "Duplicate area": the selected area copied a little aside, its size, angle and subgroup link kept. */
  function duplicate() {
    const a = areas[selected];
    if (!a || areas.length >= 24) return;
    const quad = duplicateQuad(a.quad, W, H);
    commit([...areas, { ...a, quad, suggested: quad, source: "seller" }]);
    setSelected(areas.length);
  }

  function labelColour(a: Area): string {
    const g = areaSubgroup({ subgroup_id: a.subgroup, tone: a.tone, subgroup_any: a.subgroupAny }, photo.subgroup_id, subgroups);
    return subgroupColour(g && g.id !== ANY_SUBGROUP.id ? subgroups.findIndex((x) => x.id === g.id) : -1);
  }

  function setRole(role: "front" | "back") {
    commit(areas.map((a, i) => (i === selected ? { ...a, role } : a)));
  }

  // "Fit area to design": the area takes the design's proportions, its top-centre fixed.
  function fitToDesign() {
    commit(areas.map((x, i) => (i === selected ? { ...x, quad: fitAreaToDesign(x.quad, design.width, design.height, W, H) } : x)));
  }

  function setBehind(on: boolean) {
    commit(areas.map((x, i) => (i === selected ? { ...x, occlusion: on, occlusionNote: on ? null : x.occlusionNote } : x)));
  }

  function matchGarment() {
    const a = areas[selected];
    if (!a || a.axis === null) return;
    commit(areas.map((x, i) => (i === selected ? { ...x, quad: turnTo(x.quad, a.axis as number) } : x)));
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
      return {
        print_area: shares, detected_area: suggested, source: unchanged ? a.source : "seller", kind: a.kind ?? null, role: a.role,
        subgroup_id: a.subgroup ?? null, tone: a.tone ?? null, occlusion: a.occlusion, subgroup_any: !!a.subgroupAny,
      };
    });
  }

  async function confirm() {
    setSaving(true);
    setError(null);
    try {
      onConfirmed(await api.saveMockupAreas(photo.id, payload(), true, size, printAll));
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
      const saved = await api.saveMockupAreas(photo.id, payload(), true, size, printAll);
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
      const blob = await api.previewMockupPhoto(photo.id, { areas: h.present.map((a) => toShares(a.quad, W, H)), size, behind: h.present.map((a) => a.occlusion) });
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

  async function chooseDesign(file: File | undefined) {
    if (!file) return;
    // Shown here only; nothing is uploaded. Cropped to its pixels the way the server trims it,
    // so the preview and the mockup place it identically.
    try {
      setDesign(await trimmedDesign(file));
    } catch {
      const url = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => setDesign({ url, width: img.naturalWidth || 1000, height: img.naturalHeight || 1000 });
      img.src = url;
    }
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
              <img src={api.mockupPhotoImageUrl(photo.id, "medium")} alt="" draggable={false}
                className="pointer-events-none absolute inset-0 h-full w-full" />
              {showDesign && !fabric && areas.map((a, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img key={`d${i}`} src={design.url} alt="" draggable={false} aria-hidden
                  className="pointer-events-none absolute left-0 top-0 max-w-none opacity-90"
                  style={{ width: design.width, height: design.height, transformOrigin: "0 0",
                    transform: matrix3d(design.width, design.height, designQuad(a.quad, design.width, design.height, size).map(([x, y]) => [x * scale, y * scale]) as Quad) }}
                  data-testid="editor-design" />
              ))}
              {showDesign && !fabric && photo.has_occluder && photo.occlusion !== false && areas.some((a) => a.occlusion) && (
                // What is in front of the garment (hair, hands), over the design, in the areas whose
                // switch is on: the live preview hides the design exactly where the mockup does.
                <svg key="front" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden
                  className="pointer-events-none absolute inset-0 h-full w-full" data-testid="editor-front-layer">
                  <defs>
                    <clipPath id={`front-${photo.id}`}>
                      {areas.map((a, i) => (a.occlusion ? <polygon key={i} points={a.quad.map((p) => p.join(",")).join(" ")} /> : null))}
                    </clipPath>
                  </defs>
                  <image href={api.mockupFrontUrl(photo.id, "cutout", photo.version)} x={0} y={0} width={W} height={H}
                    preserveAspectRatio="none" clipPath={`url(#front-${photo.id})`} />
                </svg>
              )}
              {showFront && photo.has_occluder && (
                <div key="front-overlay" aria-hidden className="pointer-events-none absolute inset-0 bg-fuchsia-500/60"
                  style={{ WebkitMaskImage: `url(${api.mockupFrontUrl(photo.id, "combined", photo.version)})`,
                    maskImage: `url(${api.mockupFrontUrl(photo.id, "combined", photo.version)})`,
                    WebkitMaskSize: "100% 100%", maskSize: "100% 100%", maskMode: "luminance" } as React.CSSProperties}
                  data-testid="editor-occlusion-overlay" />
              )}
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
                <span key={`n${i}`} className="pointer-events-none absolute max-w-[45%] -translate-x-1/2 -translate-y-1/2 truncate whitespace-nowrap rounded bg-slate-900/70 px-1.5 text-xs font-semibold text-white"
                  style={{ ...px([(a.quad[0][0] + a.quad[2][0]) / 2, (a.quad[0][1] + a.quad[2][1]) / 2]),
                    ...(subgroups.length > 1 ? { background: labelColour(a) } : {}) }} translate="no"
                  data-testid="editor-area-label">
                  {[
                    `${i + 1}`,
                    photo.layout === "front_back" || a.role === "back" ? ROLE_LABELS[a.role] : "",
                    subgroups.length > 1 ? areaSubgroup({ subgroup_id: a.subgroup, tone: a.tone, subgroup_any: a.subgroupAny }, photo.subgroup_id, subgroups)?.name ?? "" : "",
                  ].filter(Boolean).join(" · ")}
                </span>
              ))}
              {brushing && (
                <canvas key="brush" ref={tint} width={box.w} height={box.h} aria-label="Keep in front: paint on the photo"
                  className="absolute inset-0 z-10 h-full w-full cursor-crosshair touch-none opacity-50"
                  onPointerDown={(e) => { e.preventDefault(); e.stopPropagation(); painting.current = true; (e.target as HTMLElement).setPointerCapture(e.pointerId); paintAt(e); }}
                  onPointerMove={(e) => painting.current && paintAt(e)}
                  onPointerUp={() => { painting.current = false; }}
                  onPointerCancel={() => { painting.current = false; }}
                  data-testid="editor-brush-canvas" />
              )}
              {sel && !tapping && !brushing && sel.quad.map((p, c) => (
                <button key={`c${c}`} type="button" aria-label={`${CORNERS[c]} corner`}
                  className="absolute flex h-11 w-11 -translate-x-1/2 -translate-y-1/2 touch-none items-center justify-center rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                  style={px(p)} onPointerDown={(e) => begin(e, { kind: "corner", index: selected, corner: c, start: at(e), quad: sel.quad })}
                  data-testid="editor-corner">
                  <span aria-hidden className={`h-4 w-4 border-2 border-white bg-brand-600 shadow ${free ? "rotate-45" : "rounded-full"}`} />
                </button>
              ))}
              {sel && !tapping && !brushing && !free && edgeHandles(sel.quad).map((p, k) => (
                <button key={`e${k}`} type="button" aria-label={`${["Top", "Right", "Bottom", "Left"][k]} side`}
                  className="absolute flex h-11 w-11 -translate-x-1/2 -translate-y-1/2 touch-none items-center justify-center rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                  style={px(p)} onPointerDown={(e) => begin(e, { kind: "edge", index: selected, edge: k, start: at(e), quad: sel.quad })}
                  data-testid="editor-edge">
                  <span aria-hidden className={`border-2 border-white bg-brand-600 shadow ${k % 2 ? "h-4 w-2" : "h-2 w-4"} rounded-sm`} />
                </button>
              ))}
              {sel && !tapping && !brushing && subgroups.length > 1 && (
                <div key="chooser" role="radiogroup" aria-label="This area prints for"
                  className="absolute z-10 flex max-w-[90%] -translate-x-1/2 flex-wrap justify-center gap-1 rounded-lg bg-white/95 p-1 shadow-lg"
                  style={px([(sel.quad[2][0] + sel.quad[3][0]) / 2, Math.min(H - 1, Math.max(sel.quad[2][1], sel.quad[3][1]) + 8 / scale)])}
                  onPointerDown={(e) => e.stopPropagation()} data-testid="editor-subgroup-chooser">
                  {[...subgroups, ANY_SUBGROUP].map((g, k) => {
                    const on = g.id === ANY_SUBGROUP.id ? !!sel.subgroupAny : !sel.subgroupAny && sel.subgroup === g.id;
                    return (
                      <button key={g.id} type="button" className={`min-h-11 min-w-11 rounded-md px-2 text-xs font-semibold ${on ? "text-white" : "text-slate-700 ring-1 ring-inset ring-slate-300"}`}
                        role="radio" aria-checked={on} onClick={() => setSubgroup(on ? null : g.id)}
                        style={on ? { background: g.id === ANY_SUBGROUP.id ? "#475569" : subgroupColour(k) } : undefined}
                        data-testid="editor-subgroup-option">
                        <span translate="no">{g.name}</span>
                      </button>
                    );
                  })}
                </div>
              )}
              {sel && !tapping && !brushing && (
                <button key="rotate" type="button" aria-label="Turn the print area"
                  className="absolute flex h-11 w-11 -translate-x-1/2 -translate-y-1/2 touch-none items-center justify-center rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                  style={px(rotateHandle(sel.quad, handleGap))}
                  onPointerDown={(e) => begin(e, { kind: "rotate", index: selected, start: at(e), quad: sel.quad })}
                  data-testid="editor-rotate">
                  <span aria-hidden className="flex h-8 w-8 items-center justify-center rounded-full border-2 border-white bg-brand-600 text-base text-white shadow-md ring-2 ring-brand-600/30">↻</span>
                </button>
              )}
            </div>
            {(tapping || finding) && (
              <p key="tap" role="status" className="absolute left-1/2 top-3 -translate-x-1/2 rounded-md bg-slate-900/80 px-3 py-2 text-sm text-white" translate="no">
                {finding ? "Finding that garment…" : "Click a garment to add an area"}
              </p>
            )}
          </div>

          <aside className="flex max-h-[45vh] min-w-0 flex-col overflow-y-auto border-t border-slate-200 lg:max-h-none lg:w-80 lg:border-l lg:border-t-0">
            <div className="space-y-4 p-3 sm:p-4">
              <p className="text-sm text-slate-600">{SUGGESTION_NOTE}</p>
              <label className="block" title={LAYOUT_HINT}>
                <span className="label">Layout</span>
                <select className="field w-full" value={(photo.layout as PhotoLayout) ?? "single"}
                  onChange={(e) => setting({ layout: e.target.value as PhotoLayout })} disabled={dirty} data-testid="editor-layout">
                  {(Object.keys(LAYOUT_LABELS) as PhotoLayout[]).map((v) => <option key={v} value={v}>{LAYOUT_LABELS[v]}</option>)}
                </select>
                <span className="mt-1 block text-xs text-slate-500">{dirty ? "Save or reset your changes to change the layout." : LAYOUT_HINT}</span>
              </label>
              <ColourToggle photo={photo} onChoose={(colour) => setting({ colour })} />
              <div className="grid grid-cols-3 gap-2" role="toolbar" aria-label="Edit the print area">
                <button type="button" className="btn-secondary min-h-11 px-2" onClick={() => setH((n) => undo(n))} disabled={!h.past.length} data-testid="editor-undo">Undo</button>
                <button type="button" className="btn-secondary min-h-11 px-2" onClick={() => setH((n) => redo(n))} disabled={!h.future.length} data-testid="editor-redo">Redo</button>
                <button type="button" className="btn-secondary min-h-11 px-2" onClick={reset} data-testid="editor-reset">Reset</button>
                <button type="button" className={`min-h-11 px-2 ${tapping ? "btn-secondary btn-on" : "btn-secondary"}`} aria-pressed={tapping}
                  onClick={() => { setTapping(!tapping); setNote(null); }} disabled={finding || areas.length >= 24} data-testid="editor-add">
                  Add area
                </button>
                <button type="button" className="btn-danger min-h-11 px-2" onClick={() => remove(selected)}
                  disabled={areas.length <= 1} data-testid="editor-delete">
                  Delete area
                </button>
                <button type="button" className={`min-h-11 px-2 ${free ? "btn-secondary btn-on" : "btn-secondary"}`} aria-pressed={free} onClick={toggleFree}
                  data-testid="editor-free">
                  Free corners
                </button>
                <button type="button" className="btn-secondary min-h-11 px-2" onClick={duplicate} disabled={!sel || areas.length >= 24}
                  aria-label="Duplicate area" title="Duplicate area" data-testid="editor-duplicate">
                  Duplicate
                </button>
                <button type="button" className={`col-span-2 min-h-11 px-2 ${keepRatio ? "btn-secondary btn-on" : "btn-secondary"}`} aria-pressed={keepRatio}
                  onClick={() => setKeepRatio(!keepRatio)} title="Shift switches it while you drag a corner" data-testid="editor-ratio">
                  Keep proportions
                </button>
                <button type="button" className={`min-h-11 px-2 ${snapOn ? "btn-secondary btn-on" : "btn-secondary"}`} aria-pressed={snapOn}
                  onClick={() => setSnapOn(!snapOn)} data-testid="editor-snap">
                  Snap
                </button>
                <button type="button" className={`min-h-11 px-2 ${showDesign ? "btn-secondary btn-on" : "btn-secondary"}`} aria-pressed={showDesign}
                  onClick={() => setShowDesign(!showDesign)} data-testid="editor-preview">
                  Preview
                </button>
                <button type="button" className={`min-h-11 px-2 ${fabric ? "btn-secondary btn-on" : "btn-secondary"}`} aria-pressed={!!fabric} onClick={showFabric}
                  data-testid="editor-fabric">
                  On fabric
                </button>
                {areas.length > 1 && (
                  <button key="print-all" type="button" className={`col-span-3 min-h-11 px-2 ${printAll ? "btn-secondary btn-on" : "btn-secondary"}`}
                    aria-pressed={printAll} onClick={() => setPrintAll(!printAll)} data-testid="editor-print-all">
                    Print on all garments
                  </button>
                )}
                {areas.length > 1 && (
                  <button key="copy-all" type="button" className="col-span-3 min-h-11 px-2 btn-secondary" onClick={copyToAll}
                    data-testid="editor-copy-all">
                    Copy this area to all garments
                  </button>
                )}
                {areas.length > 1 && (photo.garments_found ?? 0) < areas.length && (
                  <p key="found" className="col-span-3 text-xs text-slate-600" translate="no" data-testid="editor-found">
                    {foundNote(photo.garments_found ?? 0, areas.length)}
                  </p>
                )}
                {sel && subgroups.length > 1 && (
                  <label key="subgroup" className="col-span-3 block">
                    <span className="label">This area prints for</span>
                    <select className="field w-full" value={sel.subgroupAny ? ANY_SUBGROUP.id : sel.subgroup ?? ""} onChange={(e) => setSubgroup(e.target.value || null)}
                      data-testid="editor-area-subgroup">
                      <option value="">
                        {`${areaSubgroup({ tone: sel.tone, subgroup_id: null }, photo.subgroup_id, subgroups)?.name ?? ""} (by garment colour)`}
                      </option>
                      {subgroups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                      <option value={ANY_SUBGROUP.id}>Any (the design&apos;s own file)</option>
                    </select>
                  </label>
                )}
                {sel && (
                  <div key="role" className="col-span-3 grid grid-cols-2 gap-2" role="radiogroup" aria-label="This area prints the">
                    {(["front", "back"] as const).map((r) => (
                      <button key={r} type="button" role="radio" aria-checked={sel.role === r}
                        className={`min-h-11 px-2 ${sel.role === r ? "btn-secondary btn-on" : "btn-secondary"}`} onClick={() => setRole(r)}
                        data-testid={`editor-role-${r}`}>
                        {`${ROLE_LABELS[r]} design`}
                      </button>
                    ))}
                  </div>
                )}
                <button type="button" className="col-span-3 min-h-11 px-2 btn-secondary" onClick={fitToDesign} disabled={!sel}
                  title="Give the area the design's proportions, keeping its top-centre where it is" data-testid="editor-fit-design">
                  Fit area to design
                </button>
                {sel && photo.has_occluder && (
                  <div key="behind" className="col-span-3 space-y-1">
                    <button type="button" role="switch" aria-checked={sel.occlusion}
                      className={`flex min-h-11 w-full items-center justify-between gap-2 rounded-md border px-3 text-left text-sm ${sel.occlusion ? "border-brand-600 bg-brand-50 text-brand-800" : "border-slate-300 bg-white text-slate-700"}`}
                      onClick={() => setBehind(!sel.occlusion)} data-testid="editor-behind">
                      <span>Design goes behind hair and arms</span>
                      <span className="shrink-0 font-semibold" translate="no">{sel.occlusion ? "On" : "Off"}</span>
                    </button>
                    {!sel.occlusion && sel.occlusionNote && (
                      <p key="why" className="text-xs text-amber-800" data-testid="editor-behind-note">{sel.occlusionNote}</p>
                    )}
                  </div>
                )}
                {photo.has_occluder && (
                  <button key="show-front" type="button" aria-pressed={showFront}
                    className={`col-span-3 min-h-11 px-2 ${showFront ? "btn-secondary btn-on" : "btn-secondary"}`}
                    onClick={() => setShowFront(!showFront)} data-testid="editor-show-front">
                    {showFront ? "Hide what is in front" : "Show what is in front"}
                  </button>
                )}
                <button type="button" className="col-span-3 min-h-11 px-2 btn-secondary" onClick={matchGarment}
                  disabled={!sel || sel.axis === null || Math.abs(angleDegrees(sel.quad) - angleDegrees(turnTo(sel.quad, sel.axis))) < 0.05}
                  title="Turn the print area to the garment's own angle" data-testid="editor-match-angle">
                  Match garment angle
                </button>
                <button type="button" className={`col-span-3 min-h-11 px-2 ${brushing ? "btn-secondary btn-on" : "btn-secondary"}`} aria-pressed={brushing}
                  onClick={() => { setBrushing(!brushing); setBrushDirty(false); setTapping(false); }} data-testid="editor-brush">
                  Keep in front
                </button>
              </div>
              {brushing ? (
                <div key="brush" className="space-y-3 rounded-md border border-slate-200 p-3" data-testid="editor-brush-panel">
                  <p className="text-xs text-slate-600">{BRUSH_HINT}</p>
                  <div className="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Brush">
                    <button type="button" role="radio" aria-checked={brushMode === "front"}
                      className={`min-h-11 px-2 ${brushMode === "front" ? "btn-secondary btn-on" : "btn-secondary"}`} onClick={() => setBrushMode("front")}
                      data-testid="editor-brush-paint">
                      Paint
                    </button>
                    <button type="button" role="radio" aria-checked={brushMode === "behind"}
                      className={`min-h-11 px-2 ${brushMode === "behind" ? "btn-secondary btn-on" : "btn-secondary"}`} onClick={() => setBrushMode("behind")}
                      data-testid="editor-brush-erase">
                      Erase
                    </button>
                  </div>
                  <label className="block">
                    <span className="label">Brush size</span>
                    <input type="range" min={8} max={80} step={2} value={brushSize} className="block h-11 w-full accent-brand-600"
                      onChange={(e) => setBrushSize(Number(e.target.value))} />
                  </label>
                  <div className="flex flex-wrap gap-2">
                    <button type="button" className="btn-primary min-h-11" onClick={saveBrush} disabled={saving || !brushDirty} data-testid="editor-brush-save">
                      Save brush
                    </button>
                    {photo.has_front_brush && (
                      <button key="clear" type="button" className="btn-secondary min-h-11" onClick={clearBrush} data-testid="editor-brush-clear">
                        Clear brush
                      </button>
                    )}
                    <button type="button" className="btn-secondary min-h-11" onClick={() => { setBrushing(false); setBrushDirty(false); }}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <p key="hint" className="text-xs text-slate-500">{EDITOR_HINT}</p>
              )}
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
              {sel && (
                <p key="angle" className="flex items-baseline justify-between gap-2 text-sm text-slate-700" data-testid="editor-angle-row">
                  <span>Angle</span>
                  <span className="tabular-nums" translate="no" data-testid="editor-angle">{`${angleDegrees(sel.quad).toFixed(1)}°`}</span>
                </p>
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
                              <img src={api.mockupPhotoImageUrl(p.id, "small")} alt="" className="h-9 w-9 shrink-0 rounded object-cover" loading="lazy" />
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
            {`${areas.length} ${areas.length === 1 ? "print area" : "print areas"}${sel ? ` · ${angleDegrees(sel.quad).toFixed(1)}°` : ""}${snapOn ? "" : " · snapping off"}`}
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
