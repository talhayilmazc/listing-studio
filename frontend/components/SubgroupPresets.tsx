"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { MockupSubgroup, MockupTemplate, SubgroupPreset } from "@/lib/types";
import { USE_ON_LABELS } from "@/lib/mockups";
import { prefixedExample } from "@/lib/presets";

/**
 * The account's subgroup presets (backend api/mockups.py; seller feedback Part C): subgroups
 * defined once ("White design", suffix "-white", use on dark garments) that any template takes
 * in one click ("Assign subgroups to this template"); a template's own subgroup can be kept as
 * one ("Save as preset"). Upload pairing still reads the suffixes from each template's subgroups.
 */
export function PresetsManager({ presets, onChange }: { presets: SubgroupPreset[]; onChange: (p: SubgroupPreset[]) => void }) {
  const [name, setName] = useState("");
  const [useOn, setUseOn] = useState<"any" | "dark" | "light">("any");
  const [suffix, setSuffix] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function add() {
    if (!name.trim()) return;
    setError(null);
    try {
      const made = await api.addSubgroupPreset({ name: name.trim(), use_on: useOn, suffix: suffix.trim() || null });
      onChange([...presets, made]);
      setName("");
      setSuffix("");
      setUseOn("any");
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }
  async function remove(p: SubgroupPreset) {
    if (!window.confirm(`Remove the preset “${p.name}”? Templates that use it keep their own subgroups.`)) return;
    try {
      await api.deleteSubgroupPreset(p.id);
      onChange(presets.filter((x) => x.id !== p.id));
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }
  return (
    <details className="card" data-testid="subgroup-presets">
      <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-4 text-sm font-medium text-slate-800">
        <span>Subgroup presets</span>
        <span className="text-xs font-normal text-slate-500" translate="no">{presets.length ? `${presets.length}` : "none yet"}</span>
      </summary>
      <div className="space-y-3 border-t border-slate-200 p-4">
        <p className="text-xs text-slate-500">
          Define your subgroups once, for example “White design” with the suffix -white for dark garments. Any template takes them in one click.
        </p>
        {presets.length > 0 && (
          <ul key="list" className="space-y-1.5">
            {presets.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-2 text-sm" data-testid="subgroup-preset">
                <span className="min-w-0 flex-1 truncate font-medium text-slate-800" title={p.name}>{p.name}</span>
                <span className="text-xs text-slate-500" translate="no">
                  {[USE_ON_LABELS[p.use_on as keyof typeof USE_ON_LABELS] ?? "", p.suffix ? `suffix ${p.suffix}` : ""].filter(Boolean).join(" · ")}
                </span>
                <button type="button" className="btn-secondary min-h-11 px-3 text-xs text-rose-700" onClick={() => remove(p)}>Remove</button>
              </li>
            ))}
          </ul>
        )}
        <div className="grid gap-2 sm:grid-cols-[1fr_11rem_8rem_auto]">
          <input className="field min-w-0" value={name} maxLength={120} placeholder="Preset name, for example White design"
            aria-label="Preset name" onChange={(e) => setName(e.target.value)} data-testid="preset-new-name" />
          <select className="field min-w-0" value={useOn} onChange={(e) => setUseOn(e.target.value as "any" | "dark" | "light")} aria-label="Use on">
            {Object.entries(USE_ON_LABELS).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
          <input className="field min-w-0" value={suffix} maxLength={40} placeholder="Suffix: -white" aria-label="File name suffix"
            onChange={(e) => setSuffix(e.target.value)} data-testid="preset-new-suffix" />
          <button type="button" className="btn-secondary min-h-11" onClick={add} disabled={!name.trim()} data-testid="preset-add">Add preset</button>
        </div>
        {error && <p key="error" role="alert" className="text-xs text-rose-700">{error}</p>}
      </div>
    </details>
  );
}

/** "Assign subgroups to this template": tick presets, one click makes them its subgroups. */
export function AssignPresets({ template, onChange }: { template: MockupTemplate; onChange: (t: MockupTemplate) => void }) {
  const [presets, setPresets] = useState<SubgroupPreset[] | null>(null);
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.subgroupPresets().then((all) => {
      setPresets(all);
      setPicked(all.filter((p) => !(template.subgroups ?? []).some((g) => g.name.toLowerCase() === p.name.toLowerCase())).map((p) => p.id));
    }).catch(() => setPresets([]));
  }, [template.id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!presets?.length) return null;
  async function assign() {
    setError(null);
    try {
      onChange(await api.assignSubgroupPresets(template.id, picked));
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }
  return (
    <div className="space-y-2 rounded-md bg-slate-50 p-2.5" data-testid="assign-presets">
      <p className="text-xs font-medium text-slate-700">Assign subgroups to this template</p>
      <div className="flex flex-wrap gap-x-3">
        {presets.map((p) => (
          <label key={p.id} className="flex min-h-11 items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" className="h-4 w-4 rounded border-slate-300" checked={picked.includes(p.id)}
              onChange={(e) => setPicked((all) => (e.target.checked ? [...all, p.id] : all.filter((x) => x !== p.id)))} data-testid="assign-preset" />
            <span translate="no">{p.name}</span>
          </label>
        ))}
      </div>
      <button type="button" className="btn-secondary min-h-11" onClick={assign} disabled={!picked.length} data-testid="assign-presets-go">
        <span translate="no">{`Assign ${picked.length} ${picked.length === 1 ? "subgroup" : "subgroups"}`}</span>
      </button>
      {error && <p key="error" role="alert" className="text-xs text-rose-700">{error}</p>}
    </div>
  );
}

/** "Save as preset" on a template's subgroup. */
export function SaveAsPreset({ group }: { group: MockupSubgroup }) {
  const [said, setSaid] = useState<string | null>(null);
  async function save() {
    try {
      await api.saveSubgroupAsPreset(group.id);
      setSaid("Saved as a preset");
    } catch (e: any) {
      setSaid(String(e.message ?? e));
    }
  }
  return (
    <button type="button" className="btn-secondary min-h-11 px-3 text-xs" onClick={save} title={said ?? undefined}
      aria-label={`Save ${group.name} as a preset`} data-testid="subgroup-save-preset">
      <span translate="no">{said ?? "Save as preset"}</span>
    </button>
  );
}

/** The template's file-name prefix: "CC" names its mockups "CC-BR100-01.jpg"; empty keeps today's names. */
export function FilePrefixField({ template, onChange }: { template: MockupTemplate; onChange: (t: MockupTemplate) => void }) {
  const [value, setValue] = useState(template.file_prefix ?? "");
  const [error, setError] = useState<string | null>(null);
  async function save() {
    if (value.trim() === (template.file_prefix ?? "")) return;
    setError(null);
    try {
      const next = await api.updateMockupTemplate(template.id, { file_prefix: value.trim() });
      setValue(next.file_prefix ?? "");
      onChange(next);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }
  return (
    <label className="block min-w-0" data-testid="template-file-prefix">
      <span className="label">File name prefix</span>
      <input className="field w-full" value={value} maxLength={20} placeholder="none (named after SKU, template and photo)"
        onChange={(e) => setValue(e.target.value)} onBlur={save} aria-describedby={`prefix-${template.id}`} data-testid="template-file-prefix-input" />
      <span id={`prefix-${template.id}`} className="mt-1 block text-xs text-slate-500" translate="no">
        {value.trim() ? `Mockups are named ${prefixedExample(value)}` : "Mockups are named <SKU>_<template>_<photo>.jpg"}
      </span>
      {error && <span key="error" role="alert" className="mt-1 block text-xs text-rose-700">{error}</span>}
    </label>
  );
}
