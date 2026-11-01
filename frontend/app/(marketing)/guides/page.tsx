import type { Metadata } from "next";
import { JsonLd, PageHead, PROSE } from "@/components/marketing/Page";
import { longDate } from "@/lib/marketing/content";
import { GUIDES, GUIDES_PAGE } from "@/lib/marketing/guides";
import { breadcrumbs } from "@/lib/marketing/jsonld";
import { metadataFor } from "@/lib/marketing/pages";

export const metadata: Metadata = metadataFor("/guides");

export default function Guides() {
  return (
    <div className={PROSE + " pb-20"}>
      <JsonLd data={breadcrumbs("/guides")} />
      <PageHead path="/guides" h1={GUIDES_PAGE.h1} lead={GUIDES_PAGE.lead} />
      <ul className="divide-y divide-slate-200 border-y border-slate-200">
        {GUIDES.map((guide) => (
          <li key={guide.slug} className="py-5" data-testid="guide-link">
            <h2 className="text-[1.1875rem] font-medium leading-snug text-slate-900">
              <a href={`/guides/${guide.slug}`} className="tap hover:underline">{guide.h1}</a>
            </h2>
            <p className="mt-1 text-[0.9375rem] leading-relaxed text-slate-700">{guide.lead}</p>
            <p className="mt-1 text-sm text-slate-600">{`Checked ${longDate(guide.checked)}`}</p>
          </li>
        ))}
      </ul>
    </div>
  );
}
