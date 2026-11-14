"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { KEPT_HOURS, making, zipLine } from "@/lib/mockupDownloads";
import { EmptyState } from "@/components/EmptyState";
import type { MockupZip } from "@/lib/types";

const megabytes = (n: number) => `${(n / (1024 * 1024)).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;

/** Mockups → Downloads (feature/mockups-visible): the ZIPs being made and those ready, kept 48 hours. Sellers
 * download them and put them where they like (Google Drive, a folder): the app uploads nowhere. */
export default function MockupDownloads() {
  const [zips, setZips] = useState<MockupZip[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function load() {
    try {
      setZips(await api.mockupZips());
      setError(null);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }
  useEffect(() => {
    load();
  }, []);
  // Looked at again while one is being made: its progress, then its link.
  useEffect(() => {
    if (!zips?.some(making)) return;
    const t = setTimeout(load, 1500);
    return () => clearTimeout(t);
  }, [zips]);

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600">
        <span>{`Your mockup ZIPs, kept ${KEPT_HOURS} hours after they are ready. Make one from your mockups' results.`}</span>
      </p>
      {error && <p key="error" role="alert" className="text-sm text-rose-700">{error}</p>}
      {zips === null && !error ? (
        <div key="wait" className="card h-24 animate-pulse bg-slate-50" />
      ) : zips && zips.length === 0 ? (
        <EmptyState key="empty" line="No downloads in the last 48 hours." action="Make mockups" href="/mockups" />
      ) : (
        <ul key="list" className="space-y-2" data-testid="downloads-list">
          {(zips ?? []).map((z) => (
            <li key={z.id} className="card flex flex-wrap items-center gap-3 p-3" data-testid="download-row">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-slate-900" translate="no">{z.label ?? "Mockups"}</p>
                <p className="text-xs text-slate-600" translate="no">
                  <span>{z.created_at ? new Date(z.created_at).toLocaleString() : ""}</span>
                  <span>{z.options ? ` · ${z.options.format.toUpperCase()}, ${z.options.size === "original" ? "original size" : `${z.options.size} px`}` : ""}</span>
                </p>
                <p className={`mt-1 text-xs ${z.status === "failed" ? "text-rose-700" : "text-slate-700"}`} translate="no">
                  {zipLine(z, z.byte_size ? megabytes(z.byte_size) : null)}
                </p>
                {making(z) && z.total ? (
                  <div key="bar" className="mt-1 h-1.5 w-full max-w-xs overflow-hidden rounded bg-slate-100" aria-hidden>
                    <div className="h-full bg-brand-600" style={{ width: `${Math.round(((z.done ?? 0) / z.total) * 100)}%` }} />
                  </div>
                ) : null}
              </div>
              {z.status === "ready" ? (
                <a key="get" className="btn-primary min-h-11" href={api.mockupZipUrl(z.id)} download data-testid="download-zip">Download</a>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-slate-600">
        <span>Single images download at once from </span>
        <Link href="/mockups" className="tap underline">Make mockups</Link>
        <span>: the Download link under each mockup, or in the editor.</span>
      </p>
    </div>
  );
}
