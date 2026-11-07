import type { Metadata } from "next";
import { JsonLd, PageHead, PROSE } from "@/components/marketing/Page";
import { feeCalculatorOpen } from "@/lib/freeTools/fees";
import { FREE_TOOLS } from "@/lib/marketing/help";
import { breadcrumbs } from "@/lib/marketing/jsonld";
import { ideasOn, metadataFor } from "@/lib/marketing/pages";
import { FEE_COMING_SOON, FEE_TOOL, IDEAS_TOOL, TOOLS } from "@/lib/marketing/tools";

export const metadata: Metadata = metadataFor("/free-tools");

export default function FreeTools() {
  const tools = ideasOn() ? [...TOOLS, IDEAS_TOOL] : TOOLS;
  return (
    <div className={PROSE + " pb-20"}>
      <JsonLd data={breadcrumbs("/free-tools")} />
      <PageHead path="/free-tools" h1={FREE_TOOLS.h1} lead={FREE_TOOLS.lead} />
      <ul className="space-y-4">
        {tools.map((tool) => (
          <li key={tool.slug} className="border border-heather bg-white p-5" data-testid="free-tool">
            <h2 className="mk-h3 !text-[1.375rem]">
              <a href={`/free-tools/${tool.slug}`} className="tap mk-link">{tool.name}</a>
              {tool.slug === FEE_TOOL.slug && !feeCalculatorOpen()
                ? <span key="soon" className="mk-chip ml-2 align-middle" data-testid="tool-status">{FEE_COMING_SOON.status}</span>
                : null}
            </h2>
            <p className="mt-2 text-[0.9375rem] leading-relaxed text-slate-700">{tool.summary}</p>
            <p className="mt-2 text-[0.9375rem]">
              <a href={tool.guide.href} className="tap mk-link">{tool.guide.label}</a>
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
