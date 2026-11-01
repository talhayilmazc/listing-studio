import type { Metadata } from "next";
import { TitleChecker } from "@/components/marketing/TitleChecker";
import { ToolPage } from "@/components/marketing/ToolPage";
import { metadataFor } from "@/lib/marketing/pages";
import { TITLE_TOOL } from "@/lib/marketing/tools";

export const metadata: Metadata = metadataFor(`/free-tools/${TITLE_TOOL.slug}`);

export default function TitleCheckerPage() {
  return (
    <ToolPage tool={TITLE_TOOL}>
      <TitleChecker />
    </ToolPage>
  );
}
