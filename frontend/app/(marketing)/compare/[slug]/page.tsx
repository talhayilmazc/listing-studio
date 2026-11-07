import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { DataTable, InviteCta, JsonLd, PageHead, PROSE, Rich, Sources } from "@/components/marketing/Page";
import { ALTERNATIVES, COMPARES } from "@/lib/marketing/compare";
import { breadcrumbs } from "@/lib/marketing/jsonld";
import { metadataFor } from "@/lib/marketing/pages";

type Props = { params: Promise<{ slug: string }> };

export const dynamicParams = false;
export const generateStaticParams = () => [...COMPARES.map((c) => ({ slug: c.slug })), { slug: ALTERNATIVES.slug }];

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return metadataFor(`/compare/${(await params).slug}`);
}

const H2 = "mk-h2 pt-10 !text-[1.5rem] sm:!text-[1.75rem]";

function Alternatives() {
  const path = `/compare/${ALTERNATIVES.slug}`;
  return (
    <div className={PROSE + " pb-20"}>
      <JsonLd data={breadcrumbs(path)} />
      <PageHead path={path} h1={ALTERNATIVES.h1} lead={ALTERNATIVES.lead} />
      <ul className="space-y-6">
        {ALTERNATIVES.options.map((option) => (
          <li key={option.name} className="border-t border-heather pt-5">
            <h2 className="mk-h3 !text-[1.375rem]"><a href={option.href} className="mk-link">{option.name}</a></h2>
            <p className="mk-body mt-2">{option.body}</p>
            <p className="mk-body mt-2"><strong className="font-semibold text-ink">Fits best: </strong><span>{option.fits}</span></p>
          </li>
        ))}
      </ul>
      <p className="mt-6 mk-body">{ALTERNATIVES.listyro}</p>
      <p className="mk-small mt-4" data-testid="last-checked">Last checked 1 November 2026. The comparison pages link to each product&apos;s own pages.</p>
      <InviteCta line="Listyro is invite-only while we onboard sellers in small groups." />
    </div>
  );
}

export default async function ComparePage({ params }: Props) {
  const { slug } = await params;
  if (slug === ALTERNATIVES.slug) return <Alternatives />;
  const c = COMPARES.find((item) => item.slug === slug);
  if (!c) notFound();
  const path = `/compare/${slug}`;
  return (
    <div className={PROSE + " pb-20"}>
      <JsonLd data={breadcrumbs(path)} />
      <PageHead path={path} h1={c.h1} lead={c.lead} />
      <DataTable
        caption={`Side by side, from each product's own pages`}
        head={["", "Listyro", c.them]}
        rows={c.rows.map((row) => [row.label, row.listyro, row.them])}
      />
      <h2 className={H2}>{`What ${c.them} does that Listyro does not`}</h2>
      <ul className="mt-3 list-disc space-y-2 pl-5 mk-body" data-testid="they-do">
        {c.theirs.map((item) => <li key={item}><Rich text={item} /></li>)}
      </ul>
      <h2 className={H2}>What Listyro does differently</h2>
      <ul className="mt-3 list-disc space-y-2 pl-5 mk-body">
        {c.ours.map((item) => <li key={item}><Rich text={item} /></li>)}
      </ul>
      <h2 className={H2}>Which fits you</h2>
      <p className="mt-3 mk-body">{`Choose ${c.them} if ${c.chooseThem}`}</p>
      <p className="mt-3 mk-body">{`Choose Listyro if ${c.chooseUs}`}</p>
      <Sources sources={c.sources} checked={c.checked} />
      <InviteCta line="Listyro is invite-only while we onboard sellers in small groups." />
    </div>
  );
}
