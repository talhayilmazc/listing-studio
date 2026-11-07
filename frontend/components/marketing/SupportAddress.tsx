"use client";

import { useEffect, useState } from "react";

/** The support address, from the server's configuration (one place to set it). */
export function SupportAddress({ className = "" }: { className?: string }) {
  const [email, setEmail] = useState<string | null>(null);
  useEffect(() => {
    fetch("/api/meta")
      .then((r) => (r.ok ? r.json() : null))
      .then((m) => setEmail(m?.support_email ?? null))
      .catch(() => setEmail(null));
  }, []);
  // A line of its own, the same height before and after the address arrives,
  // so nothing around it moves.
  return (
    <span className={"block min-h-[1.7em] " + className}>
      {email ? (
        <a key="a" href={`mailto:${email}`} className="mk-link">
          {email}
        </a>
      ) : (
        <span key="w" className="text-slate-500">…</span>
      )}
    </span>
  );
}
