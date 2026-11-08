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
      <ul className="space-y-4">
        {GUIDES.map((guide) => (
          <li key={guide.slug} className="mk-card" data-testid="guide-link">
            <h2 className="mk-h3">
              <a href={`/guides/${guide.slug}`} className="tap hover:underline hover:decoration-tag hover:decoration-[3px] hover:underline-offset-[5px]">{guide.h1}</a>
            </h2>
            <p className="mk-body mt-2">{guide.lead}</p>
            <p className="mt-3"><span className="mk-chip bg-white">{`Checked ${longDate(guide.checked)}`}</span></p>
          </li>
        ))}
      </ul>
    </div>
  );
}
