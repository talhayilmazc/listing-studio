import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Blocks, InviteCta, JsonLd, PageHead, PROSE, Shot } from "@/components/marketing/Page";
import { FEATURES } from "@/lib/marketing/features";
import { breadcrumbs } from "@/lib/marketing/jsonld";
import { metadataFor } from "@/lib/marketing/pages";

type Props = { params: Promise<{ slug: string }> };

export const dynamicParams = false;
export const generateStaticParams = () => FEATURES.map((feature) => ({ slug: feature.slug }));

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return metadataFor(`/features/${(await params).slug}`);
}

export default async function FeaturePage({ params }: Props) {
  const { slug } = await params;
  const feature = FEATURES.find((f) => f.slug === slug);
  if (!feature) notFound();
  const path = `/features/${slug}`;
  return (
    <div className={PROSE + " pb-20"}>
      <JsonLd data={breadcrumbs(path)} />
      <PageHead path={path} h1={feature.h1} lead={feature.lead} />
      <Shot screen={feature.screen} priority />
      <div className="mt-6">
        <Blocks blocks={feature.body} />
      </div>
      <InviteCta line={`Related guide: [${feature.guide.label}](${feature.guide.href}). Listyro is invite-only while we onboard sellers in small groups.`} />
    </div>
  );
}
