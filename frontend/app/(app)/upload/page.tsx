"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, uploadArchive, uploadAsset } from "@/lib/api";
import { UPLOAD_PARALLEL, UPLOAD_TRIES, runLimited, withRetry } from "@/lib/uploadQueue";
import { StatusPill } from "@/components/StatusPill";
import { relativeTime } from "@/lib/format";
import type { ArchiveResult, BatchSummary } from "@/lib/types";
import {
  groupKeyOf,
  movePhotos,
  stage,
  stageArchives,
  underListing,
  withoutListing,
  type StagedFile,
  type StagedGroup,
} from "@/lib/staging";
import { place } from "@/lib/grouping";
import { StagingBoard } from "@/components/StagingBoard";
import { smallPreview } from "@/lib/thumbs";
import {
  autoMode,
  groupLabel,
  isUnsorted,
  rememberChoice,
  rememberedChoices,
  uploadKind,
  type GroupingMode,
  type PreviewFile,
} from "@/lib/grouping";
import { GroupingChoice, GroupingResult } from "@/components/GroupingChoice";
import { MockupUpload } from "@/components/MockupUpload";

import { Txt } from "@/components/Txt";
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
  /** The folder it came from ("" when loose): what a regrouping counts from. */
  folder: string;
  assetId?: string;
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
  // Files are staged one add at a time (identical content is compared, which takes a
  // moment), so two quick adds never race; one line says what a re-added folder became.
  const stagedRef = useRef<StagedGroup<File>[]>([]);
  useEffect(() => {
    stagedRef.current = staged;
  }, [staged]);
  const staging = useRef<Promise<void>>(Promise.resolve());
  const [stageNote, setStageNote] = useState<string | null>(null);
  const addStaged = useCallback((incoming: StagedFile<File>[], fresh: boolean) => {
    staging.current = staging.current.then(async () => {
      const result = await stage(fresh ? [] : stagedRef.current, incoming);
      stagedRef.current = result.groups;
      setStaged(result.groups);
      setStageNote(result.notes.length ? result.notes.join("; ") : null);
    }).catch(() => undefined);
  }, []);
  const [archives, setArchives] = useState<File[]>([]);
  // A small preview per staged photo, made a few at a time in the background so a
  // 200-photo upload never blocks the page (undefined: not made yet; null: none).
  const [previews, setPreviews] = useState<Map<File, string | null>>(new Map());
  const ids = useRef(new WeakMap<File, string>());
  const nextId = useRef(0);
  const idOf = useCallback((file: File) => {
    let id = ids.current.get(file);
    if (!id) {
      id = `p${(nextId.current += 1)}`;
      ids.current.set(file, id);
    }
    return id;
  }, []);
  // Arranged by hand: one listing per card from then on.
  const [arrangedByHand, setArrangedByHand] = useState(false);
  // Files in chosen folders that are not images or ZIPs, left out.
  const [ignored, setIgnored] = useState(0);
  const [phase, setPhase] = useState<"staging" | "uploading" | "done">("staging");
  // Uploads are not tied to a shop (B1): where a listing goes is chosen on the review page.
  // The upload itself: one row per file.
  const [rows, setRows] = useState<Row[]>([]);
  const [dragging, setDragging] = useState(false);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [recent, setRecent] = useState<BatchSummary[] | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const filesRef = useRef<HTMLInputElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  // What each ZIP skipped or refused, so nothing disappears without a word (v6 §F).
  const [notes, setNotes] = useState<string[]>([]);
  // How the photos become listings: the seller's pick, else a default from the files.
  const [modeChoice, setModeChoice] = useState<GroupingMode | null>(null);
  // The batch as the server grouped it, once uploaded ("Found 42 groups by SKU…").
  const [summary, setSummary] = useState<BatchSummary | null>(null);
  const [regrouping, setRegrouping] = useState(false);
  // Photos added after an upload finished join that upload (same batch), until
  // anything is written from it: dropping photos one at a time never splits them
  // into separate uploads.
  const [appending, setAppending] = useState(false);
  // Upload rounds into one batch, so each round's row keys are unique.
  const round = useRef(0);
  // Object URLs this page made, by file, so removing photos frees their previews:
  // adding and removing over and over must not leak memory.
  const owned = useRef<Map<File, string>>(new Map());
  // Previews still shown on rows of an earlier round of the same upload.
  const kept = useRef<string[]>([]);
  // Previews being made now (one queue, two at a time).
  const making = useRef<Set<File>>(new Set());
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

  // Previews of photos no longer staged are freed.
  const releaseGone = useCallback((still: StagedGroup<File>[]) => {
    const keep = new Set(still.flatMap((g) => g.files.map((sf) => sf.file)));
    for (const [file, url] of owned.current) {
      if (!keep.has(file)) {
        URL.revokeObjectURL(url);
        owned.current.delete(file);
      }
    }
    setPreviews((m) => new Map([...m].filter(([file]) => keep.has(file))));
  }, []);

  const releaseAll = useCallback(() => {
    for (const url of owned.current.values()) URL.revokeObjectURL(url);
    kept.current.forEach((u) => URL.revokeObjectURL(u));
    kept.current = [];
    owned.current.clear();
    making.current.clear();
    setPreviews(new Map());
  }, []);

  // Object URLs are owned by this page; release them when it goes away.
  useEffect(() => releaseAll, [releaseAll]);

  // Make the previews the staged photos still lack, two at a time, in the order shown.
  useEffect(() => {
    const waiting = staged.flatMap((g) => g.files.map((sf) => sf.file)).filter((f) => !previews.has(f) && !making.current.has(f));
    const slots = Math.max(0, 2 - making.current.size);
    for (const file of waiting.slice(0, slots)) {
      making.current.add(file);
      smallPreview(file).then((url) => {
        if (!making.current.delete(file)) {
          if (url) URL.revokeObjectURL(url); // cleared meanwhile
          return;
        }
        if (url) owned.current.set(file, url);
        setPreviews((m) => new Map(m).set(file, url));
      });
    }
  }, [staged, previews]);

  const addItems = useCallback(
    (items: Item[]) => {
      const images = items.filter((it) => IMAGE_RE.test(it.file.name));
      const zips = items.filter((it) => ZIP_RE.test(it.file.name)).map((it) => it.file);
      const others = items.length - images.length - zips.length;
      const incoming = images.map((it) => ({ file: it.file, relpath: it.relpath }));
      if (phase === "done" && batchId && summary && summary.regroupable !== false) {
        // The last upload is finished but nothing is written from it yet: these join
        // it. The rows already shown keep their previews; new folders make their own.
        kept.current.push(...owned.current.values());
        owned.current.clear();
        making.current.clear();
        setPreviews(new Map());
        setAppending(true);
        setPhase("staging");
        addStaged(incoming, true);
        setArchives(stageArchives([], zips));
        setIgnored(others);
        return;
      }
      if (phase === "done") {
        // Listings are already written from the last upload: this starts the next one.
        releaseAll();
        setAppending(false);
        setRows([]);
        setNotes([]);
        setSummary(null);
        setModeChoice(null);
        setBatchId(null);
        setPhase("staging");
        addStaged(incoming, true);
        setArchives(stageArchives([], zips));
        setIgnored(others);
        return;
      }
      // Added to what is already staged, never in place of it; the same folder again is merged.
      addStaged(incoming, false);
      setArchives((a) => stageArchives(a, zips));
      setIgnored((n) => n + others);
    },
    [phase, releaseAll, batchId, summary, addStaged],
  );

  /** Make what is staged a separate upload instead of joining the last one. */
  function separate() {
    releaseAll();
    setRows([]);
    setNotes([]);
    setSummary(null);
    setBatchId(null);
    setAppending(false);
    setModeChoice(null);
  }

  // The cards are the listings the photos become (lib/staging.ts listingsOf): moving a
  // photo, starting a listing or adding photos to a card arranges them by hand, so from
  // then on each card is one listing (the "one listing per folder" choice).
  function arrange(next: StagedGroup<File>[]) {
    stagedRef.current = next;
    setStaged(next);
    releaseGone(next);
    if (mode !== "folder") setModeChoice("folder");
    setArrangedByHand(true);
  }
  function moveTo(files: File[], to: string | null) {
    arrange(movePhotos(stagedRef.current, mode, place, files, to).groups);
  }
  function addTo(incoming: StagedFile<File>[], to: string | null) {
    const placed = underListing(stagedRef.current, mode, place, incoming, to);
    staging.current = staging.current.then(async () => {
      const result = await stage(placed.base, placed.incoming);
      arrange(result.groups);
      setStageNote(result.notes.length ? result.notes.join("; ") : null);
    }).catch(() => undefined);
  }
  function removeListing(key: string) {
    const next = withoutListing(stagedRef.current, mode, place, key);
    stagedRef.current = next;
    setStaged(next);
    releaseGone(next);
  }

  function clearAll() {
    if (appending) {
      // Back to the finished upload; nothing new is added.
      setStaged([]);
      setStageNote(null);
      setArchives([]);
      setIgnored(0);
      setPhase("done");
      setAppending(false);
      return;
    }
    releaseAll();
    setArrangedByHand(false);
    setModeChoice(null);
    setStaged([]);
    setStageNote(null);
    setArchives([]);
    setIgnored(0);
  }

  const imageCount = staged.reduce((n, g) => n + g.files.length, 0);
  const folderCount = staged.filter((g) => g.key !== "").length;
  const stagedFiles: PreviewFile[] = useMemo(
    () => staged.flatMap((g) => g.files.map((sf) => ({ folder: groupKeyOf(sf.relpath), name: sf.file.name }))),
    [staged],
  );
  // When joining the last upload the choice covers its photos too.
  const uploadedFiles: PreviewFile[] = useMemo(
    () => rows.filter((r) => r.assetId).map((r) => ({ folder: r.folder, name: r.name })),
    [rows],
  );
  const previewFiles = useMemo(
    () => (appending ? [...uploadedFiles, ...stagedFiles] : stagedFiles),
    [appending, uploadedFiles, stagedFiles],
  );
  // Folders present -> one per folder; flat with SKUs -> by SKU; flat without -> one listing.
  // The seller's last choice for the same kind of upload wins; a joined upload keeps its own.
  const mode: GroupingMode =
    modeChoice ??
    ((appending ? summary?.grouping_mode : null) as GroupingMode | null) ??
    autoMode(previewFiles, rememberedChoices());

  function chooseMode(next: GroupingMode, files: PreviewFile[]) {
    setModeChoice(next);
    rememberChoice(uploadKind(files), next);
  }

  // After upload, before anything is written: switching regroups on the server.
  async function regroup(next: GroupingMode) {
    if (!batchId) return;
    const files = rows.filter((r) => r.assetId).map((r) => ({ folder: r.folder, name: r.name }));
    chooseMode(next, files);
    setRegrouping(true);
    try {
      setSummary(await api.regroup(batchId, next));
      const detail = await api.getBatch(batchId);
      const byId = new Map(detail.assets.map((a) => [a.id, a]));
      setRows((rs) =>
        rs.map((r) => {
          const a = r.assetId ? byId.get(r.assetId) : undefined;
          return a ? { ...r, group: a.group_key || "(root)", sku: a.parsed_sku } : r;
        }),
      );
    } catch (e: any) {
      setNotes((n) => [String(e.message ?? e), ...n]);
    } finally {
      setRegrouping(false);
    }
  }

  // Files that did not arrive (after their own tries), kept so "Try the failed ones again"
  // sends only them, into the same batch.
  const failedFiles = useRef(new Map<string, { file: File; relpath: string }>());

  /** Up to three files at a time (lib/uploadQueue.ts); a file that fails on the way is tried again by itself. */
  async function sendFiles(batch: string, files: { file: File; relpath: string; key: string }[], chosen: GroupingMode) {
    await runLimited(files, UPLOAD_PARALLEL, async ({ file, relpath, key }) => {
      setRows((r) => update(r, key, { status: "uploading", pct: 0, error: undefined }));
      try {
        const asset = await withRetry(
          () => uploadAsset(batch, file, (pct) => setRows((r) => update(r, key, { pct })), groupKeyOf(relpath), chosen),
          { onRetry: (n) => setRows((r) => update(r, key, { pct: 0, error: `Trying again (${n} of ${UPLOAD_TRIES})` })) },
        );
        failedFiles.current.delete(key);
        // The server decides the group: a folder, else the SKU in the name, else Unsorted.
        setRows((r) =>
          update(r, key, {
            status: "done", pct: 100, sku: asset.parsed_sku, assetStatus: asset.status,
            group: asset.group_key || "(root)", assetId: asset.id, error: undefined,
          }),
        );
      } catch (e: any) {
        failedFiles.current.set(key, { file, relpath });
        setRows((r) => update(r, key, { status: "error", error: String(e.message ?? e) }));
      }
    });
  }

  async function retryFailed() {
    if (!batchId || failedFiles.current.size === 0) return;
    setPhase("uploading");
    const again = [...failedFiles.current.entries()].map(([key, f]) => ({ ...f, key }));
    const chosen = (summary?.grouping_mode as GroupingMode | null) ?? mode;
    await sendFiles(batchId, again, chosen);
    try {
      setSummary(await api.finalizeBatch(batchId));
    } catch (e: any) {
      setNotes((n) => [String(e.message ?? e), ...n]);
    }
    setPhase("done");
  }

  async function upload() {
    if (imageCount === 0 && archives.length === 0) return;
    setPhase("uploading");
    setNotes([]);
    const chosen = mode;
    const joining = appending && batchId ? batchId : null;
    const before = joining ? (summary?.grouping_mode ?? null) : null;
    const r0 = (round.current += 1);
    const files = staged.flatMap((g) =>
      g.files.map((sf, i) => ({ sf, key: `r${r0}-f-${g.key}-${i}`, preview: previews.get(sf.file) ?? undefined })),
    );
    setRows((earlier) => [
      ...(joining ? earlier : []),
      ...files.map(({ sf, key, preview }) => ({
        key,
        name: sf.file.name,
        pct: 0,
        status: "pending" as const,
        group: groupKeyOf(sf.relpath) || "(root)",
        folder: groupKeyOf(sf.relpath),
        preview,
      })),
      // A ZIP shows as one row until the server has unpacked it.
      ...archives.map((z, j) => ({
        key: `r${r0}-z${j}`,
        name: z.name,
        pct: 0,
        status: "pending" as const,
        group: z.name,
        folder: "",
      })),
    ]);

    let batch: { id: string };
    if (joining) {
      batch = { id: joining };
    } else {
      try {
        batch = await api.createBatch(null);
      } catch (e: any) {
        setNotes([String(e.message ?? e)]);
        setPhase("staging");
        return;
      }
      setBatchId(batch.id);
    }

    await sendFiles(batch.id, files.map(({ sf, key }) => ({ file: sf.file, relpath: sf.relpath, key })), chosen);

    for (const [j, zip] of archives.entries()) {
      const key = `r${r0}-z${j}`;
      setRows((r) => update(r, key, { status: "uploading" }));
      try {
        const res = await uploadArchive(
          batch.id,
          zip,
          (pct) => setRows((r) => update(r, key, { pct, name: pct >= 100 ? `${zip.name}: unpacking…` : zip.name })),
          chosen,
        );
        setRows((r) => r.flatMap((row) => (row.key === key ? archiveRows(key, res) : [row])));
        setNotes((n) => [...n, ...archiveNotes(zip.name, res)]);
      } catch (e: any) {
        setRows((r) => update(r, key, { status: "error", name: zip.name, error: String(e.message ?? e) }));
      }
    }

    // Joining with another choice than before: the earlier photos follow it too.
    if (joining && before && before !== chosen) {
      try {
        await api.regroup(batch.id, chosen);
      } catch (e: any) {
        setNotes((n) => [String(e.message ?? e), ...n]);
      }
    }
    // "Found 42 groups by SKU, 3 photos unsorted": shown before anything is written.
    setSummary(await api.finalizeBatch(batch.id));
    if (joining) {
      // Every row's group as the server has it now (earlier photos may have joined new ones).
      try {
        const detail = await api.getBatch(batch.id);
        const byId = new Map(detail.assets.map((a) => [a.id, a]));
        setRows((rs) =>
          rs.map((r) => {
            const a = r.assetId ? byId.get(r.assetId) : undefined;
            return a ? { ...r, group: a.group_key || "(root)", sku: a.parsed_sku } : r;
          }),
        );
      } catch {
        /* the groups shown stay as each upload answered */
      }
    }
    // The rows keep the previews they show; nothing is staged any more.
    kept.current.push(...owned.current.values());
    owned.current.clear();
    setAppending(false);
    setArrangedByHand(false);
    setStaged([]);
    setStageNote(null);
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
  // The Unsorted tray holds photos, not a listing.
  const listingGroups = groups.filter((g) => !isUnsorted(g.key)).length;
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

  // Photos and ZIP files, together, in one dialog (folders: the drop zone, or "A folder…").
  const filesInput = (
    <input
      ref={filesRef}
      type="file"
      multiple
      accept="image/png,image/jpeg,image/webp,image/gif,image/tiff,.zip,application/zip"
      className="hidden"
      data-testid="files-input"
      onChange={(e) => {
        const files = Array.from(e.target.files ?? []);
        e.target.value = "";
        if (files.length) addItems(files.map((f) => ({ file: f, relpath: f.name })));
      }}
    />
  );

  // One "Add files" button. A browser's file dialog picks either files or one folder, never
  // both at once, so the button offers the two; the drop zone takes anything mixed.
  const pickers = (
    <span className="relative inline-flex">
      <button type="button" disabled={busy} className={empty ? "btn-primary" : "btn-secondary"} aria-haspopup="menu" aria-expanded={menuOpen}
        onClick={() => setMenuOpen((o) => !o)} data-testid="add-files">
        Add files
      </button>
      {menuOpen && (
        <span key="menu" role="menu" className="absolute right-0 top-full z-20 mt-1 flex w-56 flex-col rounded-lg border border-slate-200 bg-white p-1 text-left shadow-lg">
          <button type="button" role="menuitem" className="tap rounded px-3 py-2 text-left text-sm hover:bg-slate-50"
            onClick={() => { setMenuOpen(false); filesRef.current?.click(); }}>Photos or ZIP files…</button>
          <button type="button" role="menuitem" className="tap rounded px-3 py-2 text-left text-sm hover:bg-slate-50"
            onClick={() => { setMenuOpen(false); inputRef.current?.click(); }}>A folder…</button>
        </span>
      )}
    </span>
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
          <h2 className="mt-4 font-display text-3xl text-slate-900">Drop photos, folders or ZIPs of designs</h2>
          <p className="mt-1.5 max-w-md text-sm text-slate-500">
            Drop everything at once, mixed. You see the listings they become and can move photos between
            them before anything is uploaded.
          </p>
          <p className="mt-2 max-w-md text-xs text-slate-500">
            Keep your own copies: image files are stored here for a limited time after a listing is
            published, then deleted. The listing on Etsy is not affected.
          </p>
          <div className="mt-5 flex flex-wrap justify-center gap-2">{pickers}</div>
          {folderInput}
          {filesInput}
        </div>

        <MockupUpload shop={null} />

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
            <p className="text-sm font-medium text-slate-800">{appending ? "Adding to this upload" : "Ready to upload"}</p>
            {appending && (
              <p key="joining" className="mt-0.5 text-xs text-slate-600">
                <span translate="no" className="tabular-nums">{uploadedFiles.length}</span>
                <span> photos are already uploaded; these join them, grouped the same way. </span>
                <button type="button" className="tap text-brand-700 underline" onClick={separate} data-testid="separate-upload">
                  Make it a separate upload
                </button>
              </p>
            )}
            <p className="mt-0.5 text-xs text-slate-500">
              {folderCount > 0 && (
                <span key="folders"><span translate="no" className="tabular-nums">{folderCount}</span> <span><span>{folderCount === 1 ? "folder" : "folders"}</span> ·{" "}</span></span>
              )}
              <span translate="no" className="tabular-nums">{imageCount}</span> <span><span>{imageCount === 1 ? "image" : "images"}</span></span>
              {archives.length > 0 && (
                <>
                  <span>{" · "}</span>
                  <span translate="no" className="tabular-nums">{archives.length}</span><span><span> ZIP</span><Txt>{archives.length === 1 ? "" : "s"}</Txt></span>
                </>
              )}
              {ignored > 0 && <><span><span> · </span><span>{ignored}</span><span> other file</span><Txt>{ignored === 1 ? "" : "s"}</Txt><span> left out (not images)</span></span></>}
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
            >
              <span><span>{appending ? "Add" : "Upload"}</span> <span>{staged.length + archives.length}</span> <span>{staged.length + archives.length === 1 ? "item" : "items"}</span></span>
            </button>
          </div>
          {folderInput}
          {filesInput}
        </div>

        {imageCount > 0 && (
          <div key="choice" className="card p-4">
            <GroupingChoice
              files={previewFiles}
              value={mode}
              onChange={(m) => chooseMode(m, previewFiles)}
              extra={archives.length ? "Photos inside ZIP files are counted once they are unpacked; you can still switch then." : "You can still switch after the upload, until anything is written."}
            />
          </div>
        )}

        {stageNote && (
          <p key="stage-note" role="status" translate="no" className="text-xs text-slate-600" data-testid="stage-note">
            {stageNote}
          </p>
        )}

        {arrangedByHand && (
          <p key="arranged" className="text-xs text-slate-600" data-testid="arranged-note">Arranged by hand: each card is one listing.</p>
        )}

        <StagingBoard staged={staged} mode={mode} previews={previews} idOf={idOf} onMove={moveTo} onAdd={addTo} onRemove={removeListing} />

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {archives.map((z) => (
            <div key={`${z.name}-${z.size}-${z.lastModified}`} className="card relative flex items-center gap-3 p-5">
              <span className="rounded-md bg-slate-100 px-2 py-1 text-xs font-medium uppercase text-slate-500">zip</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-slate-800">{z.name}</span>
                <span className="block text-xs text-slate-400">
                  <span><span>{(z.size / (1024 * 1024)).toFixed(1)}</span> MB · unpacked on upload, its folders kept</span>
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
            <span translate="no" className="tabular-nums">{done}</span><span> / <span>{rows.length}</span> files ·{" "}</span>
            <span translate="no" className="tabular-nums">{listingGroups}</span><span>{" "}
            <span>{listingGroups === 1 ? "listing group" : "listing groups"}</span></span>
            {failed > 0 && (
              <span key="span-448-12" className="text-rose-700">
                {" · "}
                <span translate="no" className="tabular-nums">{failed}</span> failed
              </span>
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!busy && failed > 0 && batchId && (
            <button key="retry-failed" type="button" className="btn-secondary" onClick={retryFailed} data-testid="upload-retry-failed">
              Try the failed ones again
            </button>
          )}
          {!busy && pickers}
          {batchId && !busy && (
            <button key="button-458-10" className="btn-primary" onClick={() => router.push(`/batches/${batchId}`)}>
              Open batch
            </button>
          )}
        </div>
        {folderInput}
        {filesInput}
      </div>

      {!busy && summary && (
        <div key="choice" className="card space-y-3 p-4">
          <p role="status" translate="no" className="text-sm font-medium text-slate-800" data-testid="grouping-found">
            {summary.grouping}
          </p>
          <GroupingResult
            files={rows.filter((r) => r.assetId).map((r) => ({ folder: r.folder, name: r.name }))}
            value={(summary.grouping_mode as GroupingMode | null) ?? mode}
            onChange={regroup}
            regroupable={summary.regroupable !== false}
            disabled={regrouping}
          />
        </div>
      )}

      {notes.length > 0 && (
        <ul key="ul-468-6" role="status" translate="no" className="card space-y-1 p-3 text-xs text-slate-600">
          {notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}

      {busy && (
        <div key="div-476-6" className="progress">
          <div
            className="progress-fill"
            style={{ width: (rows.length ? (done / rows.length) * 100 : 0) + "%" }}
          />
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {groups.map((g) => (
          <GroupCard key={g.key} group={g} oneListing={summary?.grouping_mode === "one"} />
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
              <span key="span-584-12" className="absolute inset-0 animate-pulse bg-slate-200" aria-hidden />
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
                  loading="lazy"
                  decoding="async"
                  draggable={false}
                />
                {t.status === "error" && <span key="span-600-16" className="absolute inset-0 bg-rose-900/30" aria-hidden />}
              </>
            ) : t && !t.loading ? (
              <div className="flex h-full w-full items-center justify-center text-[10px] uppercase tracking-wide text-slate-400">
                {t.name.split(".").pop()}
              </div>
            ) : null}
            {more > 0 && i === 4 && (
              <span translate="no" key="span-607-12" className="absolute inset-0 flex items-center justify-center bg-slate-900/55 text-sm font-medium tabular-nums text-white">
                <span>+<span>{more}</span></span>
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

function GroupCard({ group, oneListing = false }: { group: DetectedGroup; oneListing?: boolean }) {
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
            {group.key === "(root)" || group.key === "" ? (oneListing ? "All photos (one listing)" : "Photos without a folder") : groupLabel(group.key)}
          </h2>
          {complete && <StatusPill key="statuspill-666-10" status={rows[0]?.assetStatus ?? "uploaded"} />}
        </div>

        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {group.sku ? (
            <span translate="no" className="rounded-md border border-brand-100 bg-brand-50 px-1.5 py-0.5 font-medium tabular-nums text-brand-700">
              {group.sku}
            </span>
          ) : (
            <span className="rounded-md border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-slate-400">
              no SKU
            </span>
          )}
          <span className="text-slate-400">
            <span translate="no" className="tabular-nums">{rows.length}</span><span>{" "}
            <span>{rows.length === 1 ? "image" : "images"}</span></span>
          </span>
        </div>

        {!complete && (
          <div key="div-685-8" className="mt-1">
            <div className="progress">
              <div
                className={
                  "h-full rounded-full transition-all " +
                  (errors.length ? "bg-rose-500" : "bg-brand-600")
                }
                style={{ width: pct + "%" }}
              />
            </div>
            <p translate="no" className="mt-1.5 text-xs tabular-nums text-slate-500">
              <span><span>{done}</span> / <span>{rows.length}</span> uploaded</span>
            </p>
          </div>
        )}

        {errors.length > 0 && (
          <ul key="ul-702-8" className="mt-auto space-y-0.5 pt-1 text-xs text-rose-700">
            {errors.slice(0, 3).map((r, i) => (
              <li key={i} className="truncate" title={r.error}>
                <span><span>{r.name}</span><span>: </span><Txt>{r.error}</Txt></span>
              </li>
            ))}
            {errors.length > 3 && <li key="li-709-12"><span>+<span>{errors.length - 3}</span> more failed</span></li>}
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
    folder: a.upload_folder ?? "",
    assetId: a.id,
    assetStatus: a.status,
    preview: a.status === "processed" ? api.assetImage(a.id, "small") : undefined,
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
        <Link href="/batches" className="tap text-xs font-medium text-brand-700 hover:text-brand-800">
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
                    <span translate="no">{b.name || `Batch ${b.id.slice(0, 8)}`}</span>
                  </span>
                </span>
                <span className="shrink-0 text-xs text-slate-400">
                  <span translate="no" className="tabular-nums">{b.asset_count}</span> files ·{" "}
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
