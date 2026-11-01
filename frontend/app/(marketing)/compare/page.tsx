import type { Metadata } from "next";
import { JsonLd, PageHead, PROSE } from "@/components/marketing/Page";
import { ALTERNATIVES, COMPARES, COMPARE_PAGE } from "@/lib/marketing/compare";
import { breadcrumbs } from "@/lib/marketing/jsonld";
import { metadataFor } from "@/lib/marketing/pages";

export const metadata: Metadata = metadataFor("/compare");

export default function Compare() {
  return (
    <div className={PROSE + " pb-20"}>
      <JsonLd data={breadcrumbs("/compare")} />
      <PageHead path="/compare" h1={COMPARE_PAGE.h1} lead={COMPARE_PAGE.lead} />
      <ul className="divide-y divide-slate-200 border-y border-slate-200">
        {COMPARES.map((c) => (
          <li key={c.slug} className="py-5" data-testid="compare-link">
            <h2 className="text-[1.1875rem] font-medium text-slate-900"><a href={`/compare/${c.slug}`} className="tap hover:underline">{c.h1}</a></h2>
            <p className="mt-1 text-[0.9375rem] leading-relaxed text-slate-700">{c.lead}</p>
          </li>
        ))}
        <li key={ALTERNATIVES.slug} className="py-5" data-testid="compare-link">
          <h2 className="text-[1.1875rem] font-medium text-slate-900"><a href={`/compare/${ALTERNATIVES.slug}`} className="tap hover:underline">{ALTERNATIVES.h1}</a></h2>
          <p className="mt-1 text-[0.9375rem] leading-relaxed text-slate-700">{ALTERNATIVES.lead}</p>
        </li>
      </ul>
    </div>
  );
}
