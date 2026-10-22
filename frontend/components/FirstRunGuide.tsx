"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { closeHere, closedHere, steps, type Facts } from "@/lib/firstRun";
import { useShops } from "./ShopProvider";

const SENT = new Set(["drafted", "partly_drafted", "scheduled", "partly_published", "published"]);

/** Four steps for a new account, ticked from its own data (B9); gone once done or closed. */
export function FirstRunGuide() {
  const { shops } = useShops();
  const [facts, setFacts] = useState<Facts | null>(null);
  const [closed, setClosed] = useState(true);
  useEffect(() => setClosed(closedHere()), []);
  useEffect(() => {
    if (closed || shops === null) return;
    let cancelled = false;
    Promise.all([api.listProfiles(null).catch(() => []), api.listBatches().catch(() => [])]).then(([profiles, batches]) => {
      if (cancelled) return;
      setFacts({
        shops: shops.length,
        confirmedProfiles: profiles.filter((p) => p.confirmed).length,
        batches: batches.length,
        sent: batches.filter((b) => (b.statuses ?? []).some((s) => SENT.has(s))).length,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [closed, shops]);
  if (closed || facts === null) return null;
  const list = steps(facts);
  if (list.every((s) => s.done)) return null;
  const next = list.find((s) => !s.done);
  return (
    <section className="card space-y-3 p-4" data-testid="first-run-guide" aria-labelledby="first-run">
      <div className="flex items-start justify-between gap-3">
        <h2 id="first-run" className="text-sm font-semibold text-slate-900">Getting started</h2>
        <button type="button" className="tap text-xs text-slate-500 underline" onClick={() => {
          closeHere();
          setClosed(true);
        }}>
          Close
        </button>
      </div>
      <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {list.map((s, n) => (
          <li key={s.key} className={"rounded-md border p-3 text-sm " + (s.done ? "border-emerald-200 bg-emerald-50/50" : "border-slate-200")}>
            <p className="font-medium text-slate-900">
              <span translate="no">{s.done ? "✓ " : `${n + 1}. `}</span><span>{s.title}</span>
            </p>
            <p className="mt-0.5 text-xs text-slate-600">{s.body}</p>
            {s === next && (
              <Link key="go" href={s.href} className="btn-secondary mt-2 inline-flex px-3 py-1.5 text-xs">{s.title}</Link>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
