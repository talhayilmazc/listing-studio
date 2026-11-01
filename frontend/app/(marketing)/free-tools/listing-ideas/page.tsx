import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ListingIdeas } from "@/components/marketing/ListingIdeas";
import { ToolPage } from "@/components/marketing/ToolPage";
import { ideasOn, metadataFor } from "@/lib/marketing/pages";
import { IDEAS_TOOL } from "@/lib/marketing/tools";

export const metadata: Metadata = metadataFor(`/free-tools/${IDEAS_TOOL.slug}`);

/**
 * Asked at request time, and never cached: this one tool spends money per visitor, so it is not in
 * lib/marketing/routes.mjs (docs/free-tools.md). With its flag off the page does not exist.
 */
export const dynamic = "force-dynamic";

export default function ListingIdeasPage() {
  if (!ideasOn()) notFound();
  return (
    <ToolPage tool={IDEAS_TOOL}>
      <ListingIdeas />
    </ToolPage>
  );
}
