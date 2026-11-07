import { jsonLdNames, refuseEtsy } from "@/lib/marketing/brandRule";
import type { Block, Source } from "@/lib/marketing/content";
import { linksIn, longDate } from "@/lib/marketing/content";
import { PRIMARY_CTA } from "@/lib/marketing/cta";
import { trail } from "@/lib/marketing/pages";
import type { Screen } from "@/lib/marketing/screens";

/**
 * The parts every long public page is made of. Static server markup. Links are plain <a>: a click
 * loads a whole page, which the edge can answer, and no router request reaches the server.
 */

export const WRAP = "mk-wrap";
/** A reading column: the measure stays under 75 characters. */
export const PROSE = "mx-auto w-full max-w-[44rem] px-4 sm:px-6";
const LINK = "mk-link";

/** One block of JSON-LD. "<" is escaped so no text can end the script element. */
export function JsonLd({ data }: { data: unknown }) {
  for (const name of jsonLdNames(data)) refuseEtsy(name, "a JSON-LD name or headline");
  return <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(data).replace(/</g, "\\u003c") }} />;
}

/** Text with [label](href) links in it. */
export function Rich({ text }: { text: string }) {
  const pieces: React.ReactNode[] = [];
  let at = 0;
  linksIn(text).forEach((link, i) => {
    const marker = `[${link.label}](${link.href})`;
    const start = text.indexOf(marker, at);
    pieces.push(<span key={`t${i}`}>{text.slice(at, start)}</span>);
    const external = /^https?:/.test(link.href);
    pieces.push(
      <a key={`l${i}`} href={link.href} className={LINK} {...(external ? { rel: "noopener" } : {})}>
        {link.label}
      </a>,
    );
    at = start + marker.length;
  });
  pieces.push(<span key="end">{text.slice(at)}</span>);
  return <>{pieces}</>;
}

