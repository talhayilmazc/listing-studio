"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, putPsdPiece } from "@/lib/api";
import {
  BUTTON, INTRO, KEPT_PRIVATE, RETRIES, RIGHTS, busy, fileProblem, percent, pieces, sameFile, sizeLine, statusLine,
} from "@/lib/psdUpload";
import type { PsdUploadState } from "@/lib/types";

/**
 * "Upload a Photoshop mockup (PSD)" (feature/psd-templates). The file goes up in pieces with real progress; after
 * a dropped connection it goes on from where the server's copy ends. Then the server turns it into a template:
 * the page shows the preview with a sample design, lets the seller choose the smart object when there are
 * several, says what is drawn differently from Photoshop, and "Use this template" adds it.
 */
export function PsdUpload({ onDone, focus, onFocusSeen }: {
  onDone: () => void;
  /** A PSD upload to show (a template photo made from one was opened). */
  focus?: string | null;
  onFocusSeen?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [rights, setRights] = useState(false);
  const [upload, setUpload] = useState<PsdUploadState | null>(null);
  const [sent, setSent] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [waiting, setWaiting] = useState<PsdUploadState[]>([]);
  const stop = useRef(false);
  const panel = useRef<HTMLElement>(null);

  // Uploads left half way or waiting to be used: picked up again on this page.
  useEffect(() => {
    api.psdUploads().then(setWaiting).catch(() => setWaiting([]));
  }, []);

  useEffect(() => {
    if (!focus) return;
    api.psdUpload(focus).then((u) => {
      setUpload(u);
      setOpen(true);
      setTimeout(() => panel.current?.scrollIntoView({ block: "start", behavior: "smooth" }), 0);
    }).catch(() => {}).finally(() => onFocusSeen?.());
  }, [focus, onFocusSeen]);

  // While it waits for the worker: look again every two seconds.
  useEffect(() => {
    if (!upload || !busy(upload.status)) return;
    const t = setTimeout(() => api.psdUpload(upload.id).then(setUpload).catch(() => {}), 2000);
    return () => clearTimeout(t);
  }, [upload]);

  const send = useCallback(async (state: PsdUploadState, chosen: File) => {
    let current = state;
    for (const piece of pieces(current.received, chosen.size, current.chunk_size)) {
      let tries = 0;
      for (;;) {
        if (stop.current) return null;
        try {
          current = await putPsdPiece(current.id, piece.offset, chosen.slice(piece.offset, piece.end),
            (n) => setSent(piece.offset + n));
          setSent(current.received);
          break;
        } catch (e) {
          if (!(e instanceof ApiError) || (e.status !== 0 && e.status !== 409 && e.status < 500) || tries >= RETRIES) throw e;
          tries += 1;
          await new Promise((r) => setTimeout(r, 1000 * tries));
          current = await api.psdUpload(current.id); // where the server's copy ends now
          if (current.received >= piece.end) break; // it arrived after all
          if (current.received !== piece.offset) return send(current, chosen); // start again from there
        }
      }
    }
    return api.psdComplete(current.id);
  }, []);

  async function start() {
    if (!file) return;
    const problem = fileProblem(file);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setWorking(true);
    stop.current = false;
    try {
      // The same file of an upload left half way: go on with it, not from the start.
      const unfinished = waiting.find((w) => w.status === "receiving" && sameFile(w, file));
      let state = unfinished ? await api.psdUpload(unfinished.id) : await api.psdOpen(file.name, file.size);
      setUpload(state);
      setSent(state.received);
      const done = await send(state, file);
      if (done) {
        state = done;
        setUpload(state);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setWorking(false);
    }
  }

  async function act(fn: () => Promise<PsdUploadState | void>) {
    setWorking(true);
    setError(null);
    try {
      const next = await fn();
      if (next) setUpload(next);
      return next;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setWorking(false);
    }
  }

  function reset() {
    stop.current = true;
    setUpload(null);
    setFile(null);
    setSent(0);
    setError(null);
    setRights(false);
  }

  const status = upload?.status;
  const progress = upload ? (status === "receiving" ? sent : upload.size) : 0;

  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="btn-secondary min-h-11" onClick={() => setOpen(true)} data-testid="psd-open">
          {BUTTON}
        </button>
        {waiting.some((w) => w.status === "ready") && (
          <span key="waiting" className="text-sm text-slate-600">
            <span>A Photoshop mockup is ready to check.</span>{" "}
            <button type="button" className="tap text-brand-700 underline" onClick={() => {
              setUpload(waiting.find((w) => w.status === "ready") ?? null);
              setOpen(true);
            }}>Show it</button>
          </span>
        )}
      </div>
    );
  }

  return (
    <section ref={panel} className="card space-y-4 p-4 sm:p-5" aria-labelledby="psd-title" data-testid="psd-panel">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <h2 id="psd-title" className="text-base font-semibold text-slate-900">{BUTTON}</h2>
          <p className="text-sm text-slate-600">{INTRO}</p>
        </div>
        <button type="button" className="btn-quiet min-h-11" onClick={() => { reset(); setOpen(false); }}>Close</button>
      </div>

      {!upload || status === "cancelled" ? (
        <div key="choose" className="space-y-3">
          <label className="block space-y-1">
            <span className="text-sm font-medium text-slate-800">Photoshop file (.psd or .psb, up to 500 MB)</span>
            <input type="file" accept=".psd,.psb,image/vnd.adobe.photoshop" className="field w-full"
              onChange={(e) => { setFile(e.target.files?.[0] ?? null); setError(null); }} data-testid="psd-file" />
          </label>
          <label className="flex items-start gap-2 text-sm text-slate-800">
            <input type="checkbox" className="mt-0.5 h-5 w-5" checked={rights} onChange={(e) => setRights(e.target.checked)}
              data-testid="psd-rights" />
            <span>{RIGHTS}</span>
          </label>
          <p className="text-xs text-slate-600">{KEPT_PRIVATE}</p>
          <button type="button" className="btn-primary min-h-11" onClick={start} disabled={!file || !rights || working}
            data-testid="psd-upload">
            Upload
          </button>
        </div>
      ) : (
        <div key="state" className="space-y-3">
          <p className="min-w-0 truncate text-sm font-medium text-slate-900" title={upload.filename} translate="no">{upload.filename}</p>
          <p role="status" translate="no" className="text-sm text-slate-700" data-testid="psd-status">
            {statusLine(upload.status, progress, upload.size)}
          </p>
          {(status === "receiving" || busy(upload.status)) && (
            <div key="bar" className="progress" aria-hidden="true">
              <div className="progress-fill" style={{ width: `${status === "receiving" ? percent(sent, upload.size) : 100}%` }} />
            </div>
          )}
          {status === "receiving" && !working && (
            <div key="resume" className="space-y-2 text-sm text-slate-700">
              <p>
                <span>{`Stopped at ${sizeLine(upload.received, upload.size)}. Choose the same file again to go on from there.`}</span>
              </p>
              <input type="file" accept=".psd,.psb" className="field w-full"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) { setFile(f); setRights(true); } }} />
              <button type="button" className="btn-primary min-h-11" disabled={!file || !sameFile(upload, file ?? { name: "", size: -1 })}
                onClick={start}>Go on</button>
            </div>
          )}
          {status === "failed" && upload.error && (
            <p key="failed" role="alert" className="rounded-md border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{upload.error}</p>
          )}
          {(status === "ready" || status === "used") && upload.preview_url && (
            <div key="ready" className="space-y-3">
              <figure className="space-y-1">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={`${upload.preview_url}&size=medium`} alt="The template with a sample design placed" loading="lazy"
                  className="w-full max-w-xl rounded-md border border-slate-200 bg-slate-50"
                  width={upload.width ?? undefined} height={upload.height ?? undefined} data-testid="psd-preview" />
                <figcaption className="text-xs text-slate-600">A sample design, placed as your designs will be.</figcaption>
              </figure>
              {upload.options.filter((o) => o.usable).length > 1 && (
                <fieldset key="choice" className="space-y-1" disabled={!upload.can_choose || working || status === "used"}>
                  <legend className="text-sm font-medium text-slate-800">Which smart object gets the design?</legend>
                  {upload.options.filter((o) => o.usable).map((o) => (
                    <label key={o.index} className="flex min-h-11 items-center gap-2 text-sm text-slate-800">
                      <input type="radio" name="psd-choice" checked={upload.choice === o.index}
                        onChange={() => act(() => api.psdChoose(upload.id, o.index))} data-testid="psd-choice" />
                      <span className="min-w-0 truncate" translate="no">{o.name}</span>
                    </label>
                  ))}
                  {!upload.can_choose && status === "ready" && (
                    <p key="not-kept" className="text-xs text-slate-600">We keep the file for a day; to choose again later, upload it again.</p>
                  )}
                </fieldset>
              )}
              {upload.notes.length > 0 && (
                <ul key="notes" className="space-y-1 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900" data-testid="psd-notes">
                  {upload.notes.map((n) => <li key={n}>{n}</li>)}
                </ul>
              )}
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {status === "ready" && (
              <button key="use" type="button" className="btn-primary min-h-11" disabled={working}
                onClick={async () => { if (await act(() => api.psdUse(upload.id))) onDone(); }} data-testid="psd-use">
                Use this template
              </button>
            )}
            {status === "failed" && (
              <button key="again" type="button" className="btn-primary min-h-11" onClick={reset}>Choose another file</button>
            )}
            {status === "used" && (
              <button key="another" type="button" className="btn-secondary min-h-11" onClick={reset}>Upload another</button>
            )}
            {status !== "used" && status !== "converting" && (
              <button key="cancel" type="button" className="btn-secondary min-h-11" disabled={working}
                onClick={async () => { stop.current = true; await act(() => api.psdCancel(upload.id)); reset(); onDone(); }}
                data-testid="psd-cancel">
                Cancel
              </button>
            )}
          </div>
        </div>
      )}
      {error && <p key="error" role="alert" className="text-sm text-rose-700">{error}</p>}
    </section>
  );
}
