"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, uploadAsset } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";

interface Row {
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
// Formats a browser will not paint, so the tile shows a placeholder instead.
const UNPREVIEWABLE_RE = /\.tiff?$/i;

/** The folder (listing group) a file belongs to; "" for root-level files (D1). */
function groupKeyOf(relpath: string): string {
  const i = relpath.lastIndexOf("/");
  return i >= 0 ? relpath.slice(0, i) : "";
}

export default function UploadPage() {
  const router = useRouter();
  const [rows, setRows] = useState<Row[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [batchId, setBatchId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const previews = useRef<string[]>([]);

  // Object URLs are owned by this page; release them when it goes away.
  useEffect(() => {
    const held = previews.current;
    return () => held.forEach((u) => URL.revokeObjectURL(u));
  }, []);

  const runUpload = useCallback(
    async (items: Item[]) => {
      const images = items.filter((it) => IMAGE_RE.test(it.file.name));
      if (images.length === 0) {
        setRows([
          {
            name: "No image files found in that folder.",
            pct: 0,
            status: "error",
            group: "",
          },
        ]);
        return;
      }
      setBusy(true);
      previews.current.forEach((u) => URL.revokeObjectURL(u));
      previews.current = [];

      setRows(
        images.map((it) => {
          let preview: string | undefined;
          if (!UNPREVIEWABLE_RE.test(it.file.name)) {
            preview = URL.createObjectURL(it.file);
            previews.current.push(preview);
          }
          return {
            name: it.file.name,
            pct: 0,
            status: "pending" as const,
            group: groupKeyOf(it.relpath) || "(root)",
            preview,
          };
        }),
      );

      const batch = await api.createBatch();
      setBatchId(batch.id);

      for (let i = 0; i < images.length; i++) {
        setRows((r) => update(r, i, { status: "uploading" }));
        try {
          const asset = await uploadAsset(
            batch.id,
            images[i].file,
            (pct) => setRows((r) => update(r, i, { pct })),
            groupKeyOf(images[i].relpath),
          );
          setRows((r) =>
            update(r, i, {
              status: "done",
              pct: 100,
              sku: asset.parsed_sku,
              assetStatus: asset.status,
            }),
          );
        } catch (e: any) {
          setRows((r) => update(r, i, { status: "error", error: String(e.message ?? e) }));
        }
      }

      await api.finalizeBatch(batch.id);
      setBusy(false);
    },
    [],
  );

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
      runUpload(await collectFromDrop(e.dataTransfer));
    },
  };

  const groups = groupRows(rows);
  const done = rows.filter((r) => r.status === "done").length;
  const failed = rows.filter((r) => r.status === "error").length;
  const empty = rows.length === 0;

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
          runUpload(
            files.map((f) => ({
              file: f,
              relpath: (f as any).webkitRelativePath || f.name,
            })),
          );
      }}
    />
  );

  // Empty page: the drop zone is the hero and fills it.
  if (empty) {
    return (
      <div
        {...dropHandlers}
        className={
          "flex min-h-[60vh] flex-col items-center justify-center rounded-xl border-2 border-dashed p-12 text-center transition-colors " +
          (dragging ? "border-brand-600 bg-brand-50" : "border-slate-300 bg-white")
        }
      >
        <div
          className={
            "flex h-16 w-16 items-center justify-center rounded-2xl transition-colors " +
            (dragging ? "bg-brand-600 text-white" : "bg-brand-50 text-brand-700")
          }
        >
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 16V4M12 4l-4 4M12 4l4 4" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2" strokeLinecap="round" />
          </svg>
        </div>
        <h2 className="mt-5 font-display text-3xl text-slate-900">Drop a folder of designs</h2>
        <p className="mt-2 max-w-md text-sm text-slate-500">
          Each subfolder becomes one listing group, and the SKU is read from the folder or file
          name. PNG, JPG, WebP, GIF and TIFF are accepted.
        </p>
        <button
          type="button"
          disabled={busy}
          className="btn-secondary mt-6"
          onClick={() => inputRef.current?.click()}
        >
          Choose a folder
        </button>
        {folderInput}
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
          <button
            type="button"
            disabled={busy}
            className="btn-secondary"
            onClick={() => inputRef.current?.click()}
          >
            Choose a folder
          </button>
          {batchId && !busy && (
            <button className="btn-primary" onClick={() => router.push(`/batches/${batchId}`)}>
              Open batch
            </button>
          )}
        </div>
        {folderInput}
      </div>

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
      <div className="grid grid-cols-4 grid-rows-2 gap-1 bg-slate-100 p-1" style={{ aspectRatio: "16 / 10" }}>
        {Array.from({ length: 5 }, (_, i) => {
          const r = tiles[i];
          return (
            <div
              key={i}
              className={
                "relative overflow-hidden rounded-lg bg-slate-50 " +
                (i === 0 ? "col-span-2 row-span-2" : "")
              }
            >
              {r?.preview ? (
                <>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={r.preview}
                    alt={r.name}
                    className={
                      "h-full w-full object-cover transition-opacity " +
                      (r.status === "done" ? "opacity-100" : "opacity-60")
                    }
                    decoding="async"
                  />
                  {r.status === "error" && (
                    <span className="absolute inset-0 bg-rose-900/30" aria-hidden />
                  )}
                </>
              ) : r ? (
                <div className="flex h-full w-full items-center justify-center text-[10px] uppercase tracking-wide text-slate-400">
                  {r.name.split(".").pop()}
                </div>
              ) : (
                <div className="h-full w-full bg-slate-100/60" />
              )}
              {more > 0 && i === 4 && (
                <span className="absolute inset-0 flex items-center justify-center bg-slate-900/55 text-sm font-medium tabular-nums text-white">
                  +{more}
                </span>
              )}
            </div>
          );
        })}
      </div>

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

function update(rows: Row[], i: number, patch: Partial<Row>): Row[] {
  return rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r));
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
