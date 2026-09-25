"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, uploadArchive, uploadAsset } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";
import { relativeTime } from "@/lib/format";
import type { ArchiveResult, BatchSummary } from "@/lib/types";
import {
  groupKeyOf,
  mergePending,
  removeGroup,
  skipPending,
  stage,
  stageArchives,
  type StagedGroup,
} from "@/lib/staging";
import { smallPreview } from "@/lib/thumbs";

interface Row {
  /** Stable identity: a ZIP's row is replaced by its images once unpacked. */
  key: string;
  name: string;
  pct: number;
  status: "pending" | "uploading" | "done" | "error";
  sku?: string | null;
  group: string;
  assetStatus?: string;
  error?: string;
  /** Local object URL, so a tile appears the moment a file lands. */
  preview?: string;
}

interface Item {
  file: File;
  relpath: string;
}

const IMAGE_RE = /\.(png|jpe?g|webp|gif|tiff?)$/i;
const ZIP_RE = /\.zip$/i;

export default function UploadPage() {
  const router = useRouter();
  // Chosen but not yet sent (staging): folders and ZIPs, each removable.
  const [staged, setStaged] = useState<StagedGroup<File>[]>([]);
  const [archives, setArchives] = useState<File[]>([]);
  // Up to five small previews per staged folder, by folder key.
  const [thumbs, setThumbs] = useState<Record<string, (string | null)[]>>({});
  // Files in chosen folders that are not images or ZIPs, left out.
  const [ignored, setIgnored] = useState(0);
  const [phase, setPhase] = useState<"staging" | "uploading" | "done">("staging");
  // The upload itself: one row per file.
  const [rows, setRows] = useState<Row[]>([]);
  const [dragging, setDragging] = useState(false);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [recent, setRecent] = useState<BatchSummary[] | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const zipRef = useRef<HTMLInputElement>(null);
  // What each ZIP skipped or refused, so nothing disappears without a word (v6 §F).
  const [notes, setNotes] = useState<string[]>([]);
  // Object URLs this page made, by folder key, so removing a folder frees its
  // previews: adding and removing folders over and over must not leak memory.
  const owned = useRef<Map<string, string[]>>(new Map());
  const making = useRef<Set<string>>(new Set());
  const busy = phase === "uploading";

  // The last few uploads, so an empty page is not a single box in a void.
  useEffect(() => {
    let cancelled = false;
    api
      .listBatches()
      .then((b) => !cancelled && setRecent(b.slice(0, 3)))
      .catch(() => setRecent([]));
    return () => {
      cancelled = true;
    };
  }, []);

  const release = useCallback((key: string) => {
    for (const u of owned.current.get(key) ?? []) URL.revokeObjectURL(u);
    owned.current.delete(key);
    making.current.delete(key);
    setThumbs((t) => {
      const next = { ...t };
      delete next[key];
      return next;
    });
  }, []);

  const releaseAll = useCallback(() => {
    for (const urls of owned.current.values()) urls.forEach((u) => URL.revokeObjectURL(u));
    owned.current.clear();
    making.current.clear();
    setThumbs({});
  }, []);

  // Object URLs are owned by this page; release them when it goes away.
  useEffect(() => releaseAll, [releaseAll]);

  // Make the previews each staged folder still lacks (the first five files).
  useEffect(() => {
    for (const g of staged) {
      const want = Math.min(5, g.files.length);
      if ((thumbs[g.key]?.length ?? 0) >= want || making.current.has(g.key)) continue;
      making.current.add(g.key);
      (async () => {
        const have = thumbs[g.key] ?? [];
        const made: (string | null)[] = [...have];
        for (const sf of g.files.slice(have.length, want)) {
          const url = await smallPreview(sf.file);
          if (!making.current.has(g.key)) {
            if (url) URL.revokeObjectURL(url); // removed meanwhile
            return;
          }
          if (url) owned.current.set(g.key, [...(owned.current.get(g.key) ?? []), url]);
          made.push(url);
        }
        making.current.delete(g.key);
        setThumbs((t) => ({ ...t, [g.key]: made }));
      })();
    }
  }, [staged, thumbs]);

  const addItems = useCallback(
    (items: Item[]) => {
      const images = items.filter((it) => IMAGE_RE.test(it.file.name));
      const zips = items.filter((it) => ZIP_RE.test(it.file.name)).map((it) => it.file);
      const others = items.length - images.length - zips.length;
      const incoming = images.map((it) => ({ file: it.file, relpath: it.relpath }));
      if (phase === "done") {
        // The last upload is finished: this starts the next one.
        releaseAll();
        setRows([]);
        setNotes([]);
        setBatchId(null);
        setPhase("staging");
        setStaged(stage([], incoming));
        setArchives(stageArchives([], zips));
        setIgnored(others);
        return;
      }
      // Added to what is already staged, never in place of it.
      setStaged((g) => stage(g, incoming));
      setArchives((a) => stageArchives(a, zips));
      setIgnored((n) => n + others);
    },
    [phase, releaseAll],
  );

  function removeFolder(key: string) {
    release(key);
    setStaged((g) => removeGroup(g, key));
  }

  function clearAll() {
    releaseAll();
    setStaged([]);
    setArchives([]);
    setIgnored(0);
  }

  const undecided = staged.filter((g) => g.pending.length > 0);
  const imageCount = staged.reduce((n, g) => n + g.files.length, 0);

  async function upload() {
    if (undecided.length || (imageCount === 0 && archives.length === 0)) return;
    setPhase("uploading");
    setNotes([]);
    const files = staged.flatMap((g) =>
      g.files.map((sf, i) => ({ sf, key: `f-${g.key}-${i}`, preview: i < 5 ? thumbs[g.key]?.[i] ?? undefined : undefined })),
    );
    setRows([
      ...files.map(({ sf, key, preview }) => ({
        key,
        name: sf.file.name,
        pct: 0,
        status: "pending" as const,
        group: groupKeyOf(sf.relpath) || "(root)",
        preview,
      })),
      // A ZIP shows as one row until the server has unpacked it.
      ...archives.map((z, j) => ({
        key: `z${j}`,
        name: z.name,
        pct: 0,
        status: "pending" as const,
        group: z.name,
      })),
    ]);

    let batch: BatchSummary;
    try {
      batch = await api.createBatch();
    } catch (e: any) {
      setNotes([String(e.message ?? e)]);
      setPhase("staging");
      return;
    }
    setBatchId(batch.id);

    for (const { sf, key } of files) {
      setRows((r) => update(r, key, { status: "uploading" }));
      try {
        const asset = await uploadAsset(
          batch.id,
          sf.file,
          (pct) => setRows((r) => update(r, key, { pct })),
          groupKeyOf(sf.relpath),
        );
        setRows((r) =>
          update(r, key, { status: "done", pct: 100, sku: asset.parsed_sku, assetStatus: asset.status }),
        );
      } catch (e: any) {
        setRows((r) => update(r, key, { status: "error", error: String(e.message ?? e) }));
      }
    }

    for (const [j, zip] of archives.entries()) {
      const key = `z${j}`;
      setRows((r) => update(r, key, { status: "uploading" }));
      try {
        const res = await uploadArchive(batch.id, zip, (pct) =>
          setRows((r) => update(r, key, { pct, name: pct >= 100 ? `${zip.name}: unpacking…` : zip.name })),
        );
        setRows((r) => r.flatMap((row) => (row.key === key ? archiveRows(key, res) : [row])));
        setNotes((n) => [...n, ...archiveNotes(zip.name, res)]);
      } catch (e: any) {
        setRows((r) => update(r, key, { status: "error", name: zip.name, error: String(e.message ?? e) }));
      }
    }

    await api.finalizeBatch(batch.id);
    setStaged([]);
    setArchives([]);
    setPhase("done");
  }

  async function collectFromDrop(dt: DataTransfer): Promise<Item[]> {
    const items = Array.from(dt.items);
    const entries = items
      .map((it) => (it.webkitGetAsEntry ? it.webkitGetAsEntry() : null))
      .filter(Boolean) as FileSystemEntry[];
    if (entries.length > 0) {
      const out: Item[] = [];
      await Promise.all(entries.map((e) => walkEntry(e, out, "")));
      return out;
    }
    return Array.from(dt.files).map((f) => ({ file: f, relpath: f.name }));
  }

  const dropHandlers = {
    onDragOver: (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(true);
    },
    onDragLeave: () => setDragging(false),
    onDrop: async (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      if (busy) return;
      addItems(await collectFromDrop(e.dataTransfer));
    },
  };

  const groups = groupRows(rows);
  const done = rows.filter((r) => r.status === "done").length;
  const failed = rows.filter((r) => r.status === "error").length;
  const empty = phase === "staging" && staged.length === 0 && archives.length === 0;

  const folderInput = (
    <input
      ref={inputRef}
      type="file"
      multiple
      // @ts-expect-error non-standard folder-select attributes
      webkitdirectory=""
      directory=""
      className="hidden"
      onChange={(e) => {
        const files = Array.from(e.target.files ?? []);
        e.target.value = "";
        if (files.length)
          addItems(files.map((f) => ({ file: f, relpath: (f as any).webkitRelativePath || f.name })));
      }}
    />
  );

  const zipInput = (
    <input
      ref={zipRef}
      type="file"
      multiple
      accept=".zip,application/zip"
      className="hidden"
      onChange={(e) => {
        const files = Array.from(e.target.files ?? []);
        e.target.value = "";
        if (files.length) addItems(files.map((f) => ({ file: f, relpath: f.name })));
      }}
    />
  );

  const pickers = (
    <>
      <button type="button" disabled={busy} className="btn-secondary" onClick={() => inputRef.current?.click()}>
        {empty ? "Choose a folder" : "Add folder"}
      </button>
      <button type="button" disabled={busy} className="btn-secondary" onClick={() => zipRef.current?.click()}>
        {empty ? "Choose ZIP files" : "Add ZIP files"}
      </button>
    </>
  );

  // Empty page: the drop zone leads, with recent uploads beneath it.
  if (empty) {
    return (
      <div className="space-y-6">
        <div
          {...dropHandlers}
          className={
            "flex min-h-[30vh] flex-col items-center justify-center rounded-xl border-2 border-dashed p-8 text-center transition-colors " +
            (dragging ? "border-brand-600 bg-brand-50" : "border-slate-300 bg-white")
          }
        >
          <div
            className={
              "flex h-14 w-14 items-center justify-center rounded-2xl transition-colors " +
              (dragging ? "bg-brand-600 text-white" : "bg-brand-50 text-brand-700")
            }
          >
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M12 16V4M12 4l-4 4M12 4l4 4" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2" strokeLinecap="round" />
            </svg>
          </div>
          <h2 className="mt-4 font-display text-3xl text-slate-900">Drop a folder or ZIP of designs</h2>
          <p className="mt-1.5 max-w-md text-sm text-slate-500">
            Each subfolder becomes one listing group, and the SKU is read from the folder or file
            name. PNG, JPG, WebP, GIF and TIFF are accepted. A ZIP keeps its folders; files at its
            top level are one group. Nothing is uploaded until you press Upload.
          </p>
          <div className="mt-5 flex flex-wrap justify-center gap-2">{pickers}</div>
          {folderInput}
          {zipInput}
        </div>

        <RecentUploads batches={recent} />
      </div>
    );
  }

  // Staging: what will be uploaded, each folder removable, nothing sent yet.
  if (phase === "staging") {
    return (
      <div {...dropHandlers} className="space-y-4">
        <div
          className={
            "flex flex-wrap items-center justify-between gap-4 rounded-xl border border-dashed px-5 py-4 transition-colors " +
            (dragging ? "border-brand-600 bg-brand-50" : "border-slate-300 bg-white")
          }
        >
          <div className="min-w-0">
            <p className="text-sm font-medium text-slate-800">Ready to upload</p>
            <p className="mt-0.5 text-xs text-slate-500">
              <span className="tabular-nums">{staged.length}</span> {staged.length === 1 ? "folder" : "folders"} ·{" "}
              <span className="tabular-nums">{imageCount}</span> {imageCount === 1 ? "image" : "images"}
              {archives.length > 0 && (
                <>
                  {" · "}
                  <span className="tabular-nums">{archives.length}</span> ZIP{archives.length === 1 ? "" : "s"}
                </>
              )}
              {ignored > 0 && <> · {ignored} other file{ignored === 1 ? "" : "s"} left out (not images)</>}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {pickers}
            <button type="button" className="text-xs text-slate-500 underline hover:text-slate-800" onClick={clearAll}>
              Clear
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={upload}
              disabled={undecided.length > 0}
              title={undecided.length ? "Merge or skip the folders added twice first" : undefined}
            >
              Upload {staged.length + archives.length} {staged.length + archives.length === 1 ? "item" : "items"}
            </button>
          </div>
          {folderInput}
          {zipInput}
        </div>

        {undecided.length > 0 && (
          <p role="status" className="text-xs text-amber-800">
            {undecided.length === 1 ? "A folder was" : `${undecided.length} folders were`} added twice.
            Merge or skip {undecided.length === 1 ? "it" : "each"} before uploading.
          </p>
        )}

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {staged.map((g) => (
            <StagedCard
              key={g.key}
              group={g}
              previews={thumbs[g.key] ?? []}
              onRemove={() => removeFolder(g.key)}
              onMerge={() => setStaged((s) => mergePending(s, g.key))}
              onSkip={() => setStaged((s) => skipPending(s, g.key))}
            />
          ))}
          {archives.map((z) => (
            <div key={`${z.name}-${z.size}-${z.lastModified}`} className="card relative flex items-center gap-3 p-5">
              <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-medium uppercase text-slate-500">zip</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-slate-800">{z.name}</span>
                <span className="block text-xs text-slate-400">
                  {(z.size / (1024 * 1024)).toFixed(1)} MB · unpacked on upload, its folders kept
                </span>
              </span>
              <RemoveButton label={z.name} onClick={() => setArchives((a) => a.filter((x) => x !== z))} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div {...dropHandlers} className="space-y-4">
      {/* Once files land the zone steps back to a bar, still a drop target. */}
      <div
        className={
          "flex flex-wrap items-center justify-between gap-4 rounded-xl border border-dashed px-5 py-4 transition-colors " +
          (dragging ? "border-brand-600 bg-brand-50" : "border-slate-300 bg-white")
        }
      >
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-800">
            {busy ? "Uploading…" : failed > 0 ? "Finished with errors" : "Upload complete"}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            <span className="tabular-nums">{done}</span> / {rows.length} files ·{" "}
            <span className="tabular-nums">{groups.length}</span>{" "}
            {groups.length === 1 ? "listing group" : "listing groups"}
            {failed > 0 && (
              <span className="text-rose-700">
                {" · "}
                <span className="tabular-nums">{failed}</span> failed
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!busy && pickers}
          {batchId && !busy && (
            <button className="btn-primary" onClick={() => router.push(`/batches/${batchId}`)}>
              Open batch
            </button>
          )}
        </div>
        {folderInput}
        {zipInput}
      </div>

      {notes.length > 0 && (
        <ul role="status" className="card space-y-1 p-3 text-xs text-slate-600">
          {notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}

      {busy && (
        <div className="progress">
          <div
            className="progress-fill"
            style={{ width: (rows.length ? (done / rows.length) * 100 : 0) + "%" }}
          />
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {groups.map((g) => (
          <GroupCard key={g.key} group={g} />
        ))}
      </div>
    </div>
  );
}

function RemoveButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`Remove ${label}`}
      title="Remove from this upload"
      className="absolute right-2 top-2 z-10 flex h-7 w-7 items-center justify-center rounded-full bg-white/90 text-slate-600 shadow-sm ring-1 ring-slate-200 hover:bg-white hover:text-rose-700"
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
        <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
      </svg>
    </button>
  );
}

/** A staged folder: its first images, how many, and × to leave it out. */
function StagedCard({
  group,
  previews,
  onRemove,
  onMerge,
  onSkip,
}: {
  group: StagedGroup<File>;
  previews: (string | null)[];
  onRemove: () => void;
  onMerge: () => void;
  onSkip: () => void;
}) {
  const label = group.key === "" ? "Root folder" : group.key;
  const n = group.files.length;
  return (
    <div className="card relative flex flex-col overflow-hidden">
      <RemoveButton label={label} onClick={onRemove} />
      <Tiles
        tiles={group.files.slice(0, 5).map((sf, i) => ({ name: sf.file.name, preview: previews[i], loading: i >= previews.length }))}
        more={n - Math.min(5, n)}
      />
      <div className="flex flex-col gap-2 p-5">
        <h2 className="truncate font-display text-lg leading-tight text-slate-900" title={group.key}>
          {label}
        </h2>
        <span className="text-xs text-slate-400">
          <span className="tabular-nums">{n}</span> {n === 1 ? "image" : "images"} · SKU read on upload
        </span>
        {group.pending.length > 0 && (
          <div role="alert" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            <p>
              Added again: a folder with this name is already here. The new copy has{" "}
              {group.pending.length} image{group.pending.length === 1 ? "" : "s"} not in it.
            </p>
            <div className="mt-1.5 flex gap-3">
              <button type="button" className="font-medium underline" onClick={onMerge}>
                Merge into this folder
              </button>
              <button type="button" className="underline" onClick={onSkip}>
                Skip the new copy
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

interface Tile {
  name: string;
  preview?: string | null;
  /** The preview is still being made: show the skeleton. */
  loading?: boolean;
  status?: Row["status"];
}

/**
 * Five image slots of fixed size, so the grid's height is known before any image
 * loads, each with a skeleton behind it rather than empty white while it decodes.
 */
function Tiles({ tiles, more }: { tiles: Tile[]; more: number }) {
  return (
    <div className="grid grid-cols-4 grid-rows-2 gap-1 bg-slate-100 p-1" style={{ aspectRatio: "16 / 10" }}>
      {Array.from({ length: 5 }, (_, i) => {
        const t = tiles[i];
        return (
          <div
            key={i}
            className={"relative overflow-hidden rounded-lg bg-slate-100 " + (i === 0 ? "col-span-2 row-span-2" : "")}
          >
            {/* The skeleton sits under the image: seen until it has decoded, never blank. */}
            {t && (t.preview || t.loading) && (
              <span className="absolute inset-0 animate-pulse bg-slate-200" aria-hidden />
            )}
            {t?.preview ? (
              <>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={t.preview}
                  alt={t.name}
                  className={
                    "absolute inset-0 h-full w-full object-cover transition-opacity " +
                    (!t.status || t.status === "done" ? "opacity-100" : "opacity-60")
                  }
                  decoding="async"
                  draggable={false}
                />
                {t.status === "error" && <span className="absolute inset-0 bg-rose-900/30" aria-hidden />}
              </>
            ) : t && !t.loading ? (
              <div className="flex h-full w-full items-center justify-center text-[10px] uppercase tracking-wide text-slate-400">
                {t.name.split(".").pop()}
              </div>
            ) : null}
            {more > 0 && i === 4 && (
              <span className="absolute inset-0 flex items-center justify-center bg-slate-900/55 text-sm font-medium tabular-nums text-white">
                +{more}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

interface DetectedGroup {
  key: string;
  rows: Row[];
  sku: string | null;
}

/** Detected listing groups, in the order their first file arrived. */
function groupRows(rows: Row[]): DetectedGroup[] {
  const byKey = new Map<string, Row[]>();
  for (const r of rows) {
    const list = byKey.get(r.group);
    if (list) list.push(r);
    else byKey.set(r.group, [r]);
  }
  return [...byKey.entries()].map(([key, list]) => ({
    key,
    rows: list,
    sku: list.find((r) => r.sku)?.sku ?? null,
  }));
}

function GroupCard({ group }: { group: DetectedGroup }) {
  const rows = group.rows;
  const done = rows.filter((r) => r.status === "done").length;
  const errors = rows.filter((r) => r.status === "error");
  const pct = rows.length
    ? rows.reduce((n, r) => n + (r.status === "done" ? 100 : r.pct), 0) / rows.length
    : 0;
  const complete = done === rows.length && errors.length === 0;
  const tiles = rows.slice(0, 5);
  const more = rows.length - tiles.length;

  return (
    <div className="card flex flex-col overflow-hidden">
      <Tiles
        tiles={tiles.map((r) => ({ name: r.name, preview: r.preview, status: r.status }))}
        more={more}
      />

      <div className="flex flex-1 flex-col gap-2 p-5">
        <div className="flex items-start justify-between gap-3">
          <h2
            className="min-w-0 flex-1 truncate font-display text-lg leading-tight text-slate-900"
            title={group.key}
          >
            {group.key === "(root)" ? "Root folder" : group.key}
          </h2>
          {complete && <StatusPill status={rows[0]?.assetStatus ?? "uploaded"} />}
        </div>

        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {group.sku ? (
            <span className="rounded-md border border-brand-100 bg-brand-50 px-1.5 py-0.5 font-medium tabular-nums text-brand-700">
              {group.sku}
            </span>
          ) : (
            <span className="rounded-md border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-slate-400">
              no SKU
            </span>
          )}
          <span className="text-slate-400">
            <span className="tabular-nums">{rows.length}</span>{" "}
            {rows.length === 1 ? "image" : "images"}
          </span>
        </div>

        {!complete && (
          <div className="mt-1">
            <div className="progress">
              <div
                className={
                  "h-full rounded-full transition-all " +
                  (errors.length ? "bg-rose-500" : "bg-brand-600")
                }
                style={{ width: pct + "%" }}
              />
            </div>
            <p className="mt-1.5 text-xs tabular-nums text-slate-500">
              {done} / {rows.length} uploaded
            </p>
          </div>
        )}

        {errors.length > 0 && (
          <ul className="mt-auto space-y-0.5 pt-1 text-xs text-rose-700">
            {errors.slice(0, 3).map((r, i) => (
              <li key={i} className="truncate" title={r.error}>
                {r.name}: {r.error}
              </li>
            ))}
            {errors.length > 3 && <li>+{errors.length - 3} more failed</li>}
          </ul>
        )}
      </div>
    </div>
  );
}

function update(rows: Row[], key: string, patch: Partial<Row>): Row[] {
  return rows.map((r) => (r.key === key ? { ...r, ...patch } : r));
}

/** A ZIP's images as rows, grouped by the folder each came from. */
function archiveRows(key: string, res: ArchiveResult): Row[] {
  return res.assets.map((a) => ({
    key: `${key}-${a.id}`,
    name: a.original_filename,
    pct: 100,
    status: "done" as const,
    sku: a.parsed_sku,
    group: a.group_key || "(root)",
    assetStatus: a.status,
    preview: a.status === "processed" ? api.assetImage(a.id, 224) : undefined,
  }));
}

/** One line on what a ZIP held, then one per file it could not take. */
function archiveNotes(name: string, res: ArchiveResult): string[] {
  const groups = new Set(res.assets.map((a) => a.group_key ?? "")).size;
  const skipped: string[] = [];
  const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
  if (res.skipped_unsupported)
    skipped.push(plural(res.skipped_unsupported, "file that is not an image", "files that are not images"));
  if (res.skipped_nested)
    skipped.push(plural(res.skipped_nested, "ZIP inside it (not opened)", "ZIPs inside it (not opened)"));
  if (res.skipped_unsafe)
    skipped.push(plural(res.skipped_unsafe, "file with an unsafe path", "files with unsafe paths"));
  const head =
    `${name}: ${plural(res.assets.length, "image", "images")} in ${plural(groups, "group", "groups")}` +
    (skipped.length ? `; skipped ${skipped.join(", ")}.` : ".");
  return [head, ...res.failed.map((f) => `${name} › ${f.filename}: ${f.error}`)];
}

async function walkEntry(entry: FileSystemEntry, out: Item[], prefix: string): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    );
    out.push({ file, relpath: prefix + entry.name });
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    const entries = await new Promise<FileSystemEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject),
    );
    await Promise.all(entries.map((e) => walkEntry(e, out, prefix + entry.name + "/")));
  }
}

/** The last three uploads, as compact rows. */
function RecentUploads({ batches }: { batches: BatchSummary[] | null }) {
  if (batches !== null && batches.length === 0) return null;
  return (
    <section>
      <div className="flex items-baseline justify-between border-b border-slate-200 pb-2">
        <h2 className="font-display text-lg text-slate-900">Recent uploads</h2>
        <Link href="/" className="text-xs font-medium text-brand-700 hover:text-brand-800">
          All batches
        </Link>
      </div>
      <div className="divide-y divide-slate-100">
        {batches === null
          ? [0, 1, 2].map((i) => (
              <div key={i} className="flex items-center gap-3 py-3">
                <div className="h-4 w-32 animate-pulse rounded bg-slate-100" />
                <div className="h-3 w-20 animate-pulse rounded bg-slate-100" />
              </div>
            ))
          : batches.map((b) => (
              <Link
                key={b.id}
                href={"/batches/" + b.id}
                className="group flex items-center justify-between gap-4 py-3"
              >
                <span className="flex min-w-0 items-center gap-2.5">
                  <StatusPill status={b.status} />
                  <span className="truncate text-sm text-slate-700 group-hover:text-brand-700">
                    Batch {b.id.slice(0, 8)}
                  </span>
                </span>
                <span className="shrink-0 text-xs text-slate-400">
                  <span className="tabular-nums">{b.asset_count}</span> files ·{" "}
                  <span title={new Date(b.created_at).toLocaleString()}>
                    {relativeTime(b.created_at)}
                  </span>
                </span>
              </Link>
            ))}
      </div>
    </section>
  );
}
