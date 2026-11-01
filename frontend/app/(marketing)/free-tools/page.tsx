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
          <li key={tool.slug} className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="free-tool">
            <h2 className="text-[1.1875rem] font-medium text-slate-900">
              <a href={`/free-tools/${tool.slug}`} className="tap text-brand-700 underline decoration-brand-100 underline-offset-4 hover:decoration-brand-600">{tool.name}</a>
              {tool.slug === FEE_TOOL.slug && !feeCalculatorOpen()
                ? <span key="soon" className="ml-2 rounded-full border border-slate-200 px-2 py-0.5 align-middle text-xs font-medium text-slate-600" data-testid="tool-status">{FEE_COMING_SOON.status}</span>
                : null}
            </h2>
            <p className="mt-2 text-[0.9375rem] leading-relaxed text-slate-700">{tool.summary}</p>
            <p className="mt-2 text-[0.9375rem]">
              <a href={tool.guide.href} className="tap font-medium text-brand-700 underline decoration-brand-100 underline-offset-4">{tool.guide.label}</a>
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
