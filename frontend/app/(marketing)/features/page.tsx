import type { Metadata } from "next";
import { JsonLd, PageHead, WRAP } from "@/components/marketing/Page";
import { FEATURES, FEATURES_PAGE } from "@/lib/marketing/features";
import { breadcrumbs } from "@/lib/marketing/jsonld";
import { metadataFor } from "@/lib/marketing/pages";

export const metadata: Metadata = metadataFor("/features");

export default function Features() {
  return (
    <div className={WRAP + " pb-20"}>
      <JsonLd data={breadcrumbs("/features")} />
      <PageHead path="/features" h1={FEATURES_PAGE.h1} lead={FEATURES_PAGE.lead} />
      <ul className="lx-safe">
        {FEATURES.map((feature) => (
          <li key={feature.slug} className="mk-card min-w-0" data-testid="feature-link">
            <h2 className="mk-h3"><a href={`/features/${feature.slug}`} className="tap hover:underline hover:decoration-tag hover:decoration-[3px] hover:underline-offset-[5px]">{feature.name}</a></h2>
            <p className="mk-body mt-2 max-w-[52ch]">{feature.summary}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
