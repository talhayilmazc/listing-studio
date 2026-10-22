"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/**
 * Two operator settings (Admin → Usage): how many months of analytics history
 * are kept, and whether new shop connections are paused. Both are audited and
 * take effect at once (retention at its next run, every 15 minutes).
 */
export function OperatorSettings({ onError }: { onError: (message: string) => void }) {
  const [months, setMonths] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [defaults, setDefaults] = useState(24);
  const [paused, setPaused] = useState<boolean | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    api.admin
      .analyticsRetention()
      .then((r) => {
        setMonths(r.months);
        setDraft(String(r.months));
        setDefaults(r.default_months);
      })
      .catch((e) => onError(String(e.message ?? e)));
    api.admin
      .connectionsPaused()
      .then((r) => {
        setPaused(r.on);
        setNotice(r.notice);
      })
      .catch((e) => onError(String(e.message ?? e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const wanted = Number(draft);
  const valid = Number.isInteger(wanted) && wanted >= 1 && wanted <= 60;
  const shorter = months !== null && valid && wanted < months;

  async function saveMonths() {
    setBusy(true);
    setSaved(null);
    try {
      const r = await api.admin.setAnalyticsRetention(wanted);
      setMonths(r.months);
      setSaved(`Analytics history is kept ${r.months} months. The Privacy Policy shows this figure.`);
    } catch (e: any) {
      onError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function togglePaused(on: boolean) {
    setBusy(true);
    setSaved(null);
    try {
      const r = await api.admin.setConnectionsPaused(on);
      setPaused(r.on);
      setSaved(r.on ? "New shop connections are paused. The Shops & groups page shows the notice." : "New shops can be connected again.");
    } catch (e: any) {
      onError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card space-y-5 p-5" aria-labelledby="operator-title" data-testid="operator-settings">
      <h2 id="operator-title" className="font-display text-2xl text-slate-900">Etsy data and shops</h2>

      <div className="space-y-2">
        <h3 className="text-sm font-semibold text-slate-800">Analytics history</h3>
        <p className="text-sm text-slate-500">
          How long sales totals and order lines, fee and ad totals, imported statements, and views and favourites
          are kept. Older rows are deleted by the retention job, every 15 minutes.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="label">Months kept</span>
            <input type="number" min={1} max={60} className="field w-28" value={draft} onChange={(e) => setDraft(e.target.value)}
              disabled={months === null} aria-label="Months of analytics history kept" />
          </label>
          <button type="button" className="btn-primary" disabled={busy || !valid || wanted === months} onClick={saveMonths}>
            Save
          </button>
          <span className="pb-2 text-xs text-slate-500" translate="no">{`Default ${defaults}. Between 1 and 60.`}</span>
        </div>
        {shorter && (
          <p key="shorter" role="alert" className="text-xs text-amber-800">
            Shorter than now: history older than this is deleted at the next run and does not come back.
          </p>
        )}
      </div>

      <div className="space-y-2 border-t border-slate-100 pt-4">
        <h3 className="text-sm font-semibold text-slate-800">New shop connections</h3>
        <label className="flex min-h-[2.75rem] cursor-pointer items-start gap-2.5 text-sm text-slate-700">
          <input type="checkbox" className="mt-0.5 h-4 w-4" checked={paused ?? false} disabled={busy || paused === null}
            onChange={(e) => togglePaused(e.target.checked)} data-testid="pause-connections" />
          <span>
            <span>Pause new shop connections</span>
            <span className="block text-xs text-slate-500">
              Shops already connected keep working and can reconnect. While it is on, the Shops &amp; groups page says:
            </span>
            <span className="mt-1 block rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-900">{notice}</span>
          </span>
        </label>
      </div>

      {saved && <p key="saved" role="status" className="text-sm text-emerald-700">{saved}</p>}
    </section>
  );
}
