import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AlsoRead, Blocks, InviteCta, JsonLd, PageHead, PROSE, Sources } from "@/components/marketing/Page";
import { longDate } from "@/lib/marketing/content";
import { GUIDES } from "@/lib/marketing/guides";
import { article, breadcrumbs } from "@/lib/marketing/jsonld";
import { metadataFor } from "@/lib/marketing/pages";

type Props = { params: Promise<{ slug: string }> };

export const dynamicParams = false;
export const generateStaticParams = () => GUIDES.map((guide) => ({ slug: guide.slug }));

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return metadataFor(`/guides/${(await params).slug}`);
}

export default async function GuidePage({ params }: Props) {
  const { slug } = await params;
  const guide = GUIDES.find((g) => g.slug === slug);
  if (!guide) notFound();
  const path = `/guides/${slug}`;
  return (
    <article className={PROSE + " pb-20"}>
      <JsonLd data={breadcrumbs(path)} />
      <JsonLd data={article(guide)} />
      <PageHead path={path} h1={guide.h1} lead={guide.lead}>
        <p className="mt-4 text-sm text-slate-600" data-testid="guide-dates">
          <time dateTime={guide.published}>{`Published ${longDate(guide.published)}`}</time>
          <span> · </span>
          <time dateTime={guide.checked}>{`Last checked ${longDate(guide.checked)}`}</time>
        </p>
      </PageHead>
      <Blocks blocks={guide.body} />
      <Sources sources={guide.sources} checked={guide.checked} />
      <AlsoRead pages={guide.alsoRead} />
      <InviteCta line={`${guide.feature.line} See [${guide.feature.label}](${guide.feature.href}).`} />
    </article>
  );
}
