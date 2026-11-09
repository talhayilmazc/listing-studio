"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { AdminAccountPublishing, AdminPublishing, AdminPublishingRow } from "@/lib/types";
import { Txt } from "@/components/Txt";

/**
 * Admin → Publishing (fix/usage-and-admin-publishing, Part 2): "did my schedule work?" without the server.
 * Read only: nothing here changes data or calls Etsy (requeue and reconcile stay in the CLI). Every state
 * and reason is publish-report's own (backend api/admin_publishing.py reads ops/publish_report.py).
 */
export function PublishingTab({ onError }: { onError: (message: string | null) => void }) {
  const [data, setData] = useState<AdminPublishing | null>(null);
  const [page, setPage] = useState(1);
  const [attentionPage, setAttentionPage] = useState(1);
  const [account, setAccount] = useState<{ id: string; email: string } | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.admin.publishing(page, attentionPage)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && onError(e.message ?? String(e)));
    return () => {
      cancelled = true;
    };
  }, [page, attentionPage, onError]);

  if (account) {
    return <AccountPublishing account={account} onBack={() => setAccount(null)} onError={onError} />;
  }
  if (!data) return <div className="card h-64 animate-pulse bg-white" aria-hidden data-testid="publishing-loading" />;

  const open = (r: { account: string; account_id: string }) => setAccount({ id: r.account_id, email: r.account });
  return (
    <div className="space-y-6" data-testid="admin-publishing">
      <p className="text-sm text-slate-600">
        Read only. Every state is the one <code>publish-report</code> prints. Requeue and reconcile stay in the CLI.
      </p>

      <section className="card p-4" aria-labelledby="pub-upcoming">
        <h2 id="pub-upcoming" className="text-base font-semibold text-slate-900">
          <span>Upcoming, next 7 days</span>{" "}
          <span translate="no" className="text-sm font-normal text-slate-500">{`(${data.upcoming.total})`}</span>
        </h2>
        <Rows rows={data.upcoming.rows} onAccount={open} times data-testid="upcoming" />
        <Pager page={data.upcoming.page} pages={data.upcoming.pages} onPage={setPage} />
      </section>

      <section className="card p-4" aria-labelledby="pub-attention">
        <h2 id="pub-attention" className="text-base font-semibold text-slate-900">
          <span>Needs attention</span>{" "}
          <span translate="no" className="text-sm font-normal text-slate-500">{`(${data.attention.total})`}</span>
        </h2>
        <p className="mt-1 text-xs text-slate-600">
          Missed schedules, failed or refused attempts, approved drafts waiting more than 3 days with no go-live time,
          and what <code>requeue-stuck</code> would list.
        </p>
        <Rows rows={data.attention.rows} onAccount={open} times={false} />
        <Pager page={data.attention.page} pages={data.attention.pages} onPage={setAttentionPage} />
      </section>

      <section className="card p-4" aria-labelledby="pub-activity">
        <h2 id="pub-activity" className="text-base font-semibold text-slate-900">Last 7 days</h2>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm" data-testid="publishing-activity">
            <thead>
              <tr className="border-b border-slate-200 text-xs text-slate-500">
                <th className="py-2 pr-3 font-medium">Account</th>
                <th className="py-2 pr-3 font-medium">Day (Istanbul)</th>
                <th className="py-2 pr-3 text-right font-medium">Drafts created</th>
                <th className="py-2 pr-3 text-right font-medium" title="By a schedule or Publish now">Live through the app</th>
                <th className="py-2 pr-3 text-right font-medium">Published by the seller</th>
                <th className="py-2 text-right font-medium">Failures</th>
              </tr>
            </thead>
            <tbody translate="no">
              {data.activity.map((d) => (
                <tr key={`${d.account_id}-${d.day}`} className="border-b border-slate-100 last:border-0">
                  <td className="py-1.5 pr-3"><button type="button" className="tap text-brand-700 underline" onClick={() => open(d)}>{d.account}</button></td>
                  <td className="py-1.5 pr-3 tabular-nums">{d.day}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{d.drafts}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{d.live_by_app}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums">{d.by_seller}</td>
                  <td className="py-1.5 text-right tabular-nums">{d.failures}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3 className="mt-4 text-sm font-semibold text-slate-900">Listings counted this period</h3>
        <p className="text-xs text-slate-600">New: counted at the first Etsy draft. Old: listings written. While the counter is in shadow mode the screens use the old number.</p>
        <table className="mt-2 w-full max-w-xl text-left text-sm" data-testid="publishing-counted">
          <thead>
            <tr className="border-b border-slate-200 text-xs text-slate-500">
              <th className="py-2 pr-3 font-medium">Account</th>
              <th className="py-2 pr-3 font-medium">Counter</th>
              <th className="py-2 pr-3 text-right font-medium">Old</th>
              <th className="py-2 text-right font-medium">New</th>
            </tr>
          </thead>
          <tbody translate="no">
            {data.counted.map((c) => (
              <tr key={c.account_id} className="border-b border-slate-100 last:border-0">
                <td className="py-1.5 pr-3">{c.account}</td>
                <td className="py-1.5 pr-3">{c.counter}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{c.old}</td>
                <td className="py-1.5 text-right tabular-nums">{c.new}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}

function Rows({ rows, onAccount, times }: {
  rows: AdminPublishingRow[];
  onAccount: (r: AdminPublishingRow) => void;
  times: boolean;
  "data-testid"?: string;
}) {
  if (!rows.length) return <p className="mt-2 text-sm text-slate-600">Nothing here.</p>;
  let last = "";
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full min-w-[860px] text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200 text-xs text-slate-500">
            <th className="py-2 pr-3 font-medium">Shop</th>
            <th className="py-2 pr-3 font-medium">SKU</th>
            <th className="py-2 pr-3 font-medium">Title</th>
            {times ? <th key="d" className="py-2 pr-3 font-medium">Draft</th> : null}
            {times ? <th key="g" className="py-2 pr-3 font-medium">Go-live</th> : null}
            <th className="py-2 pr-3 font-medium">State</th>
            {times ? null : <th key="w" className="py-2 font-medium">Why</th>}
          </tr>
        </thead>
        <tbody translate="no">
          {rows.flatMap((r) => {
            const head = r.account !== last;
            last = r.account;
            return [
              head ? (
                <tr key={`h-${r.account_id}-${r.content_id}`} className="bg-slate-50">
                  <td colSpan={times ? 6 : 5} className="py-1.5 pr-3 text-xs font-semibold text-slate-800">
                    <button type="button" className="tap underline" onClick={() => onAccount(r)}>{r.account}</button>
                    <span className="ml-2 font-normal text-slate-500">{`seller's zone: ${r.seller_zone}`}</span>
                  </td>
                </tr>
              ) : null,
              <tr key={`${r.content_id}-${r.shop_id}`} className="border-b border-slate-100 align-top last:border-0">
                <td className="py-1.5 pr-3">{r.shop ?? "no shop yet"}</td>
                <td className="py-1.5 pr-3 font-mono text-xs">{r.sku ?? "-"}</td>
                <td className="max-w-[220px] truncate py-1.5 pr-3" title={r.title}>{r.title}</td>
                {times ? <td key="d" className="whitespace-nowrap py-1.5 pr-3 text-xs"><Time ist={r.draft_istanbul} seller={r.draft_seller} /></td> : null}
                {times ? <td key="g" className="whitespace-nowrap py-1.5 pr-3 text-xs"><Time ist={r.go_live_istanbul} seller={r.go_live_seller} /></td> : null}
                <td className="whitespace-nowrap py-1.5 pr-3 text-xs font-medium">{r.state}</td>
                {times ? null : <td key="w" className="py-1.5 text-xs text-slate-700">{r.why}</td>}
              </tr>,
            ].filter(Boolean);
          })}
        </tbody>
      </table>
    </div>
  );
}

function Time({ ist, seller }: { ist: string | null; seller: string | null }) {
  if (!ist) return <span>-</span>;
  return (
    <span className="block">
      <span className="block">{ist}</span>
      <span className="block text-slate-500">{seller}</span>
    </span>
  );
}

function Pager({ page, pages, onPage }: { page: number; pages: number; onPage: (p: number) => void }) {
  if (pages <= 1) return null;
  return (
    <p className="mt-2 flex items-center gap-2 text-xs text-slate-600" translate="no">
      <button type="button" className="btn-quiet" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <span>{`Page ${page} of ${pages}`}</span>
      <button type="button" className="btn-quiet" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </p>
  );
}

/** An account's Publishing section: the summary lines as publish-report prints them, then its listings. */
export function AccountPublishing({ account, onBack, onError }: {
  account: { id: string; email: string };
  onBack?: () => void;
  onError: (message: string | null) => void;
}) {
  const [data, setData] = useState<AdminAccountPublishing | null>(null);
  const [filters, setFilters] = useState<{ shop: string; state: string; since: string; until: string; q: string; page: number }>(
    { shop: "", state: "", since: "", until: "", q: "", page: 1 });
  const [detail, setDetail] = useState<Record<string, string[] | "loading">>({});

  const load = useCallback(() => {
    api.admin.accountPublishing(account.id, filters).then(setData).catch((e) => onError(e.message ?? String(e)));
  }, [account.id, filters, onError]);
  useEffect(load, [load]);

  async function toggle(contentId: string, shopId: string | null) {
    const key = `${contentId}-${shopId}`;
    if (detail[key]) {
      setDetail(({ [key]: _, ...rest }) => rest);
      return;
    }
    setDetail((d) => ({ ...d, [key]: "loading" }));
    try {
      const got = await api.admin.publishingDetail(account.id, contentId, shopId);
      setDetail((d) => ({ ...d, [key]: got.lines }));
    } catch (e: any) {
      onError(e.message ?? String(e));
    }
  }

  const set = (patch: Partial<typeof filters>) => setFilters((f) => ({ ...f, page: 1, ...patch }));
  return (
    <section className="space-y-4" data-testid="account-publishing" aria-label={`Publishing for ${account.email}`}>
      <div className="flex flex-wrap items-center gap-3">
        {onBack ? <button type="button" className="btn-quiet" onClick={onBack}>← All accounts</button> : null}
        <h2 className="text-base font-semibold text-slate-900" translate="no">{`Publishing · ${account.email}`}</h2>
      </div>
      {!data ? <div className="card h-48 animate-pulse bg-white" aria-hidden /> : (
        <>
          <div className="card p-4">
            <h3 className="text-sm font-semibold text-slate-900">
              <span>Summary</span>{" "}
              <span translate="no" className="font-normal text-slate-500">{`(listings since ${data.since}, as publish-report shows them)`}</span>
            </h3>
            <ul className="mt-2 space-y-1 text-sm" data-testid="publishing-summary">
              {data.summary.map((s) => (
                <li key={s.outcome}>
                  <button type="button" className="tap text-left text-brand-700 underline" onClick={() => set({ state: filters.state === s.outcome ? "" : s.outcome })}
                    aria-pressed={filters.state === s.outcome}>
                    <span translate="no">{`${s.outcome}: ${s.count}`}</span>
                  </button>
                </li>
              ))}
            </ul>
            <details className="mt-2 text-xs">
              <summary className="tap cursor-pointer text-slate-600">The summary as text</summary>
              <pre className="mt-1 overflow-x-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-[11px] text-slate-800" translate="no">{data.summary_text.join("\n")}</pre>
            </details>
          </div>

          <div className="card space-y-3 p-4">
            <div className="flex flex-wrap items-end gap-2 text-sm">
              <label className="space-y-0.5"><span className="label block">Shop</span>
                <select className="field w-auto" value={filters.shop} onChange={(e) => set({ shop: e.target.value })}>
                  <option value="">Every shop</option>
                  {data.shops.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </label>
              <label className="space-y-0.5"><span className="label block">State</span>
                <select className="field w-auto" value={filters.state} onChange={(e) => set({ state: e.target.value })}>
                  <option value="">Every state</option>
                  {data.summary.map((s) => <option key={s.outcome} value={s.outcome}>{s.outcome}</option>)}
                </select>
              </label>
              <label className="space-y-0.5"><span className="label block">From</span>
                <input type="date" className="field w-auto" value={filters.since} onChange={(e) => set({ since: e.target.value })} /></label>
              <label className="space-y-0.5"><span className="label block">Until</span>
                <input type="date" className="field w-auto" value={filters.until} onChange={(e) => set({ until: e.target.value })} /></label>
              <label className="space-y-0.5"><span className="label block">SKU or Etsy listing id</span>
                <input type="search" className="field w-48" value={filters.q} onChange={(e) => set({ q: e.target.value })} /></label>
            </div>
            <ul className="divide-y divide-slate-100 text-sm" data-testid="publishing-items" translate="no">
              {data.items.map((i) => {
                const key = `${i.content_id}-${i.shop_id}`;
                const lines = detail[key];
                return (
                  <li key={key} className="py-2">
                    <button type="button" className="tap flex w-full flex-wrap items-baseline gap-x-3 text-left" onClick={() => toggle(i.content_id, i.shop_id)}
                      aria-expanded={Boolean(lines)}>
                      <span className="font-medium text-slate-900">{i.shop ?? "no shop yet"}</span>
                      <span className="font-mono text-xs">{i.sku ?? "-"}</span>
                      <span className="text-xs text-slate-600">{i.etsy_listing_id ?? "no draft"}</span>
                      <span className="min-w-0 flex-1 truncate">{i.title}</span>
                      <span className="text-xs font-medium">{i.state}</span>
                    </button>
                    <p className="text-xs text-slate-600">{`${i.outcome}: ${i.why}`}</p>
                    {lines ? (
                      <pre key="detail" className="mt-1 overflow-x-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-[11px] text-slate-800" data-testid="publishing-detail">
                        <Txt>{lines === "loading" ? "Reading…" : lines.join("\n")}</Txt>
                      </pre>
                    ) : null}
                  </li>
                );
              })}
            </ul>
            <Pager page={data.page} pages={data.pages} onPage={(p) => setFilters((f) => ({ ...f, page: p }))} />
          </div>
        </>
      )}
    </section>
  );
}
