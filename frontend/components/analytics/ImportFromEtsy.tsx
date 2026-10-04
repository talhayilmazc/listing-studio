"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { money } from "@/lib/analytics";
import { relativeTime } from "@/lib/format";
import { ADS_LAG, IMPORT_CARDS, IMPORT_PRIVACY, LABELS, MONTH_STATE_WORDS, STATUS_WORDS, refundsTooltip, type ImportCard } from "@/lib/importSteps";
import type { ImportMonth, ImportStatus } from "@/lib/types";

import { Txt } from "@/components/Txt";
/**
 * Import from Etsy: the monthly statement and the Etsy Ads report.
 *
 * After an upload the month's report shows what was read (rows, dates, a total
 * per category) beside the app's own calculation, and every difference with its
 * reason. A figure the app does not have is shown as missing, never as zero.
 */

const monthName = (iso: string) =>
  new Date(iso + "T12:00:00Z").toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" });
const dayName = (iso: string) =>
  new Date(iso + "T12:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const plain = (minor: number) => (Math.abs(minor) / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function ImportFromEtsy({ shopId, onImported }: { shopId: string | null; onImported: () => void }) {
  const [status, setStatus] = useState<ImportStatus | null>(null);
  const [months, setMonths] = useState<ImportMonth[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = useCallback(() => {
    api.importStatus(shopId).then(setStatus).catch(() => setStatus(null));
  }, [shopId]);

  useEffect(() => {
    setMonths([]);
    setError(null);
    loadStatus();
  }, [loadStatus]);

  async function upload(kind: ImportCard["kind"], file: File) {
    setBusy(kind);
    setError(null);
    try {
      const result = kind === "statement" ? [await api.importStatement(shopId, file)] : await api.importAds(shopId, file);
      setMonths(result);
      loadStatus();
      onImported();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(null);
    }
  }

  async function open(month: string) {
    setBusy(month);
    setError(null);
    try {
      setMonths([await api.importMonth(shopId, month.slice(0, 7))]);
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(null);
    }
  }

  async function remove(month: string) {
    setBusy(month);
    try {
      await api.deleteImportMonth(shopId, month.slice(0, 7));
      setMonths((cur) => cur.filter((m) => m.month !== month));
      loadStatus();
      onImported();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(null);
    }
  }

  const currency = months[0]?.statement?.currency ?? status?.currency ?? "USD";

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {IMPORT_CARDS.map((card) => (
          <UploadCard key={card.kind} card={card} busy={busy === card.kind} disabled={busy !== null} onFile={(f) => upload(card.kind, f)} />
        ))}
      </div>
      <p className="text-xs text-slate-500">{IMPORT_PRIVACY}</p>

      {error && (
        <p key="error" role="alert" className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">
          <span>{error}</span>
        </p>
      )}

      {months.map((m) => (
        <MonthReport key={m.month} report={m} currency={currency} />
      ))}

      <MonthList status={status} busy={busy} onOpen={open} onRemove={remove} currency={currency} />
    </div>
  );
}

function UploadCard({ card, busy, disabled, onFile }: { card: ImportCard; busy: boolean; disabled: boolean; onFile: (f: File) => void }) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <section className="card min-w-0 p-5">
      <h2 className="text-base font-semibold text-slate-900">{card.title}</h2>
      <p className="mt-1 text-sm text-slate-600">{card.what}</p>
      <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm text-slate-700">
        {card.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      <p className="mt-3 text-xs text-slate-500">{card.note}</p>
      <input
        ref={input}
        type="file"
        accept=".csv,text/csv"
        className="sr-only"
        aria-label={card.button}
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = "";
          if (file) onFile(file);
        }}
      />
      <button type="button" className="btn-primary mt-4" disabled={disabled} onClick={() => input.current?.click()}>
        <span>{busy ? "Reading…" : card.button}</span>
      </button>
    </section>
  );
}

const TH = "px-3 py-1.5 text-right font-medium whitespace-nowrap";
const TD = "px-3 py-1.5 text-right tabular-nums whitespace-nowrap";

