"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { FileDrop } from "@/components/FileDrop";
import { PrintAreaEditor } from "@/components/PrintAreaEditor";
import { api } from "@/lib/api";
import { pick } from "@/lib/fileDrop";
import type { MockupPhoto, MockupTemplate } from "@/lib/types";
import {
  ACCURACY_NOTE,
  ALL_READY,
  LIMIT_NOTE,
  MAX_NAME,
  NEEDS_CHECK,
  RESET_PLACEMENT,
  RIGHTS_STATEMENT,
  TEMPLATES_INTRO,
  addDesignsHref,
  appliedLine,
  canAddDesigns,
  checkLine,
  nameFromFile,
  ownPlacementLine,
  templateNameFrom,
  waitingForCheck,
} from "@/lib/mockups";

/**
 * Mockup templates (backend api/mockups.py): each a named group of the seller's blank
 * photos (the same tee in ten colours, or several shots of one product). Photos are
 * uploaded into a template and appear at once, "preparing", while the server suggests
 * their print areas. Each new photo then opens in the editor (components/PrintAreaEditor.tsx)
 * for one quick check: a photo is used for mockups only after "Looks right", and is marked
 * "Needs your check" until then. Several photos can take one photo's area at once
 * ("Apply this area to selected": each placed by its own garment) and be confirmed together
 * ("Confirm selected"). Once a template has a confirmed photo, "Add designs" leads to the
 * bulk step; the last check says "All photos are ready". Photos are reordered, moved and
 * removed here. Nothing here calls Etsy or an AI model.
 */
