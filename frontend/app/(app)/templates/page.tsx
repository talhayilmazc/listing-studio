"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { MockupSet, MockupTemplate } from "@/lib/types";
import {
  ACCURACY_NOTE,
  CHECK_NOTE,
  OCCLUSION_LABEL,
  garmentsLine,
  FOLD_RANGE,
  LIMIT_NOTE,
  MAX_NAME,
  RIGHTS_STATEMENT,
  SIZE_RANGE,
  TEMPLATES_INTRO,
  moveCorner,
  nameFromFile,
  percent,
  sizeProblem,
  type Corner,
} from "@/lib/mockups";

/**
 * Mockup templates (backend api/mockups.py): the seller's blank garment photos,
 * alone or in colour sets. A template is saved at once and shows "preparing"
 * while the server finds the garment's outline. Each has a print area (four
 * corners, dragged, tapped or moved with the arrow keys), a size and a fold
 * strength; one template's settings can be given to its whole set. Nothing here
 * calls Etsy or an AI model.
 */
export default function TemplatesPage() {
  const [templates, setTemplates] = useState<MockupTemplate[] | null>(null);
  const [sets, setSets] = useState<MockupSet[]>([]);
  const [error, setError] = useState<string | null>(null);
  const polls = useRef(0);

  const load = useCallback(async () => {
    try {
      const [t, s] = await Promise.all([api.mockupTemplates(), api.mockupSets()]);
      setTemplates(t);
      setSets(s);
      setError(null);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, []);
  useEffect(() => {
    load();
  }, [load]);

  // Templates being prepared: look again until they are ready (or say it is taking long).
  const preparing = (templates ?? []).some((t) => t.status === "preparing");
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

  const replace = (next: MockupTemplate) =>
    setTemplates((all) => (all ?? []).map((x) => (x.id === next.id ? next : x)));

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

  const loose = (templates ?? []).filter((t) => !t.set_id || !sets.some((s) => s.id === t.set_id));
  const card = (t: MockupTemplate, list: MockupTemplate[]) => (
    <li key={t.id}>
      <TemplateCard
        template={t}
        sets={sets}
        first={list[0]?.id === t.id}
        last={list[list.length - 1]?.id === t.id}
        onChange={replace}
        onMove={(by) => move(t.id, by)}
        onReload={load}
      />
    </li>
  );

  return (
    <div className="space-y-8">
      <div className="max-w-2xl space-y-2 text-sm text-slate-500">
        <p>{TEMPLATES_INTRO}</p>
        <p>{ACCURACY_NOTE}</p>
      </div>
      <AddBlanks sets={sets} onAdded={load} />
      {error && <p key="error" role="alert" className="card p-4 text-sm text-rose-700">{error}</p>}
      {preparing && polls.current > 90 && (
        <p key="slow" role="status" className="card p-4 text-sm text-amber-800">
          Some templates are taking longer than usual to prepare. Reload the page in a minute.
        </p>
      )}
      {templates === null ? (
        <div key="loading" className="card h-40 animate-pulse" />
      ) : templates.length === 0 ? (
        <p key="empty" className="card p-6 text-sm text-slate-500">No templates yet. Add a blank garment photo above.</p>
      ) : (
        <div key="lists" className="space-y-8" data-testid="templates">
          {sets.map((s) => {
            const members = (templates ?? []).filter((t) => t.set_id === s.id);
            return (
              <SetSection key={s.id} set={s} members={members} onReload={load}>
                <ol className="space-y-6">{members.map((t) => card(t, members))}</ol>
              </SetSection>
            );
          })}
          {loose.length > 0 && (
            <section key="loose" className="space-y-3">
              {sets.length > 0 && <h2 key="h" className="text-base font-semibold text-slate-900">Templates not in a set</h2>}
              <ol className="space-y-6">{loose.map((t) => card(t, loose))}</ol>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

type Row = { key: string; file: File; name: string; pct: number; state: "waiting" | "sending" | "done" | "error"; message?: string };

function AddBlanks({ sets, onAdded }: { sets: MockupSet[]; onAdded: () => void }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [target, setTarget] = useState<string>("new");
  const [setName, setSetName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  function choose(files: File[]) {
    setError(null);
    setRows(files.map((file, i) => {
      const problem = sizeProblem(file);
      return { key: `${i}-${file.name}-${file.size}`, file, name: nameFromFile(file.name), pct: 0,
               state: problem ? "error" : "waiting", message: problem ?? undefined };
    }));
    if (files.length > 1 && !setName) setSetName(nameFromFile(files[0].name).replace(/\s*\S+$/, "") || "Colours");
  }

  const update = (key: string, patch: Partial<Row>) => setRows((all) => all.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  const sendable = rows.filter((r) => r.state === "waiting");

  async function add() {
    if (!sendable.length || !confirmed) return;
    setBusy(true);
    setError(null);
    try {
      let setId: string | null = null;
      if (rows.length > 1 && target === "new") setId = (await api.createMockupSet(setName.trim() || "Colours")).id;
      else if (target !== "new" && target !== "none") setId = target;
      for (const row of sendable) {
        update(row.key, { state: "sending", pct: 0 });
        try {
          await api.createMockupTemplate(row.file, row.name, confirmed, (pct) => update(row.key, { pct }), setId);
          update(row.key, { state: "done", pct: 100 });
          onAdded(); // it appears at once, "preparing"
        } catch (e: any) {
          update(row.key, { state: "error", message: String(e.message ?? e) });
        }
      }
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  const several = rows.length > 1;
  return (
    <section className="card space-y-4 p-5" data-testid="add-template">
      <div>
        <h2 className="text-base font-semibold text-slate-900">Add blank photos</h2>
        <p className="mt-1 text-xs text-slate-500">
          <span><span><span>{LIMIT_NOTE}</span> </span></span>
          <span>Choose several at once for a colour set (the same garment in each colour).</span>
        </p>
      </div>
      <input
        ref={input}
        type="file"
        multiple
        accept="image/png,image/jpeg,image/webp"
        className="field block w-full min-w-0 text-sm"
        onChange={(e) => choose(Array.from(e.target.files ?? []))}
        disabled={busy}
        data-testid="template-file"
      />
      {several && (
        <div key="set" className="grid gap-3 sm:grid-cols-2">
          <label className="block min-w-0">
            <span className="label">Put them in</span>
            <select className="field w-full" value={target} onChange={(e) => setTarget(e.target.value)} disabled={busy}>
              <option value="new">A new colour set</option>
              {sets.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              <option value="none">No set</option>
            </select>
          </label>
          {target === "new" && (
            <label key="set-name" className="block min-w-0">
              <span className="label">Set name</span>
              <input className="field w-full" value={setName} maxLength={MAX_NAME} onChange={(e) => setSetName(e.target.value)}
                disabled={busy} data-testid="set-name" />
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
        {busy ? "Uploading…" : sendable.length > 1 ? `Add ${sendable.length} templates` : "Add template"}
      </button>
    </section>
  );
}

function SetSection({
  set,
  members,
  onReload,
  children,
}: {
  set: MockupSet;
  members: MockupTemplate[];
  onReload: () => void;
  children: React.ReactNode;
}) {
  const [name, setName] = useState(set.name);
  const [message, setMessage] = useState<string | null>(null);
  async function rename() {
    if (!name.trim() || name === set.name) return;
    try {
      await api.renameMockupSet(set.id, name);
      onReload();
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }
  async function remove() {
    if (!window.confirm(`Delete the set “${set.name}”? Its templates stay, in no set.`)) return;
    try {
      await api.deleteMockupSet(set.id);
      onReload();
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }
  return (
    <section className="space-y-3 rounded-xl border border-slate-200 bg-white/60 p-4" data-testid="mockup-set">
      <div className="flex flex-wrap items-end gap-2">
        <label className="block min-w-0 flex-1">
          <span className="label">Colour set</span>
          <input className="field w-full font-medium" value={name} maxLength={MAX_NAME} onChange={(e) => setName(e.target.value)} onBlur={rename} />
        </label>
        <span className="pb-2 text-xs text-slate-500" translate="no"><span><span>{members.length}</span> <span>{members.length === 1 ? "template" : "templates"}</span></span></span>
        <button type="button" className="btn-secondary px-3 py-1.5 text-xs text-rose-700" onClick={remove}>Delete set</button>
      </div>
      {message && <p key="m" role="alert" className="text-sm text-rose-700">{message}</p>}
      {children}
    </section>
  );
}

function TemplateCard({
  template,
  sets,
  first,
  last,
  onChange,
  onMove,
  onReload,
}: {
  template: MockupTemplate;
  sets: MockupSet[];
  first: boolean;
  last: boolean;
  onChange: (t: MockupTemplate) => void;
  onMove: (by: -1 | 1) => void;
  onReload: () => void;
}) {
  // Several garments in one photo: each has its own print area; the editor works on one.
  const garments = template.garments?.length ? template.garments : template.print_area ? [{ print_area: template.print_area }] : [];
  const [garment, setGarment] = useState(0);
  const areaOf = (t: MockupTemplate, i: number): Corner[] =>
    ((t.garments?.length ? t.garments[i]?.print_area : i === 0 ? t.print_area : null) ?? t.print_area ?? []) as Corner[];
  const [corners, setCorners] = useState<Corner[]>(areaOf(template, 0));
  const [size, setSize] = useState(template.size);
  const [fold, setFold] = useState(template.fold_strength);
  const [name, setName] = useState(template.name);
  const [preview, setPreview] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [applying, setApplying] = useState(false);
  // The found print area is used as it is; the corner editor opens when the
  // analysis was not sure, or when the seller chooses to adjust it.
  const [adjusting, setAdjusting] = useState(false);
  const [setPreviews, setSetPreviews] = useState<{ id: string; name: string; url: string | null; error?: string }[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const saved = useRef(template);

  // The server's copy changed (prepared, or a set's settings applied): follow it.
  useEffect(() => {
    saved.current = template;
    setCorners(areaOf(template, garment));
    setSize(template.size);
    setFold(template.fold_strength);
  }, [template.version, template.status]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  async function save(body: Parameters<typeof api.updateMockupTemplate>[1]) {
    setMessage(null);
    try {
      const next = await api.updateMockupTemplate(template.id, body);
      saved.current = next;
      onChange(next);
      if (preview) showPreview(areaOf(next, garment), next.size, next.fold_strength);
      if ("set_id" in body) onReload();
    } catch (e: any) {
      setCorners(areaOf(saved.current, garment));
      setMessage(String(e.message ?? e));
    }
  }

  async function showPreview(area = corners, s = size, f = fold) {
    setPreviewing(true);
    setMessage(null);
    try {
      const blob = await api.previewMockupTemplate(template.id, { print_area: area, size: s, fold_strength: f, garment });
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

  async function applyToSet() {
    if (!template.set_id) return;
    setApplying(true);
    setMessage(null);
    try {
      const members = await api.applyToMockupSet(template.set_id, template.id);
      members.forEach(onChange);
      // A preview of each, so the seller sees every colour with these settings.
      const shown: typeof setPreviews = members.map((m) => ({ id: m.id, name: m.name, url: null }));
      setSetPreviews(shown);
      for (const m of members) {
        if (m.status !== "ready") {
          setSetPreviews((all) => all.map((p) => (p.id === m.id ? { ...p, error: "Still preparing" } : p)));
          continue;
        }
        try {
          const blob = await api.previewMockupTemplate(m.id, {});
          const url = URL.createObjectURL(blob);
          setSetPreviews((all) => all.map((p) => (p.id === m.id ? { ...p, url } : p)));
        } catch (e: any) {
          setSetPreviews((all) => all.map((p) => (p.id === m.id ? { ...p, error: String(e.message ?? e) } : p)));
        }
      }
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    } finally {
      setApplying(false);
    }
  }

  async function remove() {
    if (!window.confirm(`Delete the template “${template.name}”? Mockups already made from it stay.`)) return;
    try {
      await api.deleteMockupTemplate(template.id);
      onReload();
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    }
  }

  const ready = template.status === "ready" && template.width && template.height;
  const aspect = ready ? `${template.width} / ${template.height}` : "6 / 7";
  return (
    <article className="card overflow-hidden" data-testid="template-card" data-status={template.status}>
      <div className="grid gap-0 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="border-b border-slate-200 bg-slate-50 p-4 md:border-b-0 md:border-r">
          {ready && (template.needs_check || adjusting) ? (
            <PrintAreaEditor
              key="editor"
              src={api.mockupTemplateImageUrl(template.id, 896)}
              aspect={aspect}
              corners={corners}
              others={garments.filter((_, i) => i !== garment).map((g) => g.print_area as Corner[])}
              onMove={setCorners}
              onDone={(next) => save({ print_area: next, garment })}
            />
          ) : ready ? (
            <PrintAreaView key="view" src={api.mockupTemplateImageUrl(template.id, 896)} aspect={aspect}
              areas={garments.map((g) => g.print_area as Corner[])} />
          ) : (
            <div key="waiting" className="flex w-full items-center justify-center rounded-md bg-white p-6 text-center text-sm"
              style={{ aspectRatio: aspect }} role="status" data-testid="template-preparing">
              {template.status === "failed" ? (
                <span className="text-rose-700">{template.error ?? "This photo could not be prepared."}</span>
              ) : (
                <span className="animate-pulse text-slate-500">Preparing… finding the garment&apos;s outline</span>
              )}
            </div>
          )}
          {ready && garments.length > 1 && (
            <div key="garments" className="mt-2 flex flex-wrap items-center gap-1.5 text-xs" data-testid="template-garments">
              <span className="text-slate-600">{garmentsLine(garments.length)}</span>
              {garments.map((g, i) => (
                <button key={i} type="button" aria-pressed={i === garment} data-testid="template-garment"
                  className={`tap rounded-md border px-2 py-1 ${i === garment ? "border-brand-600 bg-brand-50 text-brand-800" : "border-slate-300 text-slate-700"} ${g.needs_check ? "ring-1 ring-amber-400" : ""}`}
                  onClick={() => { setGarment(i); setCorners(g.print_area as Corner[]); }}>
                  <span>{`Garment ${i + 1}`}</span>
                </button>
              ))}
            </div>
          )}
          {ready && (
            <div key="analysis" className="mt-2 space-y-2 text-xs text-slate-600" data-testid="template-analysis">
              {template.needs_check && (
                <p key="check" role="status" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900" data-testid="template-check">
                  <span className="font-medium"><span><span>{CHECK_NOTE}</span> </span></span>
                  <span>{template.analysis_note ?? ""}</span>
                </p>
              )}
              {!template.needs_check && (
                <p key="found" translate="no">
                  {template.print_area_source === "seller" ? "Print area set by you." : template.analysis_note ?? "Print area found automatically."}
                </p>
              )}
              {template.needs_check || adjusting ? (
                <p key="hint" className="text-slate-500">
                  <span>Drag the four corners onto the print area, or tap a corner and use the arrow keys. </span>
                  {adjusting && !template.needs_check && (
                    <button key="done" type="button" className="tap text-brand-700 underline" onClick={() => setAdjusting(false)}>Done</button>
                  )}
                </p>
              ) : (
                <button key="adjust" type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setAdjusting(true)} data-testid="template-adjust">
                  Adjust the print area
                </button>
              )}
            </div>
          )}
        </div>
        <div className="min-w-0 space-y-4 p-4">
          <h3 className="truncate text-base font-semibold text-slate-900" title={template.name} translate="no">{template.name}</h3>
          <label className="block">
            <span className="label">Name</span>
            <input className="field w-full" value={name} maxLength={MAX_NAME} onChange={(e) => setName(e.target.value)}
              onBlur={() => name.trim() && name !== template.name && save({ name })} />
          </label>
          {ready && (
            <label key="view" className="block">
              <span className="label">Shows the</span>
              <select className="field w-full" value={template.view === "back" ? "back" : "front"} data-testid="template-view"
                onChange={(e) => save({ view: e.target.value as "front" | "back" })}>
                <option value="front">Front</option>
                <option value="back">Back</option>
              </select>
            </label>
          )}
          <label className="block">
            <span className="label">Colour set</span>
            <select className="field w-full" value={template.set_id ?? ""} onChange={(e) => save({ set_id: e.target.value || null })}>
              <option value="">No set</option>
              {sets.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          {template.notice && (
            <p key="notice" role="note" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
              {template.notice}
            </p>
          )}
          {ready && (
            <div key="settings" className="space-y-4">
              <Slider label="Design size" hint="Share of the print area the design fills; its proportions are kept, top-aligned."
                value={size} range={SIZE_RANGE} onChange={setSize} onDone={(v) => save({ size: v })} />
              <Slider label="Fold strength" hint="How much the fabric's folds and creases bend the print."
                value={fold} range={FOLD_RANGE} onChange={setFold} onDone={(v) => save({ fold_strength: v })} />
              {template.has_occluder && (
                <label key="occlusion" className="flex items-start gap-2.5 text-sm text-slate-700 max-sm:min-h-[2.75rem]">
                  <input type="checkbox" className="mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300" checked={template.occlusion !== false}
                    onChange={(e) => save({ occlusion: e.target.checked })} data-testid="template-occlusion" />
                  <span>{OCCLUSION_LABEL}</span>
                </label>
              )}
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn-primary" onClick={() => showPreview()} disabled={previewing} data-testid="template-preview">
                  {previewing ? "Drawing…" : preview ? "Preview again" : "Preview with a sample design"}
                </button>
                <button type="button" className="btn-secondary" onClick={() => save({ reset_print_area: true, garment })}>
                  Reset print area
                </button>
                {template.set_id && (
                  <button key="apply" type="button" className="btn-secondary h-auto max-w-full whitespace-normal text-left" onClick={applyToSet} disabled={applying} data-testid="apply-to-set">
                    {applying ? "Applying…" : "Use this print area for the whole set"}
                  </button>
                )}
              </div>
            </div>
          )}
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
          {setPreviews.length > 0 && (
            <div key="set-previews" className="space-y-1" data-testid="set-previews">
              <p className="text-xs font-medium text-slate-700">The whole set with these settings:</p>
              <ul className="grid grid-cols-3 gap-2 sm:grid-cols-4">
                {setPreviews.map((p) => (
                  <li key={p.id} className="min-w-0">
                    <div className="flex aspect-[6/7] items-center justify-center overflow-hidden rounded border border-slate-200 bg-white">
                      {p.url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img key="img" src={p.url} alt={p.name} className="h-full w-full object-contain" />
                      ) : (
                        <span key="state" className="px-1 text-center text-[11px] text-slate-500">{p.error ?? "Drawing…"}</span>
                      )}
                    </div>
                    <p className="mt-0.5 truncate text-[11px] text-slate-600" title={p.name}>{p.name}</p>
                  </li>
                ))}
              </ul>
            </div>
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

/** The photo with its print area, read only (the found area, used as it is). */
function PrintAreaView({ src, aspect, areas }: { src: string; aspect: string; areas: Corner[][] }) {
  return (
    <div className="relative w-full overflow-hidden rounded-md bg-white" style={{ aspectRatio: aspect }} data-testid="print-area-view">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt="" className="h-full w-full object-fill" />
      <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 h-full w-full">
        {areas.map((corners, i) => (
          <polygon key={i} points={corners.map(([x, y]) => `${x},${y}`).join(" ")}
            className="fill-brand-500/15 stroke-brand-600" strokeWidth={0.004} vectorEffect="non-scaling-stroke" />
        ))}
      </svg>
    </div>
  );
}

const CORNER_NAMES = ["Top left", "Top right", "Bottom right", "Bottom left"];

/** The blank with its print area: four handles, by pointer (mouse or finger) or keyboard. */
function PrintAreaEditor({
  src,
  aspect,
  corners,
  others = [],
  onMove,
  onDone,
}: {
  src: string;
  aspect: string;
  corners: Corner[];
  /** The photo's other garments' areas, drawn faintly (not editable here). */
  others?: Corner[][];
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
        {others.map((o, i) => (
          <polygon key={i} points={o.map(([x, y]) => `${x},${y}`).join(" ")} className="fill-none stroke-slate-400"
            strokeWidth={0.003} vectorEffect="non-scaling-stroke" strokeDasharray="4 3" />
        ))}
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
