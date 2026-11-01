import type { Metadata } from "next";
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
      <div className="space-y-10">
        {HELP.groups.map((group) => (
          <section key={group.title}>
            <h2 className="font-display text-[1.85rem] leading-tight text-slate-900">{group.title}</h2>
            <dl className="mt-3 divide-y divide-slate-200 border-y border-slate-200">
              {group.items.map((item) => (
                <div key={item.q} className="py-4">
                  <dt className="text-[1.0625rem] font-medium text-slate-900">{item.q}</dt>
                  <dd className="mt-1 text-[0.9375rem] leading-relaxed text-slate-700"><Rich text={item.a} /></dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </div>
  );
}
