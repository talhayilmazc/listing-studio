import type { Metadata } from "next";
import { FeeCalculator } from "@/components/marketing/FeeCalculator";
import { ToolPage } from "@/components/marketing/ToolPage";
import { feeCalculatorOpen } from "@/lib/freeTools/fees";
import { metadataFor } from "@/lib/marketing/pages";
import { FEE_COMING_SOON, FEE_TOOL } from "@/lib/marketing/tools";

export const metadata: Metadata = metadataFor(`/free-tools/${FEE_TOOL.slug}`);

/** Until the owner has confirmed the fees, the page has no figure on it at all. */
export default function FeeCalculatorPage() {
  return (
    <ToolPage tool={FEE_TOOL}>
      {feeCalculatorOpen()
        ? <FeeCalculator />
        : (
          <section className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="fee-coming-soon">
            <h2 className="font-display text-2xl text-slate-900">{FEE_COMING_SOON.heading}</h2>
            <p className="mt-2 text-[1.0625rem] leading-relaxed text-slate-700">{FEE_COMING_SOON.body}</p>
          </section>
        )}
    </ToolPage>
  );
}
