"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Connection } from "@/lib/types";

/** Compact Etsy connection indicator in the top bar. */
export function ConnectionBadge() {
  const [conn, setConn] = useState<Connection | null>(null);

  useEffect(() => {
    api.connection().then(setConn).catch(() => setConn(null));
  }, []);

  if (conn?.connected) {
    return (
      <Link
        href="/connect"
        className="flex items-center gap-1.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-sm text-emerald-700 hover:bg-emerald-100"
        title="Etsy shop connected"
      >
        <span className="h-2 w-2 rounded-full bg-emerald-500" />
        Shop connected
      </Link>
    );
  }

  return (
    <Link href="/connect" className="btn-secondary">
      Connect shop
    </Link>
  );
}