function MonthReport({ report, currency }: { report: ImportMonth; currency: string }) {
  const st = report.statement;
  const m = (minor: number | null | undefined) => money(minor, currency);
  return (
    <section className="card min-w-0 p-5" translate="no" aria-label={`Import for ${monthName(report.month)}`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-2xl text-slate-900">{monthName(report.month)}</h2>
        {report.shop_name && <span key="shop" className="text-xs text-slate-500">{report.shop_name}</span>}
      </div>

      {st ? (
        <div key="statement" className="mt-4">
          <h3 className="text-sm font-medium text-slate-900">What was read from the statement</h3>
          <p className="mt-0.5 text-xs text-slate-500">
            <span>
              <span><span>{st.rows.toLocaleString("en-US")}</span> rows · <span>{dayName(st.first_day)}</span> to <span>{dayName(st.last_day)}</span> · every row is in one category, and they
              add up to the statement&apos;s net total exactly</span>
            </span>
          </p>
          <div className="mt-2 overflow-x-auto">
            <table className="text-left text-xs max-sm:w-max sm:w-full">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500">
                  <th className="py-1.5 pr-3 font-medium">Category</th>
                  <th className={TH}>Total</th>
                  <th className={TH}>Rows</th>
                </tr>
              </thead>
              <tbody>
                {st.categories.map((c) => (
                  <tr key={c.key} className="border-b border-slate-100">
                    <td className="py-1.5 pr-3 text-slate-800">{c.label}</td>
                    <td className={TD + " text-slate-900"}>{m(c.minor)}</td>
                    <td className={TD + " text-slate-600"}>{c.rows.toLocaleString("en-US")}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-slate-300 font-medium text-slate-900">
                  <td className="py-1.5 pr-3">Net total</td>
                  <td className={TD}>{m(st.net_minor)}</td>
                  <td className={TD}>{st.rows.toLocaleString("en-US")}</td>
                </tr>
              </tfoot>
            </table>
          </div>

          <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 lg:grid-cols-4">
            <Figure label={LABELS.revenue} value={m(st.revenue_minor)} note="Sales less the sales tax and state fees buyers paid. Exact." />
            <Figure
              label={LABELS.refunds}
              value={m(st.refunds_net_minor)}
              note={st.refunded_minor ? refundsTooltip(plain(st.refunded_minor), plain(st.tax_returned_minor)) : "No refunds this month."}
            />
            <Figure label="Etsy fees" value={m(st.etsy_fees_minor)} note="Transaction, processing and listing fees, tax on them, less credits. Exact." />
            <Figure label="Deposits to your bank" value={m(st.deposits_minor)} note={`${st.deposits.length} transfer${st.deposits.length === 1 ? "" : "s"}. Not income and not a cost: not in the totals.`} />
          </dl>

          {st.unrecognised.length > 0 && (
            <p key="unrecognised" className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              <span>
                <span><span>{st.unrecognised.length}</span><span> row</span><Txt>{st.unrecognised.length === 1 ? "" : "s"}</Txt><span> had a title this app has no rule for. They are counted in the
                net total under &quot;Other&quot;: </span><span>{st.unrecognised.slice(0, 6).map((u) => `${u.type}: ${u.title} (${m(u.minor)})`).join("; ")}</span><span>.</span></span>
              </span>
            </p>
          )}
          {st.notes.length > 0 && (
            <p key="notes" className="mt-2 text-xs text-amber-800">
              <span>{st.notes.slice(0, 5).join(" · ")}</span>
            </p>
          )}
        </div>
      ) : (
        <p key="nostatement" className="mt-4 rounded-lg border border-slate-200 bg-stone-50 px-3 py-2 text-sm text-slate-600">
          The statement for this month is not imported yet.
        </p>
      )}

      <AdsBlock report={report} currency={currency} />

      {report.comparison.length > 0 && (
        <div key="comparison" className="mt-5">
          <h3 className="text-sm font-medium text-slate-900">Beside our own calculation</h3>
          <p className="mt-0.5 text-xs text-slate-500">
            The statement is the exact figure. &quot;Ours&quot; is what the app had read from Etsy by itself; where it has nothing, it says so.
          </p>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[46rem] text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500">
                  <th className="py-1.5 pr-3 font-medium">Line</th>
                  <th className={TH}>Statement</th>
                  <th className={TH}>Ours</th>
                  <th className={TH}>Difference</th>
                  <th className="px-3 py-1.5 font-medium">Why</th>
                </tr>
              </thead>
              <tbody>
                {report.comparison.map((c) => (
                  <tr key={c.key} className="border-b border-slate-100 align-top">
                    <td className="py-1.5 pr-3 text-slate-800">{c.label}</td>
                    <td className={TD + " text-slate-900"}>{m(c.statement_minor)}</td>
                    <td className={TD}>
                      {c.ours_minor === null ? <span className="text-slate-400">no figure</span> : <span title={c.ours_source ?? undefined}>{m(c.ours_minor)}</span>}
                    </td>
                    <td className={TD}>{c.difference_minor === null ? "—" : c.difference_minor === 0 ? "none" : m(c.difference_minor)}</td>
                    <td className="px-3 py-1.5 text-slate-600">
                      <StatusChip status={c.status} />
                      <span className="ml-1.5">{c.reason}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {report.orders && (
        <div key="orders" className="mt-5">
          <h3 className="text-sm font-medium text-slate-900">Orders tied to listings</h3>
          <p className="mt-1 text-xs text-slate-600">
            <span>
              <span><span>{report.orders.matched.toLocaleString("en-US")}</span><span> of </span><span>{report.orders.orders.toLocaleString("en-US")}</span><span> orders on the statement are in the sales
              read, by order number. </span><span>{report.orders.exact.toLocaleString("en-US")}</span><span> agree to the cent (items plus shipping against what the buyer paid
              less tax)</span><Txt>{report.orders.differing ? `; ${report.orders.differing} differ by ${m(report.orders.difference_minor)} in all` : ""}</Txt><span>. </span><span>{report.orders.note}</span></span>
            </span>
          </p>
          {report.listing_fees && (
            <p key="fees" className="mt-1 text-xs text-slate-600">
              <span>
                <span>Listing fees: <span>{report.listing_fees.fees.toLocaleString("en-US")}</span> on <span>{report.listing_fees.listings.toLocaleString("en-US")}</span> listings,
                tied by the listing number Etsy gives (<span>{m(report.listing_fees.minor + report.listing_fees.credits_minor)}</span>).</span>
              </span>
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function AdsBlock({ report, currency }: { report: ImportMonth; currency: string }) {
  const a = report.ads;
  const m = (minor: number | null | undefined) => money(minor, currency);
  const days = [...a.billed_from_before, ...a.billed_differently, ...a.billed_later];
  return (
    <div className="mt-5">
      <h3 className="text-sm font-medium text-slate-900">Etsy Ads (the whole shop)</h3>
      <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        <Figure
          label={LABELS.charged}
          value={a.charged_minor === null ? "not imported" : m(a.charged_minor)}
          note={a.charged_minor === null ? "From the statement. Import it to see what Etsy billed." : `From the statement: ${a.charge_days} daily charges. Exact.`}
          tooltip={ADS_LAG}
        />
        <Figure
          label={LABELS.spent}
          value={a.reported_minor === null ? "not imported" : m(a.reported_minor)}
          note={
            a.reported_minor === null
              ? "From the Ads report. Import it to see the spend for this month's clicks."
              : `From the Ads report: ${a.report_days} of ${a.month_days} days, ${(a.clicks ?? 0).toLocaleString("en-US")} clicks, ${(a.report_orders ?? 0).toLocaleString("en-US")} orders, ${m(a.report_revenue_minor)} in sales. Exact.`
          }
          tooltip={ADS_LAG}
        />
      </dl>
      <p className="mt-2 text-xs text-slate-500">{a.note}</p>
      {a.exact !== null && (
        <div key="days" className="mt-2">
          <p className="text-xs text-slate-600">
            <span>
              <span><Txt>{a.matched_days}</Txt><span> days match exactly.</span>{" "}
              <span>{days.length === 0 ? "The two figures are the same." : a.exact ? "The days below account for the whole gap, to the cent." : "The days below do not account for the whole gap."}</span></span>
            </span>
          </p>
          {days.length > 0 && (
            <ul key="list" className="mt-1 space-y-0.5 text-xs text-slate-600">
              {days.map((d) => (
                <li key={d.day}>
                  <span className="font-medium text-slate-800">{dayName(d.day)}</span>
                  <span>
                    <span>: report <span>{d.reported_minor === null ? "—" : m(d.reported_minor)}</span>, charged <span>{d.charged_minor === null ? "—" : m(d.charged_minor)}</span>. <span>{d.reason.charAt(0).toUpperCase() + d.reason.slice(1)}</span>.</span>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function Figure({ label, value, note, tooltip }: { label: string; value: string; note: string; tooltip?: string }) {
  return (
    <div className="min-w-0" title={tooltip}>
      <dt className="text-xs text-slate-500">
        <span>{label}</span>
        {tooltip && <span key="tip" className="ml-1 cursor-help text-slate-400" aria-label={tooltip}>ⓘ</span>}
      </dt>
      <dd className="mt-1 font-display text-2xl leading-none tabular-nums text-slate-900">{value}</dd>
      <p className="mt-1 text-[11px] text-slate-500">{note}</p>
      {tooltip && <p key="tiptext" className="mt-1 text-[11px] text-slate-500 sm:hidden">{tooltip}</p>}
    </div>
  );
}

function StatusChip({ status }: { status: string }) {
  const tone =
    status === "match" ? "border-emerald-200 bg-emerald-50 text-emerald-800"
    : status === "unexplained" ? "border-amber-200 bg-amber-50 text-amber-800"
    : "border-slate-200 bg-slate-50 text-slate-600";
  return <span className={"inline-block whitespace-nowrap rounded border px-1.5 py-0.5 text-[10px] font-medium " + tone}>{STATUS_WORDS[status] ?? status}</span>;
}

function MonthList({
  status, busy, onOpen, onRemove, currency,
}: { status: ImportStatus | null; busy: string | null; onOpen: (m: string) => void; onRemove: (m: string) => void; currency: string }) {
  const [confirming, setConfirming] = useState<string | null>(null);
  if (!status) return null;
  return (
    <section className="card min-w-0 p-5" aria-labelledby="import-months">
      <h2 id="import-months" className="text-base font-semibold text-slate-900">Imported months</h2>
      <p className="mt-1 text-xs text-slate-500">
        A month with its statement shows exact figures. Without it, figures come from what the app reads from Etsy by itself, or are missing.
      </p>
      <ul className="mt-3 divide-y divide-slate-100" translate="no">
        {status.months.map((m) => {
          const has = m.state !== "nothing";
          return (
            <li key={m.month} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-sm">
              <span className="w-36 shrink-0 font-medium text-slate-900">{monthName(m.month)}</span>
              <span className={"min-w-0 flex-1 text-xs " + (has ? "text-slate-700" : "text-slate-400")}>
                <span>{MONTH_STATE_WORDS[m.state] ?? m.state}</span>
                {m.statement_imported_at && (
                  <span key="st">
                    <span>{" "}· statement <span>{money(m.statement_net_minor, currency)}</span> net, <span>{(m.statement_rows ?? 0).toLocaleString("en-US")}</span> rows, imported{" "}
                    <span>{relativeTime(m.statement_imported_at)}</span></span>
                  </span>
                )}
                {m.ads_days > 0 && <span key="ads"><span> · Ads report <span>{m.ads_days}</span> of <span>{m.month_days}</span> days</span></span>}
              </span>
              {has && (
                <span key="actions" className="flex shrink-0 items-center gap-3 text-xs">
                  <button type="button" className="tap text-brand-700 underline decoration-dotted underline-offset-2" disabled={busy !== null} onClick={() => onOpen(m.month)}>
                    {busy === m.month ? "Opening…" : "Show"}
                  </button>
                  {confirming === m.month ? (
                    <span key="confirm" className="flex items-center gap-2">
                      <button type="button" className="tap font-medium text-rose-700 underline" disabled={busy !== null} onClick={() => { setConfirming(null); onRemove(m.month); }}>
                        Remove this month&apos;s imports
                      </button>
                      <button type="button" className="tap text-slate-500" onClick={() => setConfirming(null)}>Cancel</button>
                    </span>
                  ) : (
                    <button key="remove" type="button" className="tap text-slate-500 underline decoration-dotted underline-offset-2" disabled={busy !== null} onClick={() => setConfirming(m.month)}>
                      Remove
                    </button>
                  )}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
