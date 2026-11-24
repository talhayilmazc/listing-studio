"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  ACCEPT, MAX_SECONDS, MIN_SECONDS, busy, draftLine, durationProblem, failedLine, fileProblem, readSeconds, slotOf, uploadingLine, videoLine,
  type BatchVideos, type ListingVideo, type VideoDraft,
} from "@/lib/videoRules";
import { uploadVideo } from "@/lib/videoUpload";
import { Txt } from "./Txt";

const LOOK_AGAIN_MS = 2500;
const LOOKS = 120;

/**
 * A listing's video (feature/listing-video): Add / Replace / Remove, with its poster frame and how long it is.
 * Etsy's rules are checked before anything is sent ("This video is 22 s; Etsy takes 5–15 s."), the file goes up
 * in pieces with "Uploading 40%", then "Processing…". Under it, what became of the video on each draft: attached,
 * or "Draft made; the video couldn't be added: <Etsy's reason>" with "Try again", which sends only the video.
 * With `shop`: that shop's own video (the listing's is used there until it has one).
 *
 * A listing with no video shows one quiet line and changes nothing else.
 */
export function VideoSlot({ batchId, groupKey, contentId, shop = null, disabled = false }: {
  batchId: string;
  groupKey: string;
  contentId: string | null;
  shop?: { id: string; name: string } | null;
  disabled?: boolean;
}) {
  const [data, setData] = useState<BatchVideos | null>(null);
  const [progress, setProgress] = useState<{ sent: number; total: number } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [retry, setRetry] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [replacing, setReplacing] = useState<VideoDraft | null>(null);
  const [working, setWorking] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const last = useRef<File | null>(null);
  const looks = useRef(0);

  const load = useCallback(async () => {
    try {
      setData(await api.batchVideos(batchId));
    } catch {
      /* the slot stays as it was; the next look tries again */
    }
  }, [batchId]);
  useEffect(() => {
    load();
  }, [load]);

  const shopId = shop?.id ?? null;
  const { current, pending } = slotOf(data?.videos ?? [], groupKey, shopId);
  const listings = shopId ? slotOf(data?.videos ?? [], groupKey, null).current : null;
  const drafts = (data?.drafts ?? []).filter((d) => d.group_key === groupKey && (!contentId || d.content_id === contentId) && (!shopId || d.connection_id === shopId));
  const waiting = progress === null && data !== null && busy(data.videos, data.drafts, groupKey);
  useEffect(() => {
    if (!waiting || looks.current >= LOOKS) return;
    const t = setTimeout(() => {
      looks.current += 1;
      load();
    }, LOOK_AGAIN_MS);
    return () => clearTimeout(t);
  }, [waiting, load, data]);

  async function send(file: File) {
    last.current = file;
    setMessage(null);
    setRetry(false);
    const wrong = fileProblem(file);
    if (wrong) return setMessage(wrong);
    // How long it is, read here from the file itself: a video Etsy would not take is never uploaded.
    const seconds = await readSeconds(file);
    if (seconds !== null) {
      const long = durationProblem(seconds);
      if (long) return setMessage(long);
    }
    setProgress({ sent: 0, total: file.size });
    try {
      await uploadVideo(batchId, file, { groupKey, shopId }, (sent, total) => setProgress({ sent, total }),
        pending && pending.filename === file.name ? pending : null);
      looks.current = 0;
    } catch (e) {
      setMessage(failedLine(file.name, e));
      setRetry(true);
    } finally {
      setProgress(null);
      await load();
    }
  }

  async function remove(video: ListingVideo) {
    setWorking(true);
    try {
      await api.removeVideo(video.id);
      setMessage(null);
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    } finally {
      setRemoving(false);
      setWorking(false);
      await load();
    }
  }

  /** The video a draft in that shop gets: the shop's own, else the listing's. */
  const videoFor = (draft: VideoDraft): ListingVideo | null =>
    slotOf(data?.videos ?? [], groupKey, draft.connection_id).current ?? slotOf(data?.videos ?? [], groupKey, null).current;

  async function toEtsy(draft: VideoDraft, replace: boolean) {
    const video = videoFor(draft);
    if (!video) return;
    setWorking(true);
    setMessage(null);
    try {
      await api.sendVideo(video.id, { content_id: draft.content_id, connection_id: draft.connection_id, replace, confirm: true });
      looks.current = 0;
    } catch (e: any) {
      setMessage(String(e.message ?? e));
    } finally {
      setReplacing(null);
      setWorking(false);
      await load();
    }
  }

  const several = new Set(drafts.map((d) => d.connection_id)).size > 1 || Boolean(shop);
  const label = shop ? `Video in ${shop.name}` : "Video";
  const failed = pending && pending.status === "failed" ? pending : null;
  const processing = pending && pending.status === "processing";
  const pick = () => input.current?.click();

  return (
    <div className="flex flex-col gap-1.5 text-xs" data-testid="video-slot" data-video={current ? "ready" : "none"}>
      <input ref={input} type="file" accept={ACCEPT} className="hidden" aria-label={`${label}: choose a file`} data-testid="video-input"
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) send(file);
        }} />
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="font-medium text-slate-700"><Txt>{label}</Txt></span>
        {current ? (
          <span key="current" className="flex min-w-0 items-center gap-2">
            {current.poster_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img key="poster" src={current.poster_url} alt={`Poster frame of ${current.filename}`} loading="lazy" width={56} height={56}
                className="h-14 w-14 shrink-0 rounded border border-slate-200 bg-slate-100 object-cover" data-testid="video-poster" />
            ) : (
              <span key="tile" aria-hidden="true" className="flex h-14 w-14 shrink-0 items-center justify-center rounded border border-slate-200 bg-slate-100 text-slate-600"
                data-testid="video-poster">▶</span>
            )}
            <span className="min-w-0 truncate text-slate-700" translate="no" title={current.filename} data-testid="video-duration">{videoLine(current)}</span>
          </span>
        ) : (
          <span key="none" className="text-slate-600">
            <Txt>{shop ? (listings ? "the listing's video is used here" : "none") : "none"}</Txt>
          </span>
        )}
        {progress ? (
          <span key="progress" role="status" className="text-slate-700" translate="no" data-testid="video-progress">{uploadingLine(progress.sent, progress.total)}</span>
        ) : processing ? (
          <span key="processing" role="status" className="text-slate-700" translate="no" data-testid="video-progress">Processing…</span>
        ) : !disabled && !removing ? (
          <span key="actions" className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={pick} disabled={working} data-testid="video-add">
              {current ? "Replace" : shop ? "Add one for this shop" : "Add video"}
            </button>
            {current && (
              <button key="remove" type="button" className="btn-quiet px-2.5 py-1 text-xs" onClick={() => setRemoving(true)} disabled={working} data-testid="video-remove">
                Remove
              </button>
            )}
            {!current && <span key="hint" className="text-slate-600" translate="no">{`MP4 or MOV, ${MIN_SECONDS}–${MAX_SECONDS} s`}</span>}
          </span>
        ) : null}
      </div>
      {removing && current && (
        <div key="removing" className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5" data-testid="video-remove-confirm">
          <span>Remove this video here? Drafts already on Etsy keep theirs.</span>
          <button type="button" className="btn-danger px-2.5 py-1 text-xs" onClick={() => remove(current)} disabled={working} data-testid="video-remove-yes">Remove</button>
          <button type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={() => setRemoving(false)}>Keep it</button>
        </div>
      )}
      {(message || failed) && (
        <p key="error" role="alert" className="flex flex-wrap items-center gap-x-2 gap-y-1 text-rose-700" data-testid="video-error">
          <Txt>{message ?? failed?.error ?? ""}</Txt>
          {retry && last.current ? (
            <button key="again" type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={() => last.current && send(last.current)} data-testid="video-try-again">
              Try again
            </button>
          ) : !message && failed ? (
            <button key="dismiss" type="button" className="btn-quiet px-2.5 py-1 text-xs" onClick={() => remove(failed)} disabled={working} data-testid="video-dismiss">
              Dismiss
            </button>
          ) : null}
        </p>
      )}
      {drafts.map((draft) => {
        const video = videoFor(draft);
        const state = draft.video_state;
        if (!video && state === null) return null;  // no video here and none was sent: nothing to say
        return (
          <p key={`${draft.content_id}:${draft.connection_id}`} className={"flex flex-wrap items-center gap-x-2 gap-y-1 " + (state === "failed" ? "text-rose-700" : "text-slate-700")}
            role={state === "failed" ? "alert" : "status"} data-testid="video-draft" data-state={state ?? "none"}>
            <span translate="no">{draftLine(draft, several)}</span>
            {state === "failed" && video && (
              <button key="retry" type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={() => toEtsy(draft, false)} disabled={working} data-testid="video-draft-retry">
                Try again
              </button>
            )}
            {state === null && video && (
              <button key="add" type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={() => toEtsy(draft, false)} disabled={working} data-testid="video-draft-add">
                Add it to the draft
              </button>
            )}
            {state === "attached" && video && video.id !== draft.video_id && (
              <button key="replace" type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={() => setReplacing(draft)} disabled={working} data-testid="video-draft-replace">
                Replace video on Etsy
              </button>
            )}
          </p>
        );
      })}
      {replacing && (
        <div key="replacing" role="alertdialog" aria-label="Replace video on Etsy" className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5"
          data-testid="video-confirm">
          <span translate="no">{`The video on this listing${replacing.shop_name ? ` in ${replacing.shop_name}` : ""} is taken off on Etsy and this one is put in its place.`}</span>
          <button type="button" className="btn-primary px-2.5 py-1 text-xs" onClick={() => toEtsy(replacing, true)} disabled={working} data-testid="video-confirm-yes">Replace it</button>
          <button type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={() => setReplacing(null)}>Cancel</button>
        </div>
      )}
    </div>
  );
}
