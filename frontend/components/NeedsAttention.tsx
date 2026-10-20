"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Attention } from "@/lib/types";

/**
 * Overview's first block: everything that needs the seller now, each with its count and a link
 * straight to it (GET /api/attention; the sidebar badges show the same counts). Nothing needs
 * them: one calm line, the same height, so nothing below moves.
 */
export function NeedsAttention() {
  const [data, setData] = useState<Attention | null>(null);
  useEffect(() => {
    let cancelled = false;
    api.attention().then((a) => !cancelled && setData(a)).catch(() => !cancelled && setData({ items: [], total: 0, nav: {} }));
    return () => {
      cancelled = true;
    };
  }, []);

  if (data === null) return <section className="card min-h-[4.5rem] animate-pulse" aria-hidden />;
  if (!data.items.length) {
    return (
      <section className="card flex min-h-[4.5rem] items-center gap-2 px-5 text-sm text-slate-700" data-testid="needs-attention" data-total="0">
        <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-emerald-600" />
        <span>Nothing needs your attention right now.</span>
      </section>
    );
  }
  return (
    <section className="card border-amber-300 p-5" aria-labelledby="attention-title" data-testid="needs-attention" data-total={data.total}>
      <h2 id="attention-title" className="text-base font-semibold text-slate-900">Needs your attention</h2>
      <ul className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {data.items.map((item) => (
          <li key={item.key}>
            <Link href={item.href} data-testid="attention-item"
              className="flex min-h-[2.75rem] items-center gap-3 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-900 transition-colors hover:border-brand-600 hover:bg-brand-50">
              <span translate="no" className="flex h-7 min-w-[1.75rem] shrink-0 items-center justify-center rounded-full bg-amber-400 px-2 text-xs font-bold text-stone-900 tabular-nums">
                {item.count > 99 ? "99+" : item.count}
              </span>
              <span className="min-w-0 flex-1"><span translate="no">{item.label}</span></span>
              <span aria-hidden className="text-brand-700">→</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
