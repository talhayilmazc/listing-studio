"use client";

import Link from "next/link";
import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Meta } from "@/lib/types";

// Exact ToU notice — must not be altered or abbreviated.
export const TRADEMARK_NOTICE =
  "The term 'Etsy' is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.";

/**
 * Frame for the Terms and Privacy pages. Public (readable before sign-up), so no
 * rail: the serif wordmark, a readable column, and a table of contents.
 *
 * Operator facts (who runs the service, which law applies, where to write) come
 * from /api/meta rather than being typed into the page, so there is one place to
 * set them. Production refuses to start while any is empty; in development an
 * unset value is shown in amber so a gap is impossible to miss.
 */

type Operator = Pick<
  Meta,
  | "support_email"
  | "operator_name"
  | "operator_location"
  | "governing_law"
  | "dispute_venue"
  | "error_tracking"
>;

const OperatorContext = createContext<Operator | null>(null);

export function LegalShell({
  title,
  updated,
  sections,
  children,
}: {
  title: string;
  updated: string;
  sections: { id: string; title: string }[];
  children: React.ReactNode;
}) {
  const [meta, setMeta] = useState<Operator | null>(null);

  useEffect(() => {
    api
      .meta()
      .then(setMeta)
      .catch(() => setMeta(null));
  }, []);

  return (
    <OperatorContext.Provider value={meta}>
      <div className="min-h-screen bg-stone-50 px-6 py-10">
        <div className="mx-auto w-full max-w-3xl">
          <Link href="/" className="mb-10 flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
              LS
            </span>
            <span className="font-display text-2xl leading-none text-slate-900">
              Listing Studio
            </span>
          </Link>

          <h1 className="font-display text-4xl text-slate-900">{title}</h1>
          <p className="mt-2 text-sm text-slate-500">Last updated {updated}</p>

          <nav aria-label="Contents" className="card mt-8 p-5">
            <p className="label">Contents</p>
            <ol className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
              {sections.map((s, i) => (
                <li key={s.id}>
                  <a href={`#${s.id}`} className="text-slate-600 hover:text-brand-700">
                    <span className="tabular-nums text-slate-400">{i + 1}.</span> {s.title}
                  </a>
                </li>
              ))}
            </ol>
          </nav>

          <div className="legal mt-10 space-y-10">{children}</div>

          <footer className="mt-16 space-y-3 border-t border-slate-200 pt-8 text-xs text-slate-400">
            <p className="leading-relaxed">{TRADEMARK_NOTICE}</p>
            <p className="flex flex-wrap gap-x-4 gap-y-1">
              <Link href="/terms" className="hover:text-slate-600">
                Terms of Service
              </Link>
              <Link href="/privacy" className="hover:text-slate-600">
                Privacy Policy
              </Link>
              <SupportEmail />
            </p>
          </footer>
        </div>
      </div>
    </OperatorContext.Provider>
  );
}

export function Section({
  id,
  n,
  title,
  children,
}: {
  id: string;
  n: number;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-8">
      <h2 className="font-display text-2xl text-slate-900">
        <span className="tabular-nums text-slate-400">{n}.</span> {title}
      </h2>
      <div className="mt-3 space-y-3 text-[15px] leading-relaxed text-slate-700">{children}</div>
    </section>
  );
}

/** An operator fact from configuration, or an unmistakable marker when unset. */
export function Fact({
  field,
}: {
  field: "operator_name" | "operator_location" | "governing_law" | "dispute_venue";
}) {
  const meta = useContext(OperatorContext);
  if (meta === null) return <span className="text-slate-400">…</span>;
  const value = meta[field]?.trim();
  if (!value) {
    return (
      <span className="rounded bg-amber-100 px-1 font-medium text-amber-800">
        [{field.replace(/_/g, " ")} not configured]
      </span>
    );
  }
  return <>{value}</>;
}

export function SupportEmail() {
  const meta = useContext(OperatorContext);
  const email = meta?.support_email ?? "";
  if (!email) return <span className="text-slate-400">…</span>;
  return (
    <a href={`mailto:${email}`} className="font-medium text-brand-700 hover:underline">
      {email}
    </a>
  );
}

/** A plain definition-style list for "what we collect"-type passages. */
export function Items({ children }: { children: React.ReactNode }) {
  return <ul className="list-disc space-y-2 pl-5 marker:text-slate-300">{children}</ul>;
}

/** Emphasised block for the passages the law or Etsy's terms require be conspicuous. */
export function Conspicuous({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm uppercase leading-relaxed tracking-wide text-slate-700">
      {children}
    </div>
  );
}

/**
 * Renders only when error reporting is switched on, so the policy names Sentry
 * exactly when Sentry receives something — never before, never after.
 */
export function WhenErrorTracking({ children }: { children: React.ReactNode }) {
  const meta = useContext(OperatorContext);
  return meta?.error_tracking ? <>{children}</> : null;
}
