import type { Metadata } from "next";
import { HelpFilter } from "@/components/marketing/HelpFilter";
import { JsonLd, PageHead, PROSE, Rich } from "@/components/marketing/Page";
import { HELP } from "@/lib/marketing/help";
import { breadcrumbs, faqPage } from "@/lib/marketing/jsonld";
import { metadataFor } from "@/lib/marketing/pages";

export const metadata: Metadata = metadataFor("/help");

export default function Help() {
  return (
    <div className={PROSE + " pb-20"}>
      <JsonLd data={breadcrumbs("/help")} />
      <JsonLd data={faqPage(HELP.groups.flatMap((group) => group.items))} />
      <PageHead path="/help" h1={HELP.h1} lead={HELP.lead} />
      <HelpFilter />
      <div className="space-y-12">
        {HELP.groups.map((group) => (
          <section key={group.title} data-help-group>
            <h2 className="mk-h2 !text-[1.5rem] sm:!text-[1.75rem]">{group.title}</h2>
            <dl className="mt-5">
              {group.items.map((item) => (
                <div key={item.q} className="border-t-2 border-ink pb-7 pt-5" data-help-item>
                  <dt className="text-[1.3125rem] font-[850] leading-tight text-ink">{item.q}</dt>
                  <dd className="mk-body mt-2.5"><Rich text={item.a} /></dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </div>
  );
}
