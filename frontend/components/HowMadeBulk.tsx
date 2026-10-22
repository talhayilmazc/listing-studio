"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { announceProfilesChanged } from "@/lib/shopSetup";
import { NOT_SENT, problem, shortWords } from "@/lib/howMade";
import { HowMadeFields } from "./HowMadePicker";

/**
 * How it's made on the Shops table (lib/howMade.ts): the item's answers, the same in every
 * shop. Saved on this profile, or "Apply to all profiles…" gives every confirmed profile the
 * same answers after one confirmation.
 */
export function HowMadeBulk({
  profileId,
  production,
  tools,
  onChanged,
}: {
  profileId: string;
  production: string | null | undefined;
  tools: string[] | null | undefined;
  onChanged: () => void;
}) {
  const [p, setP] = useState<string | null>(production ?? null);
  const [t, setT] = useState<string[]>(tools ?? []);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const why = problem(p, t);

  async function save(all: boolean) {
    if (why || !p) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const { updated } = await api.howMadeForProfiles(p, t, all ? null : [profileId]);
      setMessage(all ? `${updated} profile${updated === 1 ? "" : "s"} now say: ${shortWords(p, t)}.` : `Saved: ${shortWords(p, t)}.`);
      setConfirming(false);
      onChanged();
      announceProfilesChanged();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-md border border-slate-200 p-3" data-testid="how-made-bulk">
      <h3 className="text-xs font-semibold text-slate-900">How it&apos;s made (the same in every shop)</h3>
      <p className="mb-2 mt-0.5 text-[11px] text-slate-600">
        {NOT_SENT}
      </p>
      <HowMadeFields production={p} tools={t} onChange={(np, nt) => { setP(np); setT(nt); }} name={`how-made-bulk-${profileId}`} />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button type="button" className="btn-primary px-3 py-1.5 text-xs" disabled={!!why || busy} onClick={() => save(false)}>
          Save for this profile
        </button>
        {confirming ? (
          <span key="confirm" className="flex flex-wrap items-center gap-2">
            <span>Give every confirmed profile these answers?</span>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy} onClick={() => save(true)} data-testid="how-made-all-yes">
              Yes, all profiles
            </button>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </span>
        ) : (
          <button key="all" type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={!!why || busy}
            onClick={() => setConfirming(true)} data-testid="how-made-all">
            Apply to all profiles…
          </button>
        )}
        {why && <span key="why" className="text-slate-600">{why}</span>}
      </div>
      {message && <p key="msg" role="status" translate="no" className="mt-1 text-slate-700"><span>{message}</span></p>}
      {error && <p key="err" role="alert" className="mt-1 text-rose-700"><span>{error}</span></p>}
    </section>
  );
}
