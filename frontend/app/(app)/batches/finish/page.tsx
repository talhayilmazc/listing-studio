"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { nextRow, topLine } from "@/lib/howMade";
import type { FinishInEtsy, FinishInEtsyGroup, FinishInEtsyRow } from "@/lib/types";
import { Help } from "@/components/Help";
import { StatusChip } from "@/components/StatusChip";
import { Txt } from "@/components/Txt";

/** Read again this often while a draft opened from here may go live (the server reads Etsy at most once a minute). */
const POLL_MS = 30_000;

/**
 * Finish in Etsy: every listing Listyro created, draft or active, whose How it's made is still
 * to tick in Etsy (Etsy's API has no field for it; backend api/finish_in_etsy.py). Grouped by
 * the answers to tick, then by shop. Each row opens the listing in Etsy's editor in a new tab;
 * the seller marks it done here. A draft opened from here that went live asks "Mark done?",
 * never marked done by itself. Nothing on Etsy is changed by this page.
 */
export default function FinishInEtsyPage() {
  const [data, setData] = useState<FinishInEtsy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  // Opened in this visit ("Open next" walks past them).
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.finishInEtsy());
    } catch (e: any) {
      setError(e.message ?? String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const rows = useMemo(() => (data?.groups ?? []).flatMap((g) => g.shops.flatMap((s) => s.rows)), [data]);
  const watching = rows.some((r) => r.state === "draft" && r.opened_at !== null);
  useEffect(() => {
    if (!watching) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [watching, load]);

  async function open(row: FinishInEtsyRow) {
    setOpened((o) => new Set(o).add(row.key));
    try {
      await api.finishInEtsyOpened([row.key]);
    } catch {
      /* the link still opened; only the "Mark done?" prompt depends on this */
    }
  }

  function openNext(group: FinishInEtsyGroup) {
    const all = group.shops.flatMap((s) => s.rows);
    const row = nextRow(all, opened, new Set());
    if (!row) {
      setResult("Every listing in this group was opened in this visit. Mark the finished ones done.");
      return;
    }
    window.open(row.editor_link, "_blank", "noopener");
    open(row);
  }

  async function markDone(keys: string[]) {
    if (keys.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.finishInEtsyDone(keys);
      setResult(`${res.done} marked done. ${res.total} left to finish in Etsy.`);
      setSelected((s) => new Set([...s].filter((k) => !keys.includes(k))));
      await load();
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  function toggle(keys: string[], on: boolean) {
    setSelected((s) => {
      const next = new Set(s);
      for (const k of keys) (on ? next.add(k) : next.delete(k));
      return next;
    });
  }

  const chosen = rows.filter((r) => selected.has(r.key)).map((r) => r.key);
  const live = rows.filter((r) => r.went_live);

  return (
    <div className="space-y-6" data-testid="finish-in-etsy">
      <div className="space-y-2">
        <p className="max-w-3xl text-sm text-slate-600">
          <span>Etsy&apos;s API cannot set How it&apos;s made, so each listing is finished in Etsy.</span>
          <Help label="Finish in Etsy">
            Etsy asks &ldquo;How does your shop produce this item?&rdquo; and &ldquo;What tools are used to make this
            item?&rdquo; on every listing. Its API has no field for either, and Listyro never works Etsy&apos;s website for
            you. Each profile keeps your answers; open each listing in Etsy, tick them, and mark it done here.
          </Help>
        </p>
        {data && (
          <p key="top" className="text-sm font-medium text-slate-800" translate="no" data-testid="finish-top-line">
            <span>{topLine(data.drafts, data.active)}</span>
          </p>
        )}
      </div>

      {live.length > 0 && (
        <div key="live" role="status" className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900" data-testid="finish-went-live">
          <p translate="no">
            <span>{`${live.length} draft${live.length === 1 ? "" : "s"} you opened ${live.length === 1 ? "is" : "are"} live on Etsy now. Did you tick How it's made?`}</span>
          </p>
          <ul className="mt-1 space-y-1">
            {live.map((r) => (
              <li key={r.key} className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 max-w-full truncate" title={r.title}>{r.title}</span>
                <button type="button" className="btn-secondary px-3 py-1 text-xs" disabled={busy}
                  onClick={() => markDone([r.key])} data-testid="finish-live-done">
                  Mark done
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(chosen.length > 0 || result || error) && (
        <div key="bar" className="flex flex-wrap items-center gap-3">
          {chosen.length > 0 && (
            <button key="done" type="button" className="btn-primary" disabled={busy} onClick={() => markDone(chosen)} data-testid="finish-mark-done">
              {`Mark done (${chosen.length})`}
            </button>
          )}
          {result && <span key="result" role="status" translate="no" className="text-sm text-slate-700"><span>{result}</span></span>}
          {error && <span key="error" role="alert" className="text-sm text-rose-700"><Txt>{error}</Txt></span>}
        </div>
      )}

      {data === null ? (
        <p key="loading" className="text-sm text-slate-600">Loading…</p>
      ) : data.groups.length === 0 ? (
        <p key="empty" className="card p-6 text-sm text-slate-600" data-testid="finish-empty">
          Nothing to finish in Etsy: every listing Listyro created is marked done.
        </p>
      ) : (
        <div key="groups" className="space-y-6">
          {data.groups.map((group) => {
            const keys = group.shops.flatMap((s) => s.rows.map((r) => r.key));
            const all = keys.length > 0 && keys.every((k) => selected.has(k));
            return (
              <section key={group.key} className="card overflow-hidden" data-testid="finish-group">
                <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-slate-50 px-4 py-3">
                  <div className="min-w-0">
                    <h2 className="text-sm font-semibold text-slate-900" translate="no"><span>{group.label}</span></h2>
                    {group.set ? (
                      <p key="tick" className="text-xs text-slate-600">
                        <span>{`Tick: ${group.production} · ${group.tools}`}</span>
                      </p>
                    ) : (
                      <p key="unset" className="text-xs text-amber-900">
                        <span>Set How it&apos;s made on the profile first, then tick the same in Etsy:</span>{" "}
                        {group.profiles.map((p, i) => (
                          <span key={p.id}>
                            <span>{i > 0 ? ", " : ""}</span>
                            <Link href={`/profiles#profile-${p.id}`} className="tap text-brand-700 underline">{p.name}</Link>
                          </span>
                        ))}
                      </p>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <label className="tap flex items-center gap-2 text-xs text-slate-700">
                      <input type="checkbox" checked={all} onChange={(e) => toggle(keys, e.target.checked)} aria-label={`Select all in ${group.label}`} />
                      <span>Select all</span>
                    </label>
                    <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => openNext(group)} data-testid="finish-open-next">
                      Open next
                    </button>
                  </div>
                </header>
                {group.shops.map((shop) => (
                  <div key={shop.connection_id}>
                    <p className="border-b border-slate-100 px-4 pb-1 pt-3 text-xs font-semibold text-slate-700" translate="no">
                      <span>{`${shop.shop_name} · ${shop.rows.length}`}</span>
                    </p>
                    <ul>
                      {shop.rows.map((row) => (
                        <li key={row.key} className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-2 last:border-b-0" data-testid="finish-row">
                          <input type="checkbox" checked={selected.has(row.key)} onChange={(e) => toggle([row.key], e.target.checked)}
                            aria-label={`Select ${row.title}`} />
                          <div className="h-12 w-12 shrink-0 overflow-hidden rounded bg-slate-100">
                            {row.asset_id && (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img key="img" src={api.assetImage(row.asset_id, 112)} alt="" className="h-full w-full object-cover" loading="lazy" />
                            )}
                          </div>
                          <div className="min-w-0 flex-1 basis-40">
                            <p className="truncate text-sm text-slate-900" title={row.title}>{row.title}</p>
                            <p className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                              <span translate="no" className="max-w-[12rem] truncate">{row.shop_name}</span>
                              <StatusChip tone={row.state === "active" ? "done" : "neutral"}>{row.state === "active" ? "active" : "draft"}</StatusChip>
                              {opened.has(row.key) && <span key="opened">opened</span>}
                            </p>
                          </div>
                          <div className="flex shrink-0 flex-wrap items-center gap-2">
                            <a href={row.editor_link} target="_blank" rel="noopener noreferrer" className="btn-secondary px-3 py-1.5 text-xs"
                              onClick={() => open(row)} data-testid="finish-open">
                              Open in Etsy ↗
                            </a>
                            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy}
                              onClick={() => markDone([row.key])} data-testid="finish-done">
                              Done
                            </button>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
