"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Meta } from "@/lib/types";

// The site's footer carries the exact trademark notice on these pages.

/**
 * Body of the Terms and Privacy pages, inside the public site's frame (header,
 * footer with the trademark notice): a readable column and its contents.
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
      <div className="mx-auto w-full max-w-[1240px] px-4 pb-20 pt-12 sm:px-6 sm:pt-16">
        <header className="max-w-[46rem]">
          <p className="text-sm font-medium uppercase tracking-[0.12em] text-brand-700">Legal</p>
          <h1 className="mt-3 font-display text-[clamp(2.6rem,6vw,4.25rem)] leading-[1.02] text-slate-900">{title}</h1>
          <p className="mt-4 text-sm text-slate-500"><span>Last updated <span>{updated}</span></span></p>
        </header>

        <div className="mt-12 grid grid-cols-1 gap-12 lg:grid-cols-[15rem_minmax(0,1fr)] lg:gap-16">
          {/* Wide screens: the contents stay beside the text. */}
          <nav aria-label="Contents" className="min-w-0 lg:sticky lg:top-24 lg:self-start">
            <p className="label">Contents</p>
            <ol className="mt-2 space-y-1.5 border-l border-slate-200 text-sm">
              {sections.map((s, i) => (
                <li key={s.id}>
                  <a href={`#${s.id}`} className="tap -ml-px block border-l border-transparent py-0.5 pl-3 text-slate-600 hover:border-brand-600 hover:text-slate-900">
                    <span translate="no" className="tabular-nums text-slate-500"><span><span>{i + 1}</span>.</span></span> <span><span>{s.title}</span></span>
                  </a>
                </li>
              ))}
            </ol>
          </nav>

          <div className="legal min-w-0 max-w-[46rem] space-y-12">{children}</div>
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
    <section id={id} className="scroll-mt-24">
      <h2 className="font-display text-3xl leading-tight text-slate-900">
        <span translate="no" className="tabular-nums text-slate-500"><span><span>{n}</span>.</span></span> <span><span>{title}</span></span>
      </h2>
      <div className="mt-4 space-y-4 text-[1.0625rem] leading-relaxed text-slate-700">{children}</div>
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
  if (meta === null) return <span className="text-slate-500">…</span>;
  const value = meta[field]?.trim();
  if (!value) {
    return (
      <span className="rounded bg-amber-100 px-1 font-medium text-amber-800">
        <span>[<span>{field.replace(/_/g, " ")}</span> not configured]</span>
      </span>
    );
  }
  return <>{value}</>;
}

export function SupportEmail() {
  const meta = useContext(OperatorContext);
  const email = meta?.support_email ?? "";
  if (!email) return <span className="text-slate-500">…</span>;
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
