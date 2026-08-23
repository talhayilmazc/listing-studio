"use client";

import { useEffect, useState } from "react";
import { AUTH_START_URL, api } from "@/lib/api";
import type { Connection } from "@/lib/types";

// Full literal class strings so Tailwind's JIT keeps them.
const BANNERS: Record<string, { cls: string; text: string }> = {
  connected: {
    cls: "border-emerald-200 bg-emerald-50 text-emerald-700",
    text: "Your Etsy shop is connected.",
  },
  error: {
    cls: "border-rose-200 bg-rose-50 text-rose-700",
    text: "Something went wrong during authorization. Please try again.",
  },
  denied: {
    cls: "border-amber-200 bg-amber-50 text-amber-700",
    text: "Authorization was cancelled.",
  },
  expired: {
    cls: "border-amber-200 bg-amber-50 text-amber-700",
    text: "That authorization link expired. Please start again.",
  },
  unconfigured: {
    cls: "border-rose-200 bg-rose-50 text-rose-700",
    text: "Etsy API credentials are not configured on the server.",
  },
};

export default function ConnectPage() {
  const [conn, setConn] = useState<Connection | null>(null);
  const [banner, setBanner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const status = new URLSearchParams(window.location.search).get("status");
    if (status) setBanner(status);
    api.connection().then(setConn).catch(() => setConn(null));
  }, []);

  async function disconnect() {
    setBusy(true);
    try {
      setConn(await api.disconnect());
      setBanner(null);
    } finally {
      setBusy(false);
    }
  }

  const b = banner ? BANNERS[banner] : null;

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Connect your Etsy shop</h1>
        <p className="mt-1 text-sm text-slate-500">
          Authorize Listing Studio to create draft listings in your shop. You stay in control —
          nothing is ever published without your approval, and drafts are only created when you ask.
        </p>
      </div>

      {b && <div className={`card p-3 text-sm ${b.cls}`}>{b.text}</div>}

      <div className="card p-6">
        {conn?.connected ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" />
              <span className="font-medium text-slate-900">Connected</span>
            </div>
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <Info label="Etsy user id" value={conn.etsy_user_id?.toString() ?? "—"} />
              <Info label="Scopes" value={conn.scopes.join(", ") || "—"} />
              <Info
                label="Connected"
                value={conn.connected_at ? new Date(conn.connected_at).toLocaleString() : "—"}
              />
              <Info
                label="Token expires"
                value={conn.expires_at ? new Date(conn.expires_at).toLocaleString() : "—"}
              />
            </dl>
            <p className="text-xs text-slate-400">
              Access and refresh tokens are encrypted on the server and never shown here.
            </p>
            <button className="btn-secondary" onClick={disconnect} disabled={busy}>
              {busy ? "Disconnecting…" : "Disconnect shop"}
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-slate-600">
              You&apos;ll be sent to Etsy to sign in and approve access to your shop&apos;s listings.
            </p>
            <a href={AUTH_START_URL} className="btn-primary inline-flex">
              Connect your shop
            </a>
            <p className="text-xs text-slate-400">
              Requested permissions: read and write listings and shop data.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className="mt-0.5 break-words text-slate-800">{value}</dd>
    </div>
  );
}
