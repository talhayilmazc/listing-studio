"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { formatWhen, nextHour, scheduleLabel, toWallClock, wallToInstant } from "@/lib/schedule";
import { useSession } from "./SessionProvider";
import { ZoneNote } from "./ZoneNote";
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
  const { timeZone } = useSession();
  const at = publication.scheduled_for ? new Date(publication.scheduled_for) : null;
  const status = publication.schedule_status;
  const pending = at !== null && (status === "scheduled" || status === "not_published" || !status);

  function open() {
    // The input holds the account zone's wall clock, whatever this computer's zone.
    setValue(at && at > new Date() ? toWallClock(at, timeZone) : nextHour(timeZone));
    setError(null);
    setEditing(true);
  }

  async function save() {
    if (!value) return setError("Choose a date and time.");
    if (!wallToInstant(value, timeZone)) return setError("That time doesn't exist in your time zone (the clocks skip it).");
    setBusy(true);
    setError(null);
    try {
      // Sent as the wall-clock time; the server converts it to UTC once.
      const res = await api.schedule([
        { content_id: contentId, connection_id: publication.connection_id, local_time: value },
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
          min={toWallClock(new Date(), timeZone)}
          onChange={(e) => setValue(e.target.value)}
        />
        <button type="button" className="btn-primary px-2.5 py-1 text-xs" onClick={save} disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
        <button type="button" className="text-slate-500 hover:text-slate-800" onClick={() => setEditing(false)}>
          Cancel
        </button>
        <ZoneNote />
        {error && <span key="span-97-8" className="w-full text-rose-700">{error}</span>}
      </span>
    );
  }

  if (at) {
    return (
      <span className="flex w-full flex-wrap items-center gap-x-2 gap-y-1 text-xs text-slate-600">
        <span>
          <span className="font-medium text-slate-800">{scheduleLabel(status)}</span>
          <span>{" · "}
          <span translate="no">{formatWhen(at, timeZone)}</span></span>
        </span>
        {publication.schedule_note && <span key="span-110-8" className="text-amber-800">{publication.schedule_note}</span>}
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
        {error && <span key="span-121-8" className="w-full text-rose-700">{error}</span>}
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
