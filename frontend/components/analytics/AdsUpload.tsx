"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { money } from "@/lib/analytics";
import { relativeTime } from "@/lib/format";
import type { AdsField, AdsImportResult, AdsPreview, AdsUpload as Upload } from "@/lib/types";

const FIELD_LABEL: Record<AdsField, string> = {
  listing_id: "Listing number",
  title: "Listing title",
  date: "Date",
  spend: "Spend",
  orders: "Orders",
  revenue: "Revenue",
  views: "Views",
};

const FIELD_HELP: Record<AdsField, string> = {
  listing_id: "matched to your listings by number",
  title: "used when there is no number; must match the title exactly",
  date: "one row per day; leave empty if the report covers a period",
  spend: "required",
  orders: "orders the ads brought in",
  revenue: "revenue the ads brought in",
  views: "views of the ad; a new listing with views is judged from day 30",
};

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/**
 * Upload the Etsy Ads report the seller downloaded from Shop Manager: pick the
 * file, check which column is which, then keep the spend matched to their own
 * listings. The file itself is read and discarded, never stored.
 */
export function AdsUpload({ shopId, currency, onImported }: { shopId: string | null; currency: string | null; onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<AdsPreview | null>(null);
  const [mapping, setMapping] = useState<Record<AdsField, string | null> | null>(null);
  const [start, setStart] = useState(() => isoDay(new Date(Date.now() - 29 * 86400000)));
  const [end, setEnd] = useState(() => isoDay(new Date()));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AdsImportResult | null>(null);
  const [uploads, setUploads] = useState<Upload[] | null>(null);

  const loadUploads = useCallback(() => {
    api.adsUploads(shopId).then(setUploads).catch(() => setUploads([]));
  }, [shopId]);
  useEffect(loadUploads, [loadUploads]);

  const choose = async (f: File | null) => {
    setFile(f);
    setPreview(null);
    setMapping(null);
    setResult(null);
    setError(null);
    if (!f) return;
    setBusy(true);
    try {
      const p = await api.adsPreview(f);
      setPreview(p);
      setMapping(p.mapping);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The file could not be read.");
    } finally {
      setBusy(false);
    }
  };

  const importNow = async () => {
    if (!file || !mapping) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.adsImport(shopId, file, mapping, mapping.date ? null : { start, end });
      setResult(r);
      loadUploads();
      if (r.matched) onImported();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The report could not be imported.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm("Remove this upload's ad spend from your figures?")) return;
    await api.deleteAdsUpload(id).catch(() => {});
    loadUploads();
    onImported();
  };

  const col = (h: string | null) => (h && preview ? preview.headers.indexOf(h) : -1);
  const ready = mapping && mapping.spend && (mapping.listing_id || mapping.title) && (mapping.date || (start && end && start <= end));

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
      <div className="space-y-4">
        <section className="card space-y-3 p-5 text-sm text-slate-600">
          <h2 className="font-semibold text-slate-800">Etsy Ads report</h2>
          <p>
            Etsy doesn&apos;t make ad figures available to apps, so upload the report yourself: in Shop Manager
            go to <b>Marketing → Etsy Ads</b>, choose the period, and download the listings report as CSV.
            The file is read once to match its rows to your own listings and is not kept; only each
            listing&apos;s spend, orders and revenue for the period are.
          </p>
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={(e) => choose(e.target.files?.[0] ?? null)}
            className="block text-sm file:mr-3 file:rounded-lg file:border file:border-slate-300 file:bg-white file:px-3 file:py-1.5 file:text-sm hover:file:bg-slate-50"
          />
          {busy && !preview && <p className="text-slate-400">Reading the file…</p>}
          {error && <p className="text-rose-700">{error}</p>}
        </section>

        {result && (
          <section className="card space-y-2 p-5 text-sm">
            <p className="text-slate-800">
              <b className="tabular-nums">{result.matched}</b> row{result.matched === 1 ? "" : "s"} matched to your listings,{" "}
              {money(result.spend, currency)} of ad spend.
              {result.replaced > 0 && ` Replaced ${result.replaced} earlier row${result.replaced === 1 ? "" : "s"} for the same period.`}
              {result.skipped > 0 && ` ${result.skipped} row${result.skipped === 1 ? "" : "s"} without spend or totals were skipped.`}
            </p>
            {result.unmatched_total > 0 && (
              <div>
                <p className="text-amber-800">
                  {result.unmatched_total} row{result.unmatched_total === 1 ? "" : "s"} didn&apos;t match any of your listings
                  {result.titles_refreshing && " (your listing titles are being refreshed from Etsy; upload again in a minute to match by title)"}:
                </p>
                <ul className="mt-1 max-h-48 overflow-y-auto text-xs text-slate-600">
                  {result.unmatched.map((u) => (
                    <li key={u.line}>
                      line {u.line}: {u.label} — {u.why}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        )}
        {preview && mapping && (
          <section className="card space-y-4 p-5">
            <div>
              <h2 className="text-sm font-semibold text-slate-800">Which column is which?</h2>
              <p className="text-xs text-slate-500">
                {preview.rows.toLocaleString()} rows. Filled in from the column names; check them against the rows below.
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              {(Object.keys(FIELD_LABEL) as AdsField[]).map((f) => (
                <label key={f} className="block">
                  <span className="label">{FIELD_LABEL[f]}</span>
                  <select
                    className="field py-1.5"
                    value={mapping[f] ?? ""}
                    onChange={(e) => setMapping({ ...mapping, [f]: e.target.value || null })}
                  >
                    <option value="">— none —</option>
                    {preview.headers.map((h) => (
                      <option key={h} value={h}>
                        {h}
                      </option>
                    ))}
                  </select>
                  <span className="mt-0.5 block text-xs text-slate-400">{FIELD_HELP[f]}</span>
                </label>
              ))}
            </div>
            {!mapping.date && (
              <div className="flex flex-wrap items-end gap-3">
                <label>
                  <span className="label">Report covers from</span>
                  <input type="date" className="field py-1.5" value={start} onChange={(e) => setStart(e.target.value)} />
                </label>
                <label>
                  <span className="label">to</span>
                  <input type="date" className="field py-1.5" value={end} onChange={(e) => setEnd(e.target.value)} />
                </label>
                <span className="pb-2 text-xs text-slate-500">the dates you chose in Etsy Ads before downloading</span>
              </div>
            )}

            <div className="overflow-x-auto rounded-lg border border-slate-100">
              <table className="w-full text-xs">
                <thead className="bg-slate-50 text-left text-slate-500">
                  <tr>
                    {(Object.keys(FIELD_LABEL) as AdsField[])
                      .filter((f) => mapping[f])
                      .map((f) => (
                        <th key={f} className="px-2 py-1.5 font-medium">
                          {FIELD_LABEL[f]}
                        </th>
                      ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {preview.sample.map((row, i) => (
                    <tr key={i}>
                      {(Object.keys(FIELD_LABEL) as AdsField[])
                        .filter((f) => mapping[f])
                        .map((f) => (
                          <td key={f} className="max-w-[16rem] truncate px-2 py-1.5 text-slate-700">
                            {row[col(mapping[f])] ?? ""}
                          </td>
                        ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <button type="button" className="btn-primary" disabled={!ready || busy} onClick={importNow}>
              {busy ? "Matching…" : "Match to my listings"}
            </button>
          </section>
        )}

      </div>

      <aside className="card h-fit p-5">
        <h2 className="text-sm font-semibold text-slate-800">Uploaded reports</h2>
        {uploads === null ? (
          <p className="mt-2 text-sm text-slate-400">Loading…</p>
        ) : uploads.length === 0 ? (
          <p className="mt-2 text-sm text-slate-400">None yet.</p>
        ) : (
          <ul className="mt-2 divide-y divide-slate-100 text-sm">
            {uploads.map((u) => (
              <li key={u.upload_id} className="flex items-start justify-between gap-2 py-2">
                <div>
                  <p className="text-slate-800">
                    {u.period_start} → {u.period_end}
                  </p>
                  <p className="text-xs text-slate-500">
                    {u.listings} listing{u.listings === 1 ? "" : "s"} · {money(u.spend, currency)} · uploaded {relativeTime(u.created_at)}
                  </p>
                </div>
                <button type="button" className="text-xs text-slate-500 underline hover:text-rose-700" onClick={() => remove(u.upload_id)}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs text-slate-400">Kept 13 months, then deleted; deleted at once if you disconnect the shop.</p>
      </aside>
    </div>
  );
}
