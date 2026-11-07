import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { AlsoRead, Blocks, InviteCta, JsonLd, OnThisPage, PageHead, Sources } from "@/components/marketing/Page";
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
    <div className="mk-wrap pb-20">
      <JsonLd data={breadcrumbs(path)} />
      <JsonLd data={article(guide)} />
      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,44rem)_minmax(0,1fr)] lg:gap-16">
        <article className="min-w-0">
          <PageHead path={path} h1={guide.h1} lead={guide.lead}>
            <p className="mt-5 flex flex-wrap gap-2" data-testid="guide-dates">
              <time dateTime={guide.published} className="mk-chip">{`Published ${longDate(guide.published)}`}</time>
              <time dateTime={guide.checked} className="mk-chip">{`Last checked ${longDate(guide.checked)}`}</time>
            </p>
          </PageHead>
          <Blocks blocks={guide.body} />
          <Sources sources={guide.sources} checked={guide.checked} />
          <AlsoRead pages={guide.alsoRead} />
          <InviteCta line={`${guide.feature.line} See [${guide.feature.label}](${guide.feature.href}).`} />
        </article>
        <aside className="hidden lg:block">
          <div className="sticky top-24 pt-40">
            <OnThisPage blocks={guide.body} />
          </div>
        </aside>
      </div>
    </div>
  );
}
