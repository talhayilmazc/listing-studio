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
      <ul className="space-y-4">
        {COMPARES.map((c) => (
          <li key={c.slug} className="mk-card" data-testid="compare-link">
            <h2 className="mk-h3"><a href={`/compare/${c.slug}`} className="tap hover:underline hover:decoration-tag hover:decoration-[3px] hover:underline-offset-[5px]">{c.h1}</a></h2>
            <p className="mk-body mt-2">{c.lead}</p>
          </li>
        ))}
        <li key={ALTERNATIVES.slug} className="mk-card" data-testid="compare-link">
          <h2 className="mk-h3"><a href={`/compare/${ALTERNATIVES.slug}`} className="tap hover:underline hover:decoration-tag hover:decoration-[3px] hover:underline-offset-[5px]">{ALTERNATIVES.h1}</a></h2>
          <p className="mk-body mt-2">{ALTERNATIVES.lead}</p>
        </li>
      </ul>
    </div>
  );
}
