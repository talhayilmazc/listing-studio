import type { Block, Source } from "@/lib/marketing/content";
import { linksIn, longDate } from "@/lib/marketing/content";
import { trail } from "@/lib/marketing/pages";
import type { Screen } from "@/lib/marketing/screens";

/**
 * The parts every long public page is made of. Static server markup. Links are plain <a>: a click
 * loads a whole page, which the edge can answer, and no router request reaches the server.
 */

export const WRAP = "mx-auto w-full max-w-[1240px] px-4 sm:px-6";
export const PROSE = "mx-auto w-full max-w-[46rem] px-4 sm:px-6";
const LINK = "font-medium text-brand-700 underline decoration-brand-100 underline-offset-4 hover:decoration-brand-600";

/** One block of JSON-LD. "<" is escaped so no text can end the script element. */
export function JsonLd({ data }: { data: unknown }) {
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
    <nav aria-label="Breadcrumb" className="text-sm text-slate-600">
      <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
        {pages.map((page, i) => (
          <li key={page.path} className="flex items-center gap-1.5">
            {i > 0 && <span key="sep" aria-hidden>/</span>}
            {i < pages.length - 1
              ? <a key="link" href={page.path} className="tap hover:text-slate-900">{page.crumb}</a>
              : <span key="here" aria-current="page" className="text-slate-800">{page.crumb}</span>}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function PageHead({ path, h1, lead, children }: { path: string; h1: string; lead: string; children?: React.ReactNode }) {
  return (
    <header className="pb-8 pt-8 sm:pb-10 sm:pt-12">
      <Crumbs path={path} />
      <h1 className="mt-5 font-display text-[clamp(2.2rem,5vw,3.5rem)] leading-[1.06] text-slate-900">{h1}</h1>
      <p className="mt-4 max-w-[44rem] text-[1.0625rem] leading-relaxed text-slate-700 sm:text-lg"><Rich text={lead} /></p>
      {children}
    </header>
  );
}

/** A product screenshot from the synthetic demo account, in the width the screen needs. */
export function Shot({ screen, priority = false, sizes = "(min-width: 768px) 46rem, 100vw" }: { screen: Screen; priority?: boolean; sizes?: string }) {
  return (
    <figure className="relative overflow-hidden rounded-2xl border border-slate-200 bg-white">
      <img
        src={screen.src}
        srcSet={`${screen.small} 560w, ${screen.src} 1120w`}
        sizes={sizes}
        alt={screen.alt}
        width={1120}
        height={840}
        loading={priority ? "eager" : "lazy"}
        decoding="async"
        {...(priority ? { fetchPriority: "high" as const } : {})}
        className="h-auto w-full"
      />
      <figcaption className="absolute bottom-2.5 right-2.5 rounded-full border border-slate-200 bg-white/95 px-2 py-0.5 text-xs font-medium text-slate-600">Sample data</figcaption>
    </figure>
  );
}

/** The blocks of a guide or a feature page. */
export function Blocks({ blocks }: { blocks: Block[] }) {
  return (
    <div className="space-y-4 text-[1.0625rem] leading-relaxed text-slate-700">
      {blocks.map((block, i) => {
        if ("h2" in block) return <h2 key={i} id={block.id} className="scroll-mt-20 pt-6 font-display text-[1.85rem] leading-tight text-slate-900">{block.h2}</h2>;
        if ("p" in block) return <p key={i}><Rich text={block.p} /></p>;
        if ("ul" in block) return <ul key={i} className="list-disc space-y-2 pl-5">{block.ul.map((item) => <li key={item}><Rich text={item} /></li>)}</ul>;
        if ("ol" in block) return <ol key={i} className="list-decimal space-y-2 pl-5">{block.ol.map((item) => <li key={item}><Rich text={item} /></li>)}</ol>;
        if ("note" in block) return <p key={i} className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-base"><Rich text={block.note} /></p>;
        return <DataTable key={i} caption={block.table.caption} head={block.table.head} rows={block.table.rows} />;
      })}
    </div>
  );
}

/** A table that scrolls inside its own frame on a phone; the page itself never scrolls sideways. */
export function DataTable({ caption, head, rows }: { caption: string; head: string[]; rows: string[][] }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white" tabIndex={0} role="region" aria-label={caption}>
      <table className="w-full min-w-[32rem] border-collapse text-left text-[0.9375rem]">
        <caption className="px-4 pb-1 pt-3 text-left text-sm text-slate-600">{caption}</caption>
        <thead>
          <tr className="border-b border-slate-200 text-slate-900">
            {head.map((cell, i) => <th key={i} scope="col" className="px-4 py-2 font-medium">{cell}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r} className="border-b border-slate-100 align-top last:border-0">
              {row.map((cell, c) => (c === 0
                ? <th key={c} scope="row" className="px-4 py-2.5 font-medium text-slate-900">{cell}</th>
                : <td key={c} className="px-4 py-2.5 text-slate-700">{cell}</td>))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Sources({ sources, checked }: { sources: Source[]; checked: string }) {
  return (
    <section aria-labelledby="sources-title" className="mt-10 border-t border-slate-200 pt-6">
      <h2 id="sources-title" className="font-display text-2xl text-slate-900">Sources</h2>
      <p className="mt-2 text-sm text-slate-600">{`Last checked ${longDate(checked)}. These pages change, and the linked page is what counts.`}</p>
      <ul className="mt-3 space-y-1.5 text-[0.9375rem]">
        {sources.map((source) => (
          <li key={source.url}><a href={source.url} rel="noopener" className={LINK}>{source.label}</a></li>
        ))}
      </ul>
    </section>
  );
}

export function InviteCta({ line }: { line: string }) {
  return (
    <aside className="mt-10 rounded-2xl border border-slate-200 bg-white p-6 sm:p-8">
      <p className="text-[1.0625rem] leading-relaxed text-slate-800"><Rich text={line} /></p>
      <p className="mt-4 flex flex-wrap gap-3">
        <a href="/request-invite" className="btn-primary px-5 py-3 text-[0.9375rem]">Request an invite</a>
        <a href="/pricing" className="btn-secondary px-5 py-3 text-[0.9375rem]">See pricing</a>
      </p>
    </aside>
  );
}
