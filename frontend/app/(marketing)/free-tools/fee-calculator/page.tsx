import type { Metadata } from "next";
import { FeeCalculator } from "@/components/marketing/FeeCalculator";
import { ToolPage } from "@/components/marketing/ToolPage";
import { feeCalculatorOpen } from "@/lib/freeTools/fees";
import { metadataFor } from "@/lib/marketing/pages";
import { FEE_COMING_SOON, FEE_TOOL } from "@/lib/marketing/tools";

export const metadata: Metadata = metadataFor(`/free-tools/${FEE_TOOL.slug}`);

/** Until the owner has confirmed the fees, the page has no figure on it at all. */
export default function FeeCalculatorPage() {
  const open = feeCalculatorOpen();
  return (
    <ToolPage tool={open ? FEE_TOOL : { ...FEE_TOOL, lead: FEE_COMING_SOON.lead, explain: FEE_COMING_SOON.explain }}>
      {open
        ? <FeeCalculator />
        : (
          <section className="border border-heather bg-white p-5" data-testid="fee-coming-soon">
            <h2 className="mk-h3 !text-[1.375rem]">{FEE_COMING_SOON.heading}</h2>
            <p className="mt-2 text-[1.0625rem] leading-relaxed text-slate-700">{FEE_COMING_SOON.body}</p>
          </section>
        )}
    </ToolPage>
  );
}
