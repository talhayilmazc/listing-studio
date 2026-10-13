import Image from "next/image";
import Link from "next/link";
import { HOME, type Visual } from "@/lib/homeCopy";
import { Mockup } from "./Mockup";

/**
 * The home page's sections. Static server markup: no script, no session.
 * Every word comes from lib/homeCopy.ts, where the page's rules are checked.
 */

export function Eyebrow({ children, dark = false }: { children: React.ReactNode; dark?: boolean }) {
  return (
    <p className={"text-sm font-medium uppercase tracking-[0.12em] " + (dark ? "text-violet-300" : "text-brand-700")}>{children}</p>
  );
}

export function SectionTitle({ children, id }: { children: React.ReactNode; id?: string }) {
  return (
    <h2 id={id} className="mt-3 font-display text-[clamp(2.1rem,4.4vw,3.4rem)] leading-[1.05] text-slate-900">
      {children}
    </h2>
  );
}

const WRAP = "mx-auto w-full max-w-[1240px] px-4 sm:px-6";

function Check({ className = "" }: { className?: string }) {
  return (
    <svg aria-hidden viewBox="0 0 16 16" className={"h-3.5 w-3.5 shrink-0 " + className} fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M3.5 8.5l3 3 6-7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* ---------- What sets Listyro apart ---------- */

function TemplateDrawing() {
  const d = HOME.templateDrawing;
  return (
    <div className="flex h-full w-full items-center gap-3 p-4 sm:gap-6 sm:p-7">
      <div className="w-[34%] max-w-[10.5rem] shrink-0">
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
          <Mockup art="mountain" label="" className="aspect-square w-full" />
          <p className="border-t border-slate-200 px-2.5 py-1.5 text-[11px] leading-tight text-slate-600 sm:text-xs">
            <span className="block font-medium text-slate-900">{d.from}</span>
            <span className="block">{d.fromNote}</span>
          </p>
        </div>
      </div>
      <svg aria-hidden viewBox="0 0 24 24" className="h-5 w-5 shrink-0 text-brand-600" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path d="M4 12h15M13 6l6 6-6 6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div className="min-w-0 flex-1">
        <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-500 sm:text-xs">{d.to}</p>
        <ul className="mt-1.5 space-y-1 sm:mt-2.5 sm:space-y-1.5">
          {d.copied.map((c) => (
            <li key={c} className="flex items-center gap-2 text-xs text-slate-800 sm:text-sm">
              <Check className="text-emerald-600" />
              <span className="min-w-0 truncate">{c}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function TrademarkDrawing() {
  const d = HOME.trademarkDrawing;
  return (
    <div className="flex h-full w-full flex-col justify-center gap-2.5 p-4 sm:gap-3.5 sm:p-7">
      <div>
        <p className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-500 sm:text-xs">{d.field}</p>
        <p className="mt-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-900 sm:text-sm">
          <span>{`${d.before} `}</span>
          <span className="rounded bg-rose-100 px-1 text-rose-700 underline decoration-rose-400 decoration-wavy underline-offset-4">{d.flagged}</span>
          <span>{` ${d.after}`}</span>
        </p>
      </div>
      <p className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs leading-snug text-rose-700 sm:text-sm">{d.finding}</p>
      <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-snug text-amber-700 sm:text-sm">{d.artwork}</p>
      <p className="flex items-center gap-2 text-xs text-slate-600">
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-rose-500" />
        <span>{d.state}</span>
      </p>
    </div>
  );
}

function FeatureVisual({ visual }: { visual: Visual }) {
  const a = HOME.apart;
  return (
    <figure className="relative flex aspect-[4/3] items-center justify-center overflow-hidden border-b border-slate-200 bg-stone-50">
      {visual.kind === "screen" ? (
        // A crop of the real product at 2x: sized for the card, loaded when it nears the screen.
        // One taken at phone width fills a phone's card; on a wide card it sits as a panel
        // at about its own size, so its text is not blown up.
        <Image
          src={visual.src}
          alt={visual.alt}
          width={1120}
          height={840}
          sizes="(min-width: 1240px) 588px, (min-width: 768px) 48vw, 100vw"
          className={
            "h-full w-full object-cover object-top" +
            (visual.panel ? " md:h-auto md:w-[70%] md:rounded-xl md:border md:border-slate-200 md:shadow-[0_18px_40px_-24px_rgba(28,25,23,.35)]" : "")
          }
        />
      ) : visual.kind === "template" ? (
        <TemplateDrawing />
      ) : (
        <TrademarkDrawing />
      )}
      <figcaption className="absolute bottom-2.5 right-2.5 rounded-full border border-slate-200 bg-white/95 px-2 py-0.5 text-[11px] font-medium text-slate-500">
        {visual.kind === "screen" ? a.screenLabel : a.drawingLabel}
      </figcaption>
    </figure>
  );
}

export function Apart() {
  const a = HOME.apart;
  return (
    <section id={a.id} aria-labelledby="apart-title" className="scroll-mt-20 border-t border-slate-200 bg-white">
      <div className={WRAP + " py-20 lg:py-28"}>
        <div className="max-w-[44rem]">
          <Eyebrow>{a.eyebrow}</Eyebrow>
          <SectionTitle id="apart-title">{a.heading}</SectionTitle>
          <p className="mt-5 text-[1.0625rem] leading-relaxed text-slate-600">{a.intro}</p>
        </div>
        <ul className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-2 lg:mt-16 lg:gap-8">
          {a.cards.map((card) => (
            <li key={card.title} className="card min-w-0 overflow-hidden" data-testid="feature-card">
              <FeatureVisual visual={card.visual} />
              <div className="p-5 sm:p-7">
                <h3 className="text-[1.1875rem] font-medium leading-snug text-slate-900">{card.title}</h3>
                <p className="mt-2 text-[0.9375rem] leading-relaxed text-slate-600">{card.body}</p>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/* ---------- Built with sellers ---------- */

export function Built() {
  const b = HOME.built;
  return (
    <section id={b.id} aria-labelledby="built-title" className="scroll-mt-20 border-t border-slate-200">
      <div className={WRAP + " grid grid-cols-1 gap-12 py-20 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-20 lg:py-28"}>
        <div className="min-w-0">
          <Eyebrow>{b.eyebrow}</Eyebrow>
          <SectionTitle id="built-title">{b.heading}</SectionTitle>
          <p className="mt-5 text-[1.0625rem] leading-relaxed text-slate-600">{b.intro}</p>
          <p className="mt-4 text-[0.9375rem] leading-relaxed text-slate-600">
            <Link href="/request-invite" className="tap font-medium text-brand-700 underline decoration-brand-100 underline-offset-4">
              {b.outro}
            </Link>
          </p>
        </div>
        <ol className="min-w-0 border-l border-slate-200" data-testid="changelog">
          {b.items.map((item) => (
            <li key={item.title} className="relative pb-9 pl-6 last:pb-0 sm:pl-8">
              <span aria-hidden className="absolute -left-[5px] top-1.5 h-[9px] w-[9px] rounded-full border-2 border-white bg-brand-600" />
              <p className="text-xs font-medium uppercase tracking-[0.1em] text-slate-500">{item.when}</p>
              <h3 className="mt-1 text-[1.0625rem] font-medium text-slate-900">{item.title}</h3>
              <p className="mt-1 text-[0.9375rem] leading-relaxed text-slate-600">{item.body}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/* ---------- How it works ---------- */

export function How() {
  const h = HOME.how;
  return (
    <section id={h.id} aria-labelledby="how-title" className="scroll-mt-20 border-t border-slate-200 bg-white">
      <div className={WRAP + " py-20 lg:py-28"}>
        <div className="max-w-[44rem]">
          <Eyebrow>{h.eyebrow}</Eyebrow>
          <SectionTitle id="how-title">{h.heading}</SectionTitle>
        </div>
        <ol className="mt-12 grid grid-cols-1 gap-x-8 gap-y-10 sm:grid-cols-2 lg:mt-16 lg:grid-cols-4">
          {h.steps.map((step, i) => (
            <li key={step.title} className="min-w-0 border-t border-slate-200 pt-5">
              <p aria-hidden className="font-display text-5xl leading-none text-brand-600">{i + 1}</p>
              <h3 className="mt-4 text-[1.0625rem] font-medium text-slate-900">{step.title}</h3>
              <p className="mt-2 text-[0.9375rem] leading-relaxed text-slate-600">{step.body}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/* ---------- Safe for your shop ---------- */

export function Safe() {
  const s = HOME.safe;
  return (
    <section id={s.id} aria-labelledby="safe-title" className="scroll-mt-20 border-t border-slate-200 bg-slate-900 text-stone-50">
      <div className={WRAP + " grid grid-cols-1 gap-12 py-20 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-20 lg:py-28"}>
        <div className="min-w-0">
          <h2 id="safe-title" className="font-display text-[clamp(2.1rem,4.4vw,3.4rem)] leading-[1.05]">{s.heading}</h2>
          <p className="mt-5 text-[1.0625rem] leading-relaxed text-stone-300">{s.intro}</p>
          <p className="mt-5 text-[0.9375rem] leading-relaxed text-stone-300">
            <span>{`${s.linksLead} `}</span>
            {s.links.map((link, i) => (
              <span key={link.href}>
                <span>{i > 0 ? " and the " : ""}</span>
                <Link href={link.href} className="tap font-medium text-white underline decoration-violet-300/60 underline-offset-4 hover:decoration-white">
                  {link.label}
                </Link>
              </span>
            ))}
            <span>.</span>
          </p>
        </div>
        <dl className="grid min-w-0 grid-cols-1 gap-x-10 gap-y-9 sm:grid-cols-2">
          {s.points.map((point) => (
            <div key={point.title} className="min-w-0 border-t border-white/15 pt-5">
              <dt className="flex items-start gap-2.5 text-[1.0625rem] font-medium text-white">
                <Check className="mt-1.5 text-violet-300" />
                <span>{point.title}</span>
              </dt>
              <dd className="mt-2 text-[0.9375rem] leading-relaxed text-stone-300">{point.body}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}

/* ---------- Questions ---------- */

export function Faq() {
  const f = HOME.faq;
  return (
    <section id={f.id} aria-labelledby="faq-title" className="scroll-mt-20 border-t border-slate-200 bg-white">
      <div className={WRAP + " grid grid-cols-1 gap-12 py-20 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-20 lg:py-28"}>
        <div className="min-w-0">
          <Eyebrow>{f.eyebrow}</Eyebrow>
          <SectionTitle id="faq-title">{f.heading}</SectionTitle>
          <p className="mt-5 text-[1.0625rem] leading-relaxed text-slate-600">
            <span>{`${f.contactLead} `}</span>
            <Link href={f.contact.href} className="tap font-medium text-brand-700 underline decoration-brand-100 underline-offset-4">
              {f.contact.label}
            </Link>
            <span>.</span>
          </p>
        </div>
        <div className="min-w-0 divide-y divide-slate-200 border-y border-slate-200">
          {f.items.map((item) => (
            <details key={item.q} className="group">
              <summary className="flex min-h-[2.75rem] cursor-pointer list-none items-center justify-between gap-6 py-5 text-[1.0625rem] font-medium text-slate-900 [&::-webkit-details-marker]:hidden">
                <span>{item.q}</span>
                <span aria-hidden className="text-xl leading-none text-slate-500 transition-transform duration-300 group-open:rotate-45">
                  +
                </span>
              </summary>
              <div className="pb-6 pr-10 text-[0.9375rem] leading-relaxed text-slate-600">
                <span>{item.a}</span>
                {item.link && (
                  <span key="link">
                    <span> </span>
                    <Link href={item.link.href} className="tap font-medium text-brand-700 underline decoration-brand-100 underline-offset-4">
                      {item.link.label}
                    </Link>
                    <span>.</span>
                  </span>
                )}
              </div>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ---------- Final call to action ---------- */

export function Closing() {
  const c = HOME.closing;
  return (
    <section aria-labelledby="closing-title" className="border-t border-slate-200">
      <div className={WRAP + " py-20 text-center lg:py-28"}>
        <h2 id="closing-title" className="mx-auto max-w-[40rem] font-display text-[clamp(2.3rem,5vw,3.8rem)] leading-[1.03] text-slate-900">
          <span>{`${c.heading} `}</span>
          <em className="text-brand-600">{c.headingAccent}</em>
        </h2>
        <p className="mx-auto mt-5 max-w-[34rem] text-[1.0625rem] leading-relaxed text-slate-600">{c.body}</p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Link href={c.primary.href} className="btn-primary px-5 py-3 text-[0.9375rem]" data-testid="closing-cta">{c.primary.label}</Link>
          <Link href={c.secondary.href} className="btn-secondary px-5 py-3 text-[0.9375rem]">{c.secondary.label}</Link>
        </div>
      </div>
    </section>
  );
}
