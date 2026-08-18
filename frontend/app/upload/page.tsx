"use client";

import { useRouter } from "next/navigation";
import { useCallback, useRef, useState } from "react";
import { api, uploadAsset } from "@/lib/api";
import { StatusPill } from "@/components/StatusPill";

interface Row {
  name: string;
  pct: number;
  status: "pending" | "uploading" | "done" | "error";
  sku?: string | null;
  assetStatus?: string;
  error?: string;
}

const IMAGE_RE = /\.(png|jpe?g|webp|gif|tiff?)$/i;

export default function UploadPage() {
  const router = useRouter();
  const [rows, setRows] = useState<Row[]>([]);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [batchId, setBatchId] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const runUpload = useCallback(
    async (files: File[]) => {
      const images = files.filter((f) => IMAGE_RE.test(f.name));
      if (images.length === 0) {
        setRows([{ name: "No image files found in that folder.", pct: 0, status: "error" }]);
        return;
      }
      setBusy(true);
      setRows(images.map((f) => ({ name: f.name, pct: 0, status: "pending" })));

      const batch = await api.createBatch();
      setBatchId(batch.id);

      for (let i = 0; i < images.length; i++) {
        setRows((r) => update(r, i, { status: "uploading" }));
        try {
          const asset = await uploadAsset(batch.id, images[i], (pct) =>
            setRows((r) => update(r, i, { pct })),
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

  async function collectFromDrop(dt: DataTransfer): Promise<File[]> {
    const items = Array.from(dt.items);
    const entries = items
      .map((it) => (it.webkitGetAsEntry ? it.webkitGetAsEntry() : null))
      .filter(Boolean) as FileSystemEntry[];
    if (entries.length > 0) {
      const out: File[] = [];
      await Promise.all(entries.map((e) => walkEntry(e, out)));
      return out;
    }
    return Array.from(dt.files);
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Upload designs</h1>
        <p className="mt-1 text-sm text-slate-500">
          Drop a folder of your original design images. Each file becomes an asset with its SKU
          parsed from the filename.
        </p>
      </div>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={async (e) => {
          e.preventDefault();
          setDragging(false);
          if (busy) return;
          const files = await collectFromDrop(e.dataTransfer);
          runUpload(files);
        }}
        className={`card flex flex-col items-center justify-center gap-3 border-2 border-dashed p-12 text-center transition-colors ${
          dragging ? "border-brand-500 bg-brand-50" : "border-slate-300"
        }`}
      >
        <div className="flex h-12 w-12 items-center justify-center rounded-full bg-brand-100 text-brand-600">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M12 16V4M12 4l-4 4M12 4l4 4" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2" strokeLinecap="round" />
          </svg>
        </div>
        <p className="font-medium text-slate-700">Drag a folder here</p>
        <p className="text-sm text-slate-400">PNG, JPG, WebP, GIF · or</p>
        <button
          type="button"
          disabled={busy}
          className="btn-secondary"
          onClick={() => inputRef.current?.click()}
        >
          Choose a folder
        </button>
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
            if (files.length) runUpload(files);
          }}
        />
      </div>

      {rows.length > 0 && (
        <div className="card divide-y divide-slate-100">
          <div className="flex items-center justify-between px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-700">
              Files ({rows.filter((r) => r.status === "done").length}/{rows.length})
            </h2>
            {batchId && !busy && (
              <button className="btn-primary" onClick={() => router.push(`/batches/${batchId}`)}>
                Open batch
              </button>
            )}
          </div>
          {rows.map((r, i) => (
            <div key={i} className="flex items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm text-slate-700">{r.name}</span>
                  {r.sku && (
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-600">
                      {r.sku}
                    </span>
                  )}
                </div>
                <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                  <div
                    className={`h-full rounded-full transition-all ${
                      r.status === "error" ? "bg-rose-400" : "bg-brand-500"
                    }`}
                    style={{ width: `${r.pct}%` }}
                  />
                </div>
                {r.error && <p className="mt-1 text-xs text-rose-600">{r.error}</p>}
              </div>
              <div className="w-20 text-right">
                {r.assetStatus ? (
                  <StatusPill status={r.assetStatus} />
                ) : (
                  <span className="text-xs text-slate-400">{r.status}</span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function update(rows: Row[], i: number, patch: Partial<Row>): Row[] {
  return rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r));
}

async function walkEntry(entry: FileSystemEntry, out: File[]): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    );
    out.push(file);
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    const entries = await new Promise<FileSystemEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject),
    );
    await Promise.all(entries.map((e) => walkEntry(e, out)));
  }
}
