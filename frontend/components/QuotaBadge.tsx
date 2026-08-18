"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Quota } from "@/lib/types";

/** Remaining daily API quota — ToU requires this be visible to the user. */
export function QuotaBadge() {
  const [quota, setQuota] = useState<Quota | null>(null);

  useEffect(() => {
    api.quota().then(setQuota).catch(() => setQuota(null));
  }, []);

  if (!quota) {
    return <span className="text-xs text-slate-400">quota —</span>;
  }

  const pct = quota.tenant_limit ? quota.tenant_remaining / quota.tenant_limit : 0;
  const tone = pct < 0.1 ? "text-rose-600" : pct < 0.3 ? "text-amber-600" : "text-slate-600";

  return (
    <div
      className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1.5"
      title={`Daily Etsy API quota — global remaining ${quota.global_remaining.toLocaleString()} of ${quota.global_limit.toLocaleString()}`}
    >
      <span className="text-xs font-medium text-slate-400">API quota</span>
      <span className={`text-sm font-semibold tabular-nums ${tone}`}>
        {quota.tenant_remaining.toLocaleString()}
        <span className="font-normal text-slate-400"> / {quota.tenant_limit.toLocaleString()}</span>
      </span>
    </div>
  );
}