export function Crumbs({ path }: { path: string }) {
  const pages = trail(path);
  return (
    <nav aria-label="Breadcrumb" className="text-[0.9375rem] text-slate-600">
      <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
        {pages.map((page, i) => (
          <li key={page.path} className="flex items-center gap-1.5">
            {i > 0 && <span key="sep" aria-hidden>/</span>}
            {i < pages.length - 1
              ? <a key="link" href={page.path} className="tap hover:text-ink hover:underline">{page.crumb}</a>
              : <span key="here" aria-current="page" className="font-medium text-ink">{page.crumb}</span>}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function PageHead({ path, h1, lead, children }: { path: string; h1: string; lead: string; children?: React.ReactNode }) {
  refuseEtsy(h1, `the h1 of ${path}`);
  return (
    <header className="pb-8 pt-8 sm:pb-10 sm:pt-12">
      <Crumbs path={path} />
      <h1 className="mk-h1 mt-5 max-w-[22ch]">{h1}</h1>
      <p className="mk-lead mt-4"><Rich text={lead} /></p>
      {children}
    </header>
  );
}

/**
 * A crop of the real app from the synthetic demo account (docs/marketing-screens.md; scripts/marketing-shots.mjs
 * takes them at twice their size). Shown at its natural size, never wider, so its text reads as it does in the
 * app; on a narrow screen it scales down with the column.
 */
export function Shot({ screen, priority = false, className = "" }: { screen: Screen; priority?: boolean; className?: string }) {
  return (
    <figure className={"mk-crop " + className} style={{ maxWidth: screen.width }}>
      <picture>
        <source media="(max-width: 639px)" srcSet={screen.phone.src} width={screen.phone.width} height={screen.phone.height} />
        <img
          src={screen.src}
          alt={screen.alt}
          width={screen.width}
          height={screen.height}
          loading={priority ? "eager" : "lazy"}
          decoding="async"
          {...(priority ? { fetchPriority: "high" as const } : {})}
          className="block h-auto w-full"
        />
      </picture>
      <figcaption className="absolute bottom-2 right-2 rounded-[3px] border border-heather bg-white px-1.5 py-0.5 text-xs font-semibold text-slate-600">Sample data</figcaption>
    </figure>
  );
}

/** An anchor for a heading: its own id, or one made from its words. */
export const headingId = (block: { h2: string; id?: string }) =>
  block.id ?? block.h2.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

/** "On this page": the guide's headings, for a long read. */
export function OnThisPage({ blocks }: { blocks: Block[] }) {
  const heads = blocks.filter((b): b is { h2: string; id?: string } => "h2" in b);
  if (heads.length < 3) return null;
  return (
    <nav aria-label="On this page" className="text-[0.9375rem]" data-testid="on-this-page">
      <h2 className="font-bold text-ink">On this page</h2>
      <ul className="mt-3 space-y-2 border-l-2 border-heather">
        {heads.map((head) => (
          <li key={headingId(head)}><a href={`#${headingId(head)}`} className="tap -ml-0.5 block border-l-2 border-transparent pl-3 text-slate-600 hover:border-ink hover:text-ink">{head.h2}</a></li>
        ))}
      </ul>
    </nav>
  );
}

/** The blocks of a guide or a feature page. */
export function Blocks({ blocks }: { blocks: Block[] }) {
  return (
    <div className="mk-body space-y-5">
      {blocks.map((block, i) => {
        if ("h2" in block) return <h2 key={i} id={headingId(block)} className="mk-h2 scroll-mt-24 pt-6 !text-[1.5rem] sm:!text-[1.75rem]">{block.h2}</h2>;
        if ("p" in block) return <p key={i}><Rich text={block.p} /></p>;
        if ("ul" in block) return <ul key={i} className="list-disc space-y-2 pl-5">{block.ul.map((item) => <li key={item}><Rich text={item} /></li>)}</ul>;
        if ("ol" in block) return <ol key={i} className="list-decimal space-y-2 pl-5">{block.ol.map((item) => <li key={item}><Rich text={item} /></li>)}</ol>;
        if ("note" in block) return <p key={i} className="border-l-4 border-tag bg-fabric px-4 py-3 text-base"><Rich text={block.note} /></p>;
        return <DataTable key={i} caption={block.table.caption} head={block.table.head} rows={block.table.rows} />;
      })}
    </div>
  );
}

/** A table that scrolls inside its own frame on a phone; the page itself never scrolls sideways. */
export function DataTable({ caption, head, rows }: { caption: string; head: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto border-y-2 border-ink bg-white" tabIndex={0} role="region" aria-label={caption}>
      <table className="w-full min-w-[32rem] border-collapse text-left text-[0.9375rem]">
        <caption className="pb-2 pt-3 text-left text-sm text-slate-600">{caption}</caption>
        <thead>
          <tr className="border-b border-ink bg-fabric text-ink">
            {head.map((cell, i) => <th key={i} scope="col" className="px-3 py-2.5 font-bold">{cell}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r} className="border-b border-heather align-top last:border-0">
              {row.map((cell, c) => (c === 0
                ? <th key={c} scope="row" className="px-3 py-3 font-semibold text-ink">{cell}</th>
                : <td key={c} className="px-3 py-3 text-slate-700">{cell}</td>))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Sources({ sources, checked }: { sources: Source[]; checked: string }) {
  return (
    <section aria-labelledby="sources-title" className="mt-12 border-t border-heather pt-6">
      <h2 id="sources-title" className="mk-h3">Sources</h2>
      <p className="mt-2 text-sm text-slate-600">{`Last checked ${longDate(checked)}. These pages change, and the linked page is what counts.`}</p>
      <ul className="mt-3 space-y-1.5 text-[0.9375rem]">
        {sources.map((source) => (
          <li key={source.url}><a href={source.url} rel="noopener" className={LINK}>{source.label}</a></li>
        ))}
      </ul>
    </section>
  );
}

/** Etsy's own pages on the subject. A guide relies on them for nothing, so they are not its sources. */
export function AlsoRead({ pages }: { pages: Source[] }) {
  return (
    <section aria-labelledby="also-read-title" className="mt-8" data-testid="also-read">
      <h2 id="also-read-title" className="mk-h3">Also read on Etsy</h2>
      <p className="mt-2 text-sm text-slate-600">Etsy&apos;s own pages on this subject. Nothing above relies on them, and where they differ from our advice, follow Etsy.</p>
      <ul className="mt-3 space-y-1.5 text-[0.9375rem]">
        {pages.map((page) => (
          <li key={page.url}><a href={page.url} rel="noopener" className={LINK}>{page.label}</a></li>
        ))}
      </ul>
    </section>
  );
}

export function InviteCta({ line }: { line: string }) {
  return (
    <aside className="mt-12 bg-fabric p-6 sm:p-8">
      <p className="mk-body text-ink"><Rich text={line} /></p>
      <p className="mt-5 flex flex-wrap gap-3">
        <a href={PRIMARY_CTA.href} className="mk-btn">{PRIMARY_CTA.label}</a>
        <a href="/pricing" className="mk-btn-line">See pricing</a>
      </p>
    </aside>
  );
}
