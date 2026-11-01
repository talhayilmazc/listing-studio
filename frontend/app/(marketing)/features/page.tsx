import type { Metadata } from "next";
import { JsonLd, PageHead, Shot, WRAP } from "@/components/marketing/Page";
import { FEATURES, FEATURES_PAGE } from "@/lib/marketing/features";
import { breadcrumbs } from "@/lib/marketing/jsonld";
import { metadataFor } from "@/lib/marketing/pages";

export const metadata: Metadata = metadataFor("/features");

export default function Features() {
  return (
    <div className={WRAP + " pb-20"}>
      <JsonLd data={breadcrumbs("/features")} />
      <PageHead path="/features" h1={FEATURES_PAGE.h1} lead={FEATURES_PAGE.lead} />
      <ul className="grid grid-cols-1 gap-8 md:grid-cols-2">
        {FEATURES.map((feature) => (
          <li key={feature.slug} className="min-w-0" data-testid="feature-link">
            <a href={`/features/${feature.slug}`} className="group block">
              <Shot screen={feature.screen} sizes="(min-width: 768px) 46vw, 100vw" />
              <h2 className="mt-4 text-[1.1875rem] font-medium text-slate-900 group-hover:underline">{feature.name}</h2>
              <p className="mt-1 text-[0.9375rem] leading-relaxed text-slate-700">{feature.summary}</p>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
