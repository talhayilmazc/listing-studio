"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { DISK_COLORS, percent, segments, size } from "@/lib/disk";
import type { AdminDisk } from "@/lib/types";

import { Txt } from "@/components/Txt";
/**
 * What is using the server's disk, and the cleanup that keeps it from filling.
 * Admin only; sizes and counts, never a file or a seller.
 *
 * The disk cannot grow, so this is the number to watch: one bar for the whole
 * disk (each category, then free space), the same figures as a table, and what
 * the daily upload cleanup last freed.
 */

/** Istanbul time, whatever the browser's zone. */
const when = (iso: string) =>
  new Date(iso).toLocaleString("en-GB", { timeZone: "Europe/Istanbul", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
const count = (n: number, word: string) => `${n.toLocaleString("en-US")} ${word}${n === 1 ? "" : "s"}`;

export function DiskPanel({ disk, onChanged, onError }: { disk: AdminDisk | null; onChanged: () => void; onError: (m: string) => void }) {
  const [hover, setHover] = useState<string | null>(null);
  if (!disk) return <p className="text-sm text-slate-500">Loading disk usage…</p>;

  const parts = segments(disk);
  const total = disk.total_bytes;
  const used = total !== null && disk.free_bytes !== null ? total - disk.free_bytes : null;
  const shown = parts.find((p) => p.key === hover) ?? null;
  const run = disk.last_run;

  return (
    <section className="card p-5" translate="no" aria-labelledby="disk-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="disk-title" className="font-display text-2xl text-slate-900">Disk</h2>
        <p className="text-xs text-slate-500">
          <span>The server&apos;s one disk · measured <span>{when(disk.as_of)}</span> Istanbul time · sizes only</span>
        </p>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-4">
        <Stat label="Free" value={size(disk.free_bytes)} note={total !== null ? `of ${size(total)}` : "Total size unknown"} />
        <Stat
          label="Used"
          value={used !== null && total ? percent(used / total) : "—"}
          note={used !== null ? size(used) : "Not measured"}
        />
        <Stat
          label="Uploads and derivatives"
          value={size(sum(disk, ["uploads", "derivatives"]))}
          note="What the upload cleanup works on"
        />
        <Stat
          label={run && !run.applied ? "Last cleanup would free" : "Last cleanup freed"}
          value={run ? size(run.freed_bytes) : "—"}
          note={run ? when(run.at) : "The daily cleanup has not run yet"}
        />
      </dl>

      {parts.length > 0 ? (
        <div key="bar" className="mt-5">
          {/* One bar is the whole disk. Segments are separated by the surface, not outlined. */}
          <div className="flex h-5 w-full gap-0.5 overflow-hidden rounded" role="group" aria-label="Disk usage by category">
            {parts.map((p) => (
              <button
                key={p.key}
                type="button"
                className={"h-full min-w-[3px] outline-none transition-opacity focus-visible:ring-2 focus-visible:ring-brand-500 " + (hover && hover !== p.key ? "opacity-50" : "")}
                style={{ width: `${p.share * 100}%`, background: p.color }}
                onPointerEnter={() => setHover(p.key)}
                onPointerLeave={() => setHover(null)}
                onFocus={() => setHover(p.key)}
                onBlur={() => setHover(null)}
                onClick={() => setHover((cur) => (cur === p.key ? null : p.key))}
                aria-label={`${p.label}: ${size(p.bytes)}, ${percent(p.share)} of the disk`}
              />
            ))}
          </div>
          <p className="mt-1.5 min-h-[1.25rem] text-xs text-slate-600" role="status">
            {shown ? (
              <span key="shown">
                <span className="font-medium text-slate-900">{size(shown.bytes)}</span>
                <span><span> · <span>{shown.label}</span> · <span>{percent(shown.share)}</span> of the disk</span></span>
              </span>
            ) : (
              <span key="hint" className="text-slate-500">Point at or tap a part of the bar to read it. The pale end is free space.</span>
            )}
          </p>
        </div>
      ) : (
        <p key="nobar" className="mt-5 text-sm text-slate-500">The disk&apos;s size could not be read.</p>
      )}

      <div className="mt-3 overflow-x-auto">
        <table className="text-left text-xs max-sm:w-max sm:w-full">
          <thead>
            <tr className="border-b border-slate-200 text-slate-500">
              <th className="py-1.5 pr-4 font-medium">Category</th>
              <th className="whitespace-nowrap px-3 py-1.5 text-right font-medium">Size</th>
              <th className="whitespace-nowrap px-3 py-1.5 text-right font-medium">Of the disk</th>
              <th className="whitespace-nowrap px-3 py-1.5 text-right font-medium">Files</th>
              <th className="py-1.5 pl-4 font-medium max-sm:hidden">What it is</th>
            </tr>
          </thead>
          <tbody>
            {disk.categories.map((c) => (
              <tr key={c.key} className="border-b border-slate-100">
                <td className="py-1.5 pr-4 text-slate-800">
                  <span className="flex items-center gap-2 whitespace-nowrap">
                    <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: DISK_COLORS[c.key] }} aria-hidden />
                    <span>{c.label}</span>
                  </span>
                </td>
                <td className={"px-3 py-1.5 text-right tabular-nums " + (c.bytes === null ? "text-slate-400" : "font-medium text-slate-900")}>
                  {c.bytes === null ? "not measured" : size(c.bytes)}
                </td>
                <td className="px-3 py-1.5 text-right tabular-nums text-slate-700">{c.bytes !== null && total ? percent(c.bytes / total) : "—"}</td>
                <td className="px-3 py-1.5 text-right tabular-nums text-slate-700">{c.files === null ? "—" : c.files.toLocaleString("en-US")}</td>
                <td className="py-1.5 pl-4 text-slate-500 max-sm:hidden">{c.note}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="font-medium text-slate-900">
              <td className="py-1.5 pr-4">Free</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{size(disk.free_bytes)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums">{disk.free_bytes !== null && total ? percent(disk.free_bytes / total) : "—"}</td>
              <td />
              <td className="max-sm:hidden" />
            </tr>
          </tfoot>
        </table>
      </div>

      <p className={"mt-3 text-xs " + (disk.host_fresh ? "text-slate-500" : "rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-amber-800")}>
        {disk.host_fresh && disk.host_reported_at ? (
          <span key="fresh">Backups and Docker are measured on the server by its hourly disk check, last at <span>{when(disk.host_reported_at)}</span>.</span>
        ) : disk.host_reported_at ? (
          <span key="stale">
            Backups and Docker were last measured at <span>{when(disk.host_reported_at)}</span>: the server&apos;s hourly disk check
            (deploy/disk-check.sh, from cron) has not reported since. The figures shown for them are that old.
          </span>
        ) : (
          <span key="never">
            Backups and Docker are not measured yet. The server&apos;s hourly disk check (deploy/disk-check.sh, from cron) measures
            them and has not reported; it does within an hour of the update that added this.
          </span>
        )}
      </p>

      <Cleanup disk={disk} onChanged={onChanged} onError={onError} />
    </section>
  );
}

function sum(disk: AdminDisk, keys: string[]): number | null {
  const found = disk.categories.filter((c) => keys.includes(c.key));
  return found.some((c) => c.bytes === null) ? null : found.reduce((n, c) => n + (c.bytes ?? 0), 0);
}

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-500">{label}</dt>
      <dd className="mt-1 font-display text-3xl leading-none tabular-nums text-slate-900">{value}</dd>
      <p className="mt-1 text-[11px] text-slate-500">{note}</p>
    </div>
  );
}

/** The upload cleanup: how long files are kept, and what the last run did. */
function Cleanup({ disk, onChanged, onError }: { disk: AdminDisk; onChanged: () => void; onError: (m: string) => void }) {
  const [published, setPublished] = useState(String(disk.retention.published_days));
  const [unpublished, setUnpublished] = useState(String(disk.retention.unpublished_days));
  const [busy, setBusy] = useState(false);
  // Follow the server unless the admin is in the middle of typing a change.
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (touched) return;
    setPublished(String(disk.retention.published_days));
    setUnpublished(String(disk.retention.unpublished_days));
  }, [disk.retention.published_days, disk.retention.unpublished_days, touched]);

  const run = disk.last_run;
  const changed = Number(published) !== disk.retention.published_days || Number(unpublished) !== disk.retention.unpublished_days;
  const shorter = Number(published) < disk.retention.published_days || Number(unpublished) < disk.retention.unpublished_days;
  const defaults = disk.retention_defaults;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.admin.setUploadRetention({ published_days: Number(published), unpublished_days: Number(unpublished) });
      setTouched(false);
      onChanged();
    } catch (err: any) {
      onError(String(err.message ?? err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-5 border-t border-slate-200 pt-4">
      <h3 className="text-sm font-medium text-slate-900">Upload cleanup</h3>
      <p className="mt-1 max-w-3xl text-xs text-slate-600">
        Once a day, a listing&apos;s image files (the uploads, their processed copies and previews) are deleted after the time below.
        The listing&apos;s text, its publication record and a small cover thumbnail stay, and nothing on Etsy changes. Deleted
        files do not come back: a seller who needs them again uploads the design again.
      </p>
      {!disk.retention_applies && (
        <p key="dry" className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          This server only counts what it would delete (UPLOAD_RETENTION_APPLY is false): nothing is deleted.
        </p>
      )}

      <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={save}>
        <label className="block">
          <span className="block text-[11px] text-slate-500">Days kept after a listing is published</span>
          <input
            className="field mt-0.5 w-28 tabular-nums"
            type="number" min={1} max={3650} required inputMode="numeric"
            value={published}
            onChange={(e) => { setTouched(true); setPublished(e.target.value); }}
          />
        </label>
        <label className="block">
          <span className="block text-[11px] text-slate-500">Days kept when nothing was published</span>
          <input
            className="field mt-0.5 w-28 tabular-nums"
            type="number" min={1} max={3650} required inputMode="numeric"
            value={unpublished}
            onChange={(e) => { setTouched(true); setUnpublished(e.target.value); }}
          />
        </label>
        <button type="submit" className="btn-primary" disabled={busy || !changed}>Save days</button>
      </form>
      <p className="mt-2 max-w-3xl text-[11px] text-slate-500">
        <span>
          <span>Defaults: <span>{defaults.published_days}</span> and <span>{defaults.unpublished_days}</span> days. The second counts from when a group was last worked on
          (uploaded, written, or a draft created). The Privacy Policy states <span>{defaults.published_days}</span> and <span>{defaults.unpublished_days}</span> days:
          change its text if you change these.</span>
        </span>
      </p>
      {changed && shorter && (
        <p key="shorter" className="mt-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          A shorter time deletes more files at the next daily run, and they cannot be restored.
        </p>
      )}

      <h4 className="mt-4 text-xs font-medium text-slate-700">Last run</h4>
      {run ? (
        <p key="run" className="mt-1 text-xs text-slate-600">
          <span>
            <span><span>{when(run.at)}</span>: <span>{run.applied ? "freed" : "would free (dry run)"}</span> </span><span className="font-medium text-slate-900">{size(run.freed_bytes)}</span>
            <span>{" "}<span>in </span><span>{count(run.files, "file")}</span><span> of </span><span>{count(run.published_groups + run.unpublished_groups, "listing")}</span><span> (</span><span>{run.published_groups.toLocaleString("en-US")}</span><span> published,</span>{" "}
            <span>{run.unpublished_groups.toLocaleString("en-US")}</span><span> never published).</span>
            <Txt>{run.applied ? ` Kept ${count(run.thumbnails, "cover thumbnail")} (${size(run.thumbnail_bytes)}).` : ""}</Txt>
            <Txt>{run.waiting > 0 ? ` ${count(run.waiting, "more listing")} waited for running work to finish.` : ""}</Txt></span>
          </span>
        </p>
      ) : (
        <p key="norun" className="mt-1 text-xs text-slate-500">Not run yet. It runs daily at 01:15 UTC (04:15 Istanbul) and its result is in the daily summary.</p>
      )}
    </div>
  );
}
