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
  | "analytics_retention_months"
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
      <div className="mk-wrap pb-20 pt-10 sm:pt-14">
        <header className="max-w-[46rem]">
          <h1 className="mk-h1">{title}</h1>
          <p className="mt-4"><span className="mk-chip"><span>Last updated <span>{updated}</span></span></span></p>
        </header>

        <div className="mt-12 grid grid-cols-1 gap-12 lg:grid-cols-[15rem_minmax(0,1fr)] lg:gap-16">
          {/* Wide screens: the contents stay beside the text. */}
          <nav aria-label="Contents" className="min-w-0 lg:sticky lg:top-24 lg:self-start">
            <p className="text-[0.9375rem] font-bold text-ink">Contents</p>
            <ol className="mt-3 space-y-1.5 border-l-2 border-heather text-[0.9375rem]">
              {sections.map((s, i) => (
                <li key={s.id}>
                  <a href={`#${s.id}`} className="tap -ml-0.5 block border-l-2 border-transparent py-0.5 pl-3 text-slate-600 hover:border-ink hover:text-ink">
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
      <h2 className="mk-h2 !text-[1.5rem] sm:!text-[1.75rem]">
        <span translate="no" className="tabular-nums text-slate-500"><span><span>{n}</span>.</span></span> <span><span>{title}</span></span>
      </h2>
      <div className="mk-body mt-4 space-y-4">{children}</div>
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
    <a href={`mailto:${email}`} className="mk-link">
      {email}
    </a>
  );
}

/**
 * How many months of analytics history are kept: the figure in force (an
 * operator setting, from /api/meta), or the default until it has loaded.
 */
export function AnalyticsMonths({ fallback }: { fallback: number }) {
  const meta = useContext(OperatorContext);
  return <span translate="no">{meta?.analytics_retention_months ?? fallback}</span>;
}

/** A plain definition-style list for "what we collect"-type passages. */
export function Items({ children }: { children: React.ReactNode }) {
  return <ul className="list-disc space-y-2 pl-5 marker:text-slate-300">{children}</ul>;
}

/** Emphasised block for the passages the law or Etsy's terms require be conspicuous: in capitals, as such
 *  passages are set by convention. Legal text, not a label (docs/design-v2.md keeps labels in sentence case). */
export function Conspicuous({ children }: { children: React.ReactNode }) {
  return (
    <div className="border-l-4 border-ink bg-fabric p-4 text-sm uppercase leading-relaxed text-ink">
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
