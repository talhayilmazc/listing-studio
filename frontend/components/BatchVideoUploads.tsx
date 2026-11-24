"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { groupKeyOf } from "@/lib/staging";
import { durationProblem, failedLine, fileProblem, readSeconds, uploadingLine, videoLine, type ListingVideo } from "@/lib/videoRules";
import { uploadVideo } from "@/lib/videoUpload";
import { Txt } from "./Txt";

export interface VideoItem { file: File; relpath: string }

type Row = {
  item: VideoItem;
  /** What Etsy would not take, found before anything is sent. */
  problem: string | null;
  checked: boolean;
  progress: { sent: number; total: number } | null;
  video: ListingVideo | null;
  error: string | null;
};

const UNSORTED = "~unsorted";

/** Where a video uploaded with the photos went, in the seller's words. */
export function placedLine(video: Pick<ListingVideo, "group_key">): string {
  if (video.group_key === UNSORTED) return "no SKU in its name, so it is with no listing: add it on a listing's card instead";
  return video.group_key ? `with ${video.group_key}'s photos` : "with this upload's listing";
}

/**
 * Videos chosen with a batch's photos (feature/listing-video). Each MP4 or MOV goes to the listing its folder or
 * SKU names, by the same rule that groups the photos; it is never a photo and never the cover. They are checked
 * here first (a video Etsy would not take is not uploaded), sent in pieces once the photos are up, and each row
 * says what became of it, with "Try again" for one that did not arrive.
 */
export function BatchVideoUploads({ items, batchId, ready }: { items: VideoItem[]; batchId: string | null; ready: boolean }) {
  const [rows, setRows] = useState<Row[]>([]);
  const known = useRef(new Set<File>());
  const sending = useRef(false);
  const [tick, setTick] = useState(0);

  // New files: checked at once (kind, size, and how long they are, read in the browser).
  useEffect(() => {
    const fresh = items.filter((it) => !known.current.has(it.file));
    if (!fresh.length) return;
    fresh.forEach((it) => known.current.add(it.file));
    setRows((rs) => [...rs, ...fresh.map((item) => ({ item, problem: fileProblem(item.file), checked: false, progress: null, video: null, error: null }))]);
    fresh.forEach(async (item) => {
      const seconds = fileProblem(item.file) ? null : await readSeconds(item.file);
      setRows((rs) => rs.map((r) => (r.item.file === item.file
        ? { ...r, checked: true, problem: r.problem ?? (seconds !== null ? durationProblem(seconds) : null) } : r)));
    });
  }, [items]);

  const patch = (file: File, change: Partial<Row>) => setRows((rs) => rs.map((r) => (r.item.file === file ? { ...r, ...change } : r)));

  // Once the photos are up: one video at a time, each in pieces.
  useEffect(() => {
    if (!ready || !batchId || sending.current) return;
    const next = rows.find((r) => r.checked && !r.problem && !r.video && !r.error && !r.progress);
    if (!next) return;
    sending.current = true;
    const file = next.item.file;
    patch(file, { progress: { sent: 0, total: file.size } });
    uploadVideo(batchId, file, { place: true, uploadFolder: groupKeyOf(next.item.relpath) || null },
      (sent, total) => patch(file, { progress: { sent, total } }))
      .then((video) => patch(file, { video, progress: null }))
      .catch((e) => patch(file, { error: failedLine(file.name, e), progress: null }))
      .finally(() => {
        sending.current = false;
        setTick((n) => n + 1);
      });
  }, [ready, batchId, rows, tick]);

  // A video the worker is reading: asked about until it is ready or refused.
  useEffect(() => {
    const waiting = rows.filter((r) => r.video && r.video.status === "processing");
    if (!waiting.length) return;
    const t = setTimeout(async () => {
      for (const r of waiting) {
        try {
          patch(r.item.file, { video: await api.video(r.video!.id) });
        } catch {
          /* asked again next time */
        }
      }
    }, 2500);
    return () => clearTimeout(t);
  }, [rows]);

  if (!rows.length) return null;
  return (
    <div className="card space-y-1.5 p-3 text-xs" data-testid="batch-videos">
      <p className="font-medium text-slate-800">
        <span translate="no">{rows.length === 1 ? "1 video" : `${rows.length} videos`}</span>
        <span className="font-normal text-slate-600">: each goes to the listing its folder or SKU names. A video is never a photo and never the cover.</span>
      </p>
      <ul className="space-y-1">
        {rows.map((r) => {
          const refused = r.problem ?? r.error ?? (r.video?.status === "failed" ? r.video.error : null);
          return (
            <li key={r.item.relpath + r.item.file.size} className="flex flex-wrap items-center gap-x-2 gap-y-1" data-testid="batch-video-row">
              <span className="font-medium text-slate-700" translate="no">{r.item.file.name}</span>
              {refused ? (
                <span key="refused" role="alert" className="text-rose-700"><Txt>{refused}</Txt></span>
              ) : r.progress ? (
                <span key="progress" role="status" translate="no">{uploadingLine(r.progress.sent, r.progress.total)}</span>
              ) : r.video?.status === "processing" ? (
                <span key="processing" role="status" translate="no">Processing…</span>
              ) : r.video ? (
                <span key="done" className="text-slate-700" translate="no">{`${videoLine({ duration: r.video.duration, filename: "" })} · ${placedLine(r.video)}`}</span>
              ) : (
                <span key="waiting" className="text-slate-600">goes up after the photos</span>
              )}
              {r.error && (
                <button key="again" type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={() => patch(r.item.file, { error: null })} data-testid="batch-video-again">
                  Try again
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