export default function TemplatesPage() {
  const [templates, setTemplates] = useState<MockupTemplate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editor, setEditor] = useState<{ queue: string[]; index: number } | null>(null);
  // Photos just uploaded: each opens in the editor once it is ready.
  const [pending, setPending] = useState<string[]>([]);
  // The template whose last waiting photo was just confirmed: "All photos are ready".
  const [readyTemplate, setReadyTemplate] = useState<string | null>(null);
  const polls = useRef(0);

  const load = useCallback(async () => {
    try {
      setTemplates(await api.mockupTemplates());
      setError(null);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  const photos = (templates ?? []).flatMap((t) => t.photos);
  // Photos being prepared: look again until they are ready (or say it is taking long).
  const preparing = photos.some((p) => p.status === "preparing");
  useEffect(() => {
    if (!preparing) {
      polls.current = 0;
      return;
    }
    if (polls.current > 90) return;
    const t = setTimeout(() => {
      polls.current += 1;
      load();
    }, 2000);
    return () => clearTimeout(t);
  }, [preparing, templates, load]);

  // New photos that are ready open in the editor, in the order they were added.
  useEffect(() => {
    if (editor || !pending.length) return;
    const ready = pending.filter((id) => photos.find((p) => p.id === id)?.status === "ready");
    const waiting = pending.filter((id) => photos.find((p) => p.id === id)?.status === "preparing");
    if (!ready.length || waiting.length) return; // all of one upload together
    const queue = ready.filter((id) => photos.find((p) => p.id === id)?.needs_check);
    setPending([]);
    if (queue.length) setEditor({ queue, index: 0 });
  }, [pending, photos, editor]);

  const replace = (next: MockupPhoto) =>
    setTemplates((all) => (all ?? []).map((t) => ({ ...t, photos: t.photos.map((p) => (p.id === next.id ? next : p)) })));

  async function moveTemplate(id: string, by: -1 | 1) {
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

  const current = editor ? photos.find((p) => p.id === editor.queue[editor.index]) : undefined;
  const siblings = current ? (templates ?? []).find((t) => t.id === current.template_id)?.photos ?? [] : [];

  function next(saved: MockupPhoto) {
    const finished = !editor || editor.index + 1 >= editor.queue.length;
    setEditor((e) => (e && e.index + 1 < e.queue.length ? { ...e, index: e.index + 1 } : null));
    if (finished) {
      // The last photo that needed a check: say what comes next, straight away.
      const template = (templates ?? []).find((t) => t.id === saved.template_id);
      const stillWaiting = template?.photos.filter((p) => p.id !== saved.id && p.needs_check).length ?? 0;
      if (stillWaiting === 0) setReadyTemplate(saved.template_id);
    }
    load();
  }

  const anyConfirmed = (templates ?? []).filter(canAddDesigns);

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-start gap-4">
        <div className="max-w-2xl flex-1 space-y-2 text-sm text-slate-500">
          <p>{TEMPLATES_INTRO}</p>
          <p>{ACCURACY_NOTE}</p>
        </div>
        {anyConfirmed.length > 0 && (
          <Link key="add-designs" href={addDesignsHref(anyConfirmed.map((t) => t.id))} className="btn-primary min-h-11"
            data-testid="templates-add-designs">
            Add designs
          </Link>
        )}
      </div>

      <AddPhotos templates={templates ?? []} onAdded={(ids) => { setPending((p) => [...p, ...ids]); load(); }} />
      {error && <p key="error" role="alert" className="card p-4 text-sm text-rose-700">{error}</p>}
      {preparing && polls.current > 90 && (
        <p key="slow" role="status" className="card p-4 text-sm text-amber-800">
          Some photos are taking longer than usual to prepare. Reload the page in a minute.
        </p>
      )}
      {templates === null ? (
        <div key="loading" className="card h-40 animate-pulse" />
      ) : templates.length === 0 ? (
        <p key="empty" className="card p-6 text-sm text-slate-500">No templates yet. Add blank garment photos above.</p>
      ) : (
        <ol key="list" className="space-y-6" data-testid="templates">
          {templates.map((t, i) => (
            <li key={t.id}>
              <TemplateCard
                template={t}
                first={i === 0}
                last={i === templates.length - 1}
                onMove={(by) => moveTemplate(t.id, by)}
                onReload={load}
                onChange={(u) => setTemplates((all) => (all ?? []).map((x) => (x.id === u.id ? u : x)))}
                onOpen={(queue) => setEditor({ queue, index: 0 })}
                allReady={readyTemplate === t.id && t.needs_check === 0 && canAddDesigns(t)}
                onReadySeen={() => setReadyTemplate(null)}
              />
            </li>
          ))}
        </ol>
      )}
      {current && current.status === "ready" && (
        <PrintAreaEditor
          key="editor"
          photo={current}
          siblings={siblings}
          step={editor ? { index: editor.index, total: editor.queue.length } : null}
          onConfirmed={(saved) => { replace(saved); next(saved); }}
          onCopied={(copied, saved) => {
            replace(saved);
            copied.forEach(replace);
            setEditor(copied.length ? { queue: copied.map((p) => p.id), index: 0 } : null);
          }}
          onChanged={replace}
          onClose={() => { setEditor(null); load(); }}
        />
      )}
    </div>
  );
}

type Row = { key: string; file: File; name: string; pct: number; state: "waiting" | "sending" | "done" | "error"; message?: string };

/** Upload one or many photos: into a new template (named from them) or an existing one. */
function AddPhotos({ templates, onAdded, into }: { templates: MockupTemplate[]; onAdded: (ids: string[]) => void; into?: MockupTemplate }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [target, setTarget] = useState<string>(into?.id ?? "new");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function choose(files: File[]) {
    setError(null);
    // Every file on its own row: one that cannot be sent (not a photo, over 30 MB) says
    // why and the others go on. Chosen again: added to what is waiting.
    const next = pick(files).map(({ file, problem }, i) => ({
      key: `${Date.now()}-${i}-${file.name}-${file.size}`, file, name: nameFromFile(file.name), pct: 0,
      state: problem ? "error" : "waiting", message: problem ?? undefined,
    }) as Row);
    setRows((now) => [...now.filter((r) => r.state !== "done"), ...next]);
    if (!name) setName(templateNameFrom(next.filter((r) => r.state === "waiting").map((r) => r.name)));
  }

  const update = (key: string, patch: Partial<Row>) => setRows((all) => all.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const sendable = rows.filter((r) => r.state === "waiting");

  async function add() {
    if (!sendable.length || !confirmed) return;
    setBusy(true);
    setError(null);
    const added: string[] = [];
    try {
      const templateId = target === "new" ? (await api.createMockupTemplate(name.trim() || "Template")).id : target;
      for (const row of sendable) {
        update(row.key, { state: "sending", pct: 0 });
        try {
          const photo = await api.addMockupPhoto(templateId, row.file, row.name, confirmed, (pct) => update(row.key, { pct }));
          added.push(photo.id);
          update(row.key, { state: "done", pct: 100 });
        } catch (e: any) {
          update(row.key, { state: "error", message: String(e.message ?? e) });
        }
      }
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
      if (added.length) onAdded(added); // they appear at once, "preparing"; each then opens for its check
    }
  }

  return (
    <section className={into ? "space-y-3" : "card space-y-4 p-5"} data-testid={into ? "add-photos-here" : "add-template"}>
      {!into && (
        <div key="head">
          <h2 className="text-base font-semibold text-slate-900">Add blank photos</h2>
          <p className="mt-1 text-xs text-slate-500">
            <span><span><span>{LIMIT_NOTE}</span> </span></span>
            <span>Choose several at once: the same garment in each colour, or several shots of one product.</span>
          </p>
        </div>
      )}
      <FileDrop onFiles={choose} disabled={busy} testId="template-file"
        label="Drop photos or a folder here"
        hint="Every photo goes into the same template; each is sent with its own progress." />
      {!into && rows.length > 0 && (
        <div key="target" className="grid gap-3 sm:grid-cols-2">
          <label className="block min-w-0">
            <span className="label">Put them in</span>
            <select className="field w-full" value={target} onChange={(e) => setTarget(e.target.value)} disabled={busy} data-testid="template-target">
              <option value="new">A new template</option>
              {templates.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </label>
          {target === "new" && (
            <label key="name" className="block min-w-0">
              <span className="label">Template name</span>
              <input className="field w-full" value={name} maxLength={MAX_NAME} onChange={(e) => setName(e.target.value)}
                disabled={busy} data-testid="template-name" />
            </label>
          )}
        </div>
      )}
      {rows.length > 0 && (
        <ul key="rows" className="space-y-2" data-testid="template-rows">
          {rows.map((r) => (
            <li key={r.key} className="rounded-md border border-slate-200 p-2.5">
              <div className="flex min-w-0 items-center gap-2">
                <input className="field min-w-0 flex-1" value={r.name} maxLength={MAX_NAME} aria-label={`Name for ${r.file.name}`}
                  onChange={(e) => update(r.key, { name: e.target.value })} disabled={busy || r.state !== "waiting"} />
                <span className="w-20 shrink-0 text-right text-xs tabular-nums text-slate-500" translate="no">
                  {r.state === "sending" ? `${r.pct}%` : r.state === "done" ? "Added" : r.state === "error" ? "Not added" : ""}
                </span>
              </div>
              <div className="progress mt-2" aria-hidden>
                <div className="progress-fill" style={{ width: `${r.state === "done" ? 100 : r.pct}%` }} />
              </div>
              <p className="mt-1 min-h-[1rem] truncate text-xs text-slate-500" title={r.file.name} translate="no">
                {r.message ? <span className="text-rose-700">{r.message}</span> : r.file.name}
              </p>
            </li>
          ))}
        </ul>
      )}
      <label className="flex items-start gap-2.5 text-sm text-slate-700 max-sm:min-h-[2.75rem]">
        <input type="checkbox" className="mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300" checked={confirmed}
          onChange={(e) => setConfirmed(e.target.checked)} data-testid="template-rights" />
        <span>{RIGHTS_STATEMENT}</span>
      </label>
      {error && <p key="error" role="alert" className="text-sm text-rose-700">{error}</p>}
      <button type="button" className="btn-primary" onClick={add} disabled={!sendable.length || !confirmed || busy} data-testid="template-add">
        <span translate="no">{busy ? "Uploading…" : sendable.length > 1 ? `Add ${sendable.length} photos` : "Add photo"}</span>
      </button>
    </section>
  );
}

function TemplateCard({
  template,
  first,
  last,
  onMove,
  onReload,
  onChange,
  onOpen,
  allReady,
  onReadySeen,
}: {
  template: MockupTemplate;
  first: boolean;
  last: boolean;
  onMove: (by: -1 | 1) => void;
  onReload: () => void;
  onChange: (template: MockupTemplate) => void;
  onOpen: (queue: string[]) => void;
  /** Its last waiting photo was just confirmed: say what comes next, here, in view. */
  allReady: boolean;
  onReadySeen: () => void;
}) {
  const readyBox = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (allReady) readyBox.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [allReady]);
  const [name, setName] = useState(template.name);
  const [adding, setAdding] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const waiting = waitingForCheck(template);
  const readyPhotos = template.photos.filter((p) => p.status === "ready");
  const confirmedPhotos = readyPhotos.filter((p) => p.confirmed);
  const [sourceId, setSourceId] = useState<string>("");
  const source = confirmedPhotos.find((p) => p.id === sourceId) ?? confirmedPhotos[0];
  const chosen = selected.filter((id) => readyPhotos.some((p) => p.id === id));
  const toggle = (id: string, on: boolean) => setSelected((now) => (on ? [...now, id] : now.filter((x) => x !== id)));

  async function applyToSelected() {
    if (!source) return;
    const targets = chosen.filter((id) => id !== source.id);
    if (!targets.length) return;
    setBusy(true);
    setMessage(null);
    try {
      const placed = await api.copyMockupAreas(source.id, targets, true);
      setNote(appliedLine(placed.length));
      onReload();
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function confirmSelected() {
    if (!chosen.length) return;
    setBusy(true);
    setMessage(null);
    try {
      onChange(await api.confirmMockupPhotos(template.id, chosen));
      setNote(`${chosen.length} ${chosen.length === 1 ? "photo" : "photos"} confirmed.`);
      setSelected([]);
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function resetPlacement() {
    if (!window.confirm(RESET_PLACEMENT)) return;
    try {
      await api.resetMockupPlacement();
      setNote("Your own placement was reset. New photos use the standard placement.");
      onReload();
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }

  async function rename() {
    if (!name.trim() || name === template.name) return;
    try {
      await api.renameMockupTemplate(template.id, name);
      onReload();
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }

  async function remove() {
    if (!window.confirm(`Delete the template “${template.name}” and its ${template.photos.length} photos? Mockups already made from them stay.`)) return;
    try {
      await api.deleteMockupTemplate(template.id);
      onReload();
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }

  async function order(id: string, by: -1 | 1) {
    const ids = template.photos.map((p) => p.id);
    const i = ids.indexOf(id);
    const j = i + by;
    if (j < 0 || j >= ids.length) return;
    [ids[i], ids[j]] = [ids[j], ids[i]];
    try {
      onChange(await api.orderMockupPhotos(template.id, ids));
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }

  async function removePhoto(photo: MockupPhoto) {
    if (!window.confirm(`Remove “${photo.name}” from this template? Mockups already made from it stay.`)) return;
    try {
      await api.deleteMockupPhoto(photo.id);
      onReload();
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }

  return (
    <article className="card space-y-4 p-4" data-testid="template-card">
      <div className="flex flex-wrap items-end gap-2">
        <label className="block min-w-0 flex-1">
          <span className="label">Template</span>
          <input className="field w-full font-medium" value={name} maxLength={MAX_NAME} title={template.name}
            onChange={(e) => setName(e.target.value)} onBlur={rename} data-testid="template-rename" />
        </label>
        <span className="pb-2 text-xs text-slate-500" translate="no">
          <span><span>{template.photos.length}</span> <span>{template.photos.length === 1 ? "photo" : "photos"}</span></span>
        </span>
        {canAddDesigns(template) && (
          <Link key="add-designs" href={addDesignsHref([template.id])} className="btn-primary min-h-11" data-testid="template-add-designs">
            Add designs
          </Link>
        )}
      </div>
      {allReady && (
        <div key="all-ready" ref={readyBox} role="status"
          className="flex flex-wrap items-center gap-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2" data-testid="all-ready">
          <p className="min-w-[11rem] flex-1 text-sm font-medium text-emerald-900">{`${ALL_READY.split(". ")[0]}.`}</p>
          <Link href={addDesignsHref([template.id])} className="btn-primary min-h-11" data-testid="all-ready-add">Add designs</Link>
          <button type="button" className="btn-secondary min-h-11" onClick={onReadySeen}>Later</button>
        </div>
      )}
      {!!template.own_placement && (
        <div key="own-placement" className="flex flex-wrap items-center gap-2 rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900"
          data-testid="template-own-placement">
          <span className="min-w-0 flex-1" translate="no">{ownPlacementLine(template.own_placement)}</span>
          <button type="button" className="tap text-sm font-medium text-sky-800 underline" onClick={resetPlacement}
            data-testid="template-reset-placement">
            Reset
          </button>
        </div>
      )}
      {waiting.length > 0 && (
        <div key="check" role="status" className="flex flex-wrap items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
          data-testid="template-needs-check">
          <span className="min-w-0 flex-1" translate="no">{`${checkLine(waiting.length)}: they are not used for mockups until you confirm them.`}</span>
          <button type="button" className="btn-primary min-h-11" onClick={() => onOpen(waiting)} data-testid="template-check-all">
            <span translate="no">{waiting.length === 1 ? "Check it" : `Check ${waiting.length} photos`}</span>
          </button>
        </div>
      )}
      {readyPhotos.length > 1 && (
        <div key="select" className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-slate-50 p-2" data-testid="template-selection">
          <label className="flex min-h-11 items-center gap-2 px-1 text-sm text-slate-700">
            <input type="checkbox" className="h-5 w-5 rounded border-slate-300" data-testid="template-select-all"
              checked={chosen.length === readyPhotos.length} onChange={(e) => setSelected(e.target.checked ? readyPhotos.map((p) => p.id) : [])} />
            <span>Select all</span>
          </label>
          <span className="text-xs text-slate-500" translate="no">{`${chosen.length} selected`}</span>
          {confirmedPhotos.length > 0 && (
            <label key="source" className="flex min-h-11 min-w-0 items-center gap-1.5 text-xs text-slate-600">
              <span className="shrink-0">Area from</span>
              <select className="field min-w-0 max-w-[12rem] text-xs" value={source?.id ?? ""} onChange={(e) => setSourceId(e.target.value)}
                data-testid="template-apply-source">
                {confirmedPhotos.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
          )}
          <button type="button" className="btn-secondary min-h-11" data-testid="template-apply-selected"
            onClick={applyToSelected} disabled={busy || !source || !chosen.some((id) => id !== source?.id)}
            title={source ? undefined : "Confirm one photo's area first"}>
            Apply this area to selected
          </button>
          <button type="button" className="btn-primary min-h-11" onClick={confirmSelected} disabled={busy || !chosen.length}
            data-testid="template-confirm-selected">
            Confirm selected
          </button>
        </div>
      )}
      {note && <p key="note" role="status" className="text-sm text-slate-700">{note}</p>}
      {template.photos.length > 0 ? (
        <ul key="photos" className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4" data-testid="template-photos">
          {template.photos.map((p, i) => (
            <li key={p.id} className="min-w-0">
              <PhotoTile photo={p} first={i === 0} last={i === template.photos.length - 1}
                selected={selected.includes(p.id)} onSelect={(on) => toggle(p.id, on)}
                onOpen={() => onOpen([p.id])} onMove={(by) => order(p.id, by)} onRemove={() => removePhoto(p)} />
            </li>
          ))}
        </ul>
      ) : (
        <p key="empty" className="text-sm text-slate-500">No photos in this template yet.</p>
      )}
      {adding ? (
        <AddPhotos key="add" templates={[]} into={template} onAdded={() => { setAdding(false); onReload(); }} />
      ) : (
        <button key="add-open" type="button" className="btn-secondary min-h-11" onClick={() => setAdding(true)} data-testid="template-add-photos">
          Add photos
        </button>
      )}
      {message && <p key="message" role="alert" className="text-sm text-rose-700">{message}</p>}
      <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-4">
        <span className="text-xs text-slate-500">Template order:</span>
        <button type="button" className="btn-secondary min-h-11 px-3 text-xs" onClick={() => onMove(-1)} disabled={first}>Earlier</button>
        <button type="button" className="btn-secondary min-h-11 px-3 text-xs" onClick={() => onMove(1)} disabled={last}>Later</button>
        <button type="button" className="btn-secondary ml-auto min-h-11 px-3 text-xs text-rose-700" onClick={remove}>Delete template</button>
      </div>
    </article>
  );
}

/** One photo of a template: its print areas drawn, whether it is confirmed, and its actions. */
function PhotoTile({
  photo,
  first,
  last,
  selected,
  onSelect,
  onOpen,
  onMove,
  onRemove,
}: {
  photo: MockupPhoto;
  first: boolean;
  last: boolean;
  selected: boolean;
  onSelect: (on: boolean) => void;
  onOpen: () => void;
  onMove: (by: -1 | 1) => void;
  onRemove: () => void;
}) {
  const ready = photo.status === "ready" && photo.width && photo.height;
  const aspect = ready ? `${photo.width} / ${photo.height}` : "6 / 7";
  return (
    <div className={`overflow-hidden rounded-lg border ${photo.needs_check ? "border-amber-400 ring-1 ring-amber-300" : "border-slate-200"}`}
      data-testid="template-photo" data-status={photo.status} data-confirmed={photo.confirmed ? "yes" : "no"}>
      {ready ? (
        <button key="open" type="button" className="relative block w-full bg-white" style={{ aspectRatio: aspect }} onClick={onOpen}
          aria-label={`${photo.needs_check ? "Check" : "Edit"} the print area of ${photo.name}`} data-testid="template-photo-open">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={api.mockupPhotoImageUrl(photo.id, 448)} alt="" loading="lazy" className="h-full w-full object-fill" />
          <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 h-full w-full">
            {photo.areas.map((a, i) => (
              <polygon key={i} points={a.print_area.map(([x, y]) => `${x},${y}`).join(" ")}
                className={photo.needs_check ? "fill-amber-400/20 stroke-amber-600" : "fill-brand-500/15 stroke-brand-600"}
                strokeWidth={2} vectorEffect="non-scaling-stroke" />
            ))}
          </svg>
          <span className={`absolute left-1.5 top-1.5 rounded px-1.5 py-0.5 text-xs font-medium ${photo.needs_check ? "bg-amber-100 text-amber-900" : "bg-emerald-100 text-emerald-800"}`}
            translate="no">
            {photo.needs_check ? NEEDS_CHECK : "Confirmed"}
          </span>
        </button>
      ) : (
        <div key="waiting" className="flex w-full items-center justify-center bg-white p-3 text-center text-xs" style={{ aspectRatio: aspect }}
          role="status" data-testid="template-preparing">
          {photo.status === "failed" ? (
            <span key="failed" className="text-rose-700">{photo.error ?? "This photo could not be prepared."}</span>
          ) : (
            <span key="preparing" className="animate-pulse text-slate-500">Preparing… finding the garment&apos;s outline</span>
          )}
        </div>
      )}
      <div className="space-y-1 p-2">
        <label className="flex min-h-11 min-w-0 items-center gap-2">
          <input type="checkbox" className="h-5 w-5 shrink-0 rounded border-slate-300" checked={selected} disabled={!ready}
            onChange={(e) => onSelect(e.target.checked)} aria-label={`Select ${photo.name}`} data-testid="template-photo-select" />
          <span className="min-w-0 truncate text-xs font-medium text-slate-800" title={photo.name} translate="no">{photo.name}</span>
        </label>
        <div className="flex flex-wrap gap-1">
          {ready && (
            <button key="edit" type="button" className={`${photo.needs_check ? "btn-primary" : "btn-secondary"} min-h-11 flex-1 px-2 text-xs`} onClick={onOpen}>
              {photo.needs_check ? "Check" : "Edit"}
            </button>
          )}
          <button type="button" className="btn-secondary min-h-11 min-w-11 px-2 text-xs" onClick={() => onMove(-1)} disabled={first}
            aria-label={`Move ${photo.name} earlier`}>
            Earlier
          </button>
          <button type="button" className="btn-secondary min-h-11 min-w-11 px-2 text-xs" onClick={() => onMove(1)} disabled={last}
            aria-label={`Move ${photo.name} later`}>
            Later
          </button>
          <button type="button" className="btn-secondary min-h-11 min-w-11 px-2 text-xs text-rose-700" onClick={onRemove}
            aria-label={`Remove ${photo.name}`}>
            Remove
          </button>
        </div>
      </div>
    </div>
  );
}
