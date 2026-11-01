import type { Metadata } from "next";
import { TagChecker } from "@/components/marketing/TagChecker";
import { ToolPage } from "@/components/marketing/ToolPage";
import { metadataFor } from "@/lib/marketing/pages";
import { TAG_TOOL } from "@/lib/marketing/tools";

export const metadata: Metadata = metadataFor(`/free-tools/${TAG_TOOL.slug}`);

export default function TagCheckerPage() {
  return (
    <ToolPage tool={TAG_TOOL}>
      <TagChecker />
    </ToolPage>
  );
}
