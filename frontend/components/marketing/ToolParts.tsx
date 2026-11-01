import type { Source } from "@/lib/marketing/content";
import { brandWarning } from "@/lib/brandSpot";
import { BRAND_LIMITS, BRAND_RULE, BRAND_SOURCES, KIND_WORDS, VERDICT_WORDS, type Line, type Verdict } from "@/lib/freeTools/check";

/** The parts both checkers are made of. They hold no state, so the static page shows every rule. */

const VERDICT_STYLE: Record<Verdict, string> = {
  idle: "border-slate-200 bg-slate-50 text-slate-700",
  pass: "border-emerald-200 bg-emerald-50 text-emerald-800",
  fail: "border-rose-200 bg-rose-50 text-rose-800",
  warn: "border-amber-200 bg-amber-50 text-amber-900",
  note: "border-slate-200 bg-slate-50 text-slate-700",
};

const LINK = "font-medium text-brand-700 underline decoration-brand-100 underline-offset-4 hover:decoration-brand-600";

export function RuleList({ lines }: { lines: Line[] }) {
  return (
    <ul className="divide-y divide-slate-100 rounded-2xl border border-slate-200 bg-white" data-testid="rule-list">
      {lines.map((line) => (
        <li key={line.id} className="px-4 py-3.5 sm:px-5" data-testid={`rule-${line.id}`} data-verdict={line.verdict}>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-medium text-slate-900">{line.name}</span>
            <span translate="no" className={"rounded border px-1.5 text-xs leading-5 " + VERDICT_STYLE[line.verdict]} data-testid="verdict">{VERDICT_WORDS[line.verdict]}</span>
            <span className="text-xs text-slate-600" data-testid="rule-kind">{KIND_WORDS[line.kind]}</span>
          </p>
          <p className="mt-1 min-h-[1.25rem] text-[0.9375rem] leading-relaxed text-slate-800" translate="no" data-testid="rule-detail">{line.detail}</p>
          {line.kind === "etsy" ? (
            <p className="mt-1 text-sm leading-relaxed text-slate-600">
              <span>{`“${line.quote}” `}</span>
              <a href={line.source.url} rel="noopener" className={LINK}>{line.source.label}</a>
            </p>
          ) : (
            <p className="mt-1 text-sm leading-relaxed text-slate-600">{line.why}</p>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Each source on a line of its own, so two links never read as one. */
export function SourceLinks({ sources }: { sources: Source[] }) {
  return (
    <ul className="mt-1 space-y-1 text-sm leading-relaxed">
      {sources.map((source) => <li key={source.url}><a href={source.url} rel="noopener" className={LINK}>{source.label}</a></li>)}
    </ul>
  );
}

/** The trademark warning, in the app's own sentence: it warns, and it stops nothing. */
export function BrandWarning({ brands }: { brands: string[] }) {
  return (
    <section aria-label="Brand names" className="rounded-2xl border border-slate-200 bg-white px-4 py-3.5 sm:px-5" data-testid="brand-warning" data-brands={brands.length}>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium text-slate-900">Brand names</span>
        <span translate="no" className={"rounded border px-1.5 text-xs leading-5 " + VERDICT_STYLE[brands.length ? "warn" : "pass"]}>{brands.length ? VERDICT_WORDS.warn : "None found"}</span>
      </p>
      <div translate="no">
        {brands.map((brand) => <p key={brand} className="mt-1 text-[0.9375rem] leading-relaxed text-slate-800">{brandWarning(brand)}</p>)}
      </div>
      {brands.length === 0 ? <p key="rule" className="mt-1 text-sm leading-relaxed text-slate-600">{BRAND_RULE}</p> : null}
      <SourceLinks sources={BRAND_SOURCES} />
      <p className="mt-1 text-sm leading-relaxed text-slate-600">{BRAND_LIMITS}</p>
    </section>
  );
}
