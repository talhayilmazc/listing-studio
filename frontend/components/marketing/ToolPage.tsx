import { JsonLd, PageHead, PROSE } from "@/components/marketing/Page";
import { breadcrumbs, toolApplication } from "@/lib/marketing/jsonld";
import { TOOL_CTA, type Tool } from "@/lib/marketing/tools";

const LINK = "tap font-medium text-brand-700 underline decoration-brand-100 underline-offset-4 hover:decoration-brand-600";

/** What every tool's page is around the tool: a heading, a short explanation, the guide, a quiet invitation. */
export function ToolPage({ tool, children }: { tool: Tool; children: React.ReactNode }) {
  const path = `/free-tools/${tool.slug}`;
  return (
    <div className={PROSE + " pb-20"}>
      <JsonLd data={breadcrumbs(path)} />
      <JsonLd data={toolApplication(tool)} />
      <PageHead path={path} h1={tool.h1} lead={tool.lead} />
      {children}
      <section aria-labelledby="tool-about" className="mt-10">
        <h2 id="tool-about" className="font-display text-[1.85rem] leading-tight text-slate-900">About this tool</h2>
        <div className="mt-3 space-y-3 text-[1.0625rem] leading-relaxed text-slate-700">
          {tool.explain.map((text) => <p key={text}>{text}</p>)}
        </div>
        <p className="mt-4 flex flex-wrap gap-x-6 gap-y-2 text-[0.9375rem]">
          <a href={tool.guide.href} className={LINK} data-testid="tool-guide">{tool.guide.label}</a>
          {tool.also ? <a key="also" href={tool.also.href} className={LINK}>{tool.also.label}</a> : null}
        </p>
      </section>
      <p className="mt-8 border-t border-slate-200 pt-5 text-[0.9375rem] text-slate-700" data-testid="tool-cta">
        <a href={TOOL_CTA.href} className={LINK}>{TOOL_CTA.label}</a>
      </p>
    </div>
  );
}
