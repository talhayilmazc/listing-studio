"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import {
  DEFAULT_OPTIONS, FORMAT_LABELS, LAYOUT_LABELS, NAMING_LABELS, QUALITY_LABELS, SAME_AS_ETSY, SIZE_LABELS, SIZE_NOTE,
} from "@/lib/mockupDownloads";
import type { DownloadOptions } from "@/lib/types";

/** The download options, as the seller left them last time (saved on the account as they change). */
export function useDownloadOptions(): [DownloadOptions, (next: DownloadOptions) => void] {
  const [options, setOptions] = useState<DownloadOptions>(DEFAULT_OPTIONS);
  useEffect(() => {
    api.mockupDownloadOptions().then(setOptions).catch(() => {});
  }, []);
  function change(next: DownloadOptions) {
    setOptions(next);
    api.saveMockupDownloadOptions(next).catch(() => {}); // remembered next time; the download itself does not wait
  }
  return [options, change];
}

/** Format, size, quality, file names and ZIP folders: one compact row of choices. */
export function MockupDownloadOptions({ options, onChange }: { options: DownloadOptions; onChange: (next: DownloadOptions) => void }) {
  const [open, setOpen] = useState(false);
  const pick = <K extends keyof DownloadOptions>(key: K, value: DownloadOptions[K]) => onChange({ ...options, [key]: value });
  return (
    <details className="rounded-md border border-slate-200 p-2.5 text-sm" open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
      data-testid="download-options">
      <summary className="tap cursor-pointer font-medium text-slate-800">
        <span>Download options: </span>
        <span translate="no" className="text-slate-600">{summaryOf(options)}</span>
      </summary>
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Choice label="Format" value={options.format} items={FORMAT_LABELS} onChange={(v) => pick("format", v)} testid="download-format" />
        <Choice label="Size" value={options.size} items={SIZE_LABELS} onChange={(v) => pick("size", v)} testid="download-size" />
        <Choice label="Quality" value={String(options.quality)} items={QUALITY_LABELS.map(([v, l]) => [String(v), l] as [string, string])}
          onChange={(v) => pick("quality", Number(v) as DownloadOptions["quality"])} testid="download-quality"
          disabled={options.format === "png"} />
        <Choice label="File names" value={options.naming} items={NAMING_LABELS} onChange={(v) => pick("naming", v)} testid="download-naming" />
        <Choice label="ZIP" value={options.layout} items={LAYOUT_LABELS} onChange={(v) => pick("layout", v)} testid="download-layout" />
      </div>
      <p className="mt-2 text-xs text-slate-600">{SIZE_NOTE}</p>
      <p className="mt-1 text-xs text-slate-600">{SAME_AS_ETSY}</p>
    </details>
  );
}

function summaryOf(o: DownloadOptions): string {
  const size = SIZE_LABELS.find(([v]) => v === o.size)?.[1] ?? o.size;
  const format = FORMAT_LABELS.find(([v]) => v === o.format)?.[1] ?? o.format;
  const quality = o.format === "png" ? "" : `, ${o.quality === 92 ? "high" : "smaller"}`;
  return `${format}, ${size.toLowerCase()}${quality}`;
}

function Choice<V extends string>({ label, value, items, onChange, testid, disabled = false }: {
  label: string; value: V; items: [V, string][]; onChange: (v: V) => void; testid: string; disabled?: boolean;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-xs text-slate-700">
      <span className="font-medium">{label}</span>
      <select className="field min-w-0 text-sm" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as V)} data-testid={testid}>
        {items.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}
