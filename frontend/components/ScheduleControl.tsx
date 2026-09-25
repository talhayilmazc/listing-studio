"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { fromLocalInput, nextHour, scheduleLabel, toLocalInput } from "@/lib/schedule";
import type { Publication } from "@/lib/types";

/**
 * "Schedule" beside "Publish now" on an approved listing's draft (v6 §G). The
 * seller picks a time in their own time zone; it goes live then. Setting the
 * time is the seller's confirmation, so only an approved listing offers it.
 */
export function ScheduleControl({
  contentId,
  publication,
  approved,
  onChange,
}: {
  contentId: string;
  publication: Publication;
  approved: boolean;
  onChange: (patch: Partial<Publication>) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const at = publication.scheduled_for ? new Date(publication.scheduled_for) : null;
  const status = publication.schedule_status;
  const pending = at !== null && (status === "scheduled" || status === "not_published" || !status);

  function open() {
    setValue(toLocalInput(at && at > new Date() ? at : nextHour()));
    setError(null);
    setEditing(true);
  }

  async function save() {
    const when = fromLocalInput(value);
    if (!when) return setError("Choose a date and time.");
    setBusy(true);
    setError(null);
    try {
      const res = await api.schedule([
        { content_id: contentId, connection_id: publication.connection_id, run_at: when.toISOString() },
      ]);
      if (res.skipped.length) {
        setError(res.skipped[0].reason);
      } else {
        const [s] = res.scheduled;
        onChange({ scheduled_for: s.scheduled_for, schedule_status: s.status, schedule_note: s.note });
        setEditing(false);
      }
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    setBusy(true);
    setError(null);
    try {
      await api.cancelSchedule(contentId, publication.connection_id);
      onChange({ scheduled_for: null, schedule_status: null, schedule_note: null });
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  if (publication.state === "active" || !approved) return null;

  if (editing) {
    return (
      <span className="flex w-full flex-wrap items-center gap-2 text-xs">
        <label className="text-slate-500" htmlFor={`sched-${contentId}-${publication.connection_id}`}>
          Go live at
        </label>
        <input
          id={`sched-${contentId}-${publication.connection_id}`}
          type="datetime-local"
          className="field w-auto py-1 text-xs"
          value={value}
          min={toLocalInput(new Date())}
          onChange={(e) => setValue(e.target.value)}
        />
        <button type="button" className="btn-primary px-2.5 py-1 text-xs" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button type="button" className="text-slate-500 hover:text-slate-800" onClick={() => setEditing(false)}>
          Cancel
        </button>
        <span className="text-slate-400">your time zone</span>
        {error && <span className="w-full text-rose-700">{error}</span>}
      </span>
    );
  }

  if (at) {
    return (
      <span className="flex w-full flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-600">
        <span>
          <span className="font-medium text-slate-800">{scheduleLabel(status)}</span>
          {" · "}
          {at.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
        </span>
        {publication.schedule_note && <span className="text-amber-800">{publication.schedule_note}</span>}
        {pending && (
          <>
            <button type="button" className="underline hover:text-slate-900" onClick={open} disabled={busy}>
              change
            </button>
            <button type="button" className="underline hover:text-slate-900" onClick={cancel} disabled={busy}>
              {busy ? "cancelling…" : "cancel"}
            </button>
          </>
        )}
        {error && <span className="w-full text-rose-700">{error}</span>}
      </span>
    );
  }

  return (
    <button
      type="button"
      className="btn-secondary px-2.5 py-1 text-xs"
      onClick={open}
      title="Choose when this draft goes live"
    >
      Schedule
    </button>
  );
}
