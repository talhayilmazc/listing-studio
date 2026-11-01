import { HOME } from "@/lib/homeCopy";
import { SCREENS, type Screen } from "@/lib/marketing/screens";
import { TESTIMONIALS } from "@/lib/marketing/testimonials";
import { DEMO_VIDEO } from "@/lib/marketing/video";
import { PLANNED_NOTICE, PLANS, listingsFact, priceFact, shopsFact } from "@/lib/plans";
import { Shot } from "./Page";

/**
 * The home page's sections. Static server markup: no script, no session.
 * Every word comes from lib/homeCopy.ts, where the page's rules are checked.
 */

export function Eyebrow({ children }: { children: React.ReactNode }) {
  return <p className="text-sm font-medium uppercase tracking-[0.12em] text-brand-700">{children}</p>;
}

export function SectionTitle({ children, id }: { children: React.ReactNode; id?: string }) {
  return (
    <h2 id={id} className="mt-3 font-display text-[clamp(2rem,4.2vw,3.1rem)] leading-[1.06] text-slate-900">
      {children}
    </h2>
  );
}

const WRAP = "mx-auto w-full max-w-[1240px] px-4 sm:px-6";
const TEXT_LINK = "tap font-medium text-brand-700 underline decoration-brand-100 underline-offset-4";

/* ---------- The demo video's place ---------- */

export function VideoSlot() {
  const v = HOME.video;
  if (DEMO_VIDEO) {
    return <video src={DEMO_VIDEO} controls preload="none" poster={SCREENS.review.src} title={v.title} className="h-auto w-full rounded-2xl border border-slate-200" data-testid="demo-video" />;
  }
  return (
    <div data-testid="demo-video-slot">
      <Shot screen={SCREENS.mockups} priority sizes="(min-width: 1024px) 620px, 100vw" />
      <p className="mt-3 text-sm leading-relaxed text-slate-600">{v.label}</p>
    </div>
  );
}

/* ---------- How it works ---------- */

const STEP_SCREENS: Screen[] = [SCREENS.upload, SCREENS.write, SCREENS.review, SCREENS.schedule];

export function How() {
  const h = HOME.how;
  return (
    <section id={h.id} aria-labelledby="how-title" className="scroll-mt-20 border-t border-slate-200 bg-white">
      <div className={WRAP + " py-16 lg:py-24"}>
        <div className="max-w-[44rem]">
          <Eyebrow>{h.eyebrow}</Eyebrow>
          <SectionTitle id="how-title">{h.heading}</SectionTitle>
          <p className="mt-4 text-[1.0625rem] leading-relaxed text-slate-700">{h.before}</p>
        </div>
        <ol className="mt-10 grid grid-cols-1 gap-x-8 gap-y-10 md:grid-cols-2 lg:mt-14">
          {h.steps.map((step, i) => (
            <li key={step.title} className="min-w-0" data-testid="how-step">
              <Shot screen={STEP_SCREENS[i]} sizes="(min-width: 768px) 46vw, 100vw" />
              <h3 className="mt-4 text-[1.1875rem] font-medium text-slate-900">
                <span aria-hidden className="mr-2 font-display text-2xl text-brand-600">{i + 1}</span>
                <span>{step.title}</span>
              </h3>
              <p className="mt-1.5 text-[0.9375rem] leading-relaxed text-slate-700">{step.body}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/* ---------- What sets it apart ---------- */

export function Apart() {
  const a = HOME.apart;
  return (
    <section id={a.id} aria-labelledby="apart-title" className="scroll-mt-20 border-t border-slate-200">
      <div className={WRAP + " py-16 lg:py-24"}>
        <div className="max-w-[44rem]">
          <Eyebrow>{a.eyebrow}</Eyebrow>
          <SectionTitle id="apart-title">{a.heading}</SectionTitle>
        </div>
        <ul className="mt-10 grid grid-cols-1 gap-6 md:grid-cols-3">
          {a.items.map((item) => (
            <li key={item.title} className="min-w-0 rounded-2xl border border-slate-200 bg-white p-6" data-testid="differentiator">
              <h3 className="text-[1.1875rem] font-medium text-slate-900">{item.title}</h3>
              <p className="mt-2 text-[0.9375rem] leading-relaxed text-slate-700">{item.body}</p>
              <p className="mt-3 text-[0.9375rem]"><a href={item.link.href} className={TEXT_LINK}>{item.link.label}</a></p>
            </li>
          ))}
        </ul>
        <p className="mt-8"><a href={a.more.href} className="btn-secondary px-5 py-3 text-[0.9375rem]">{a.more.label}</a></p>
      </div>
    </section>
  );
}

/* ---------- Who it is for ---------- */

export function Who() {
  const w = HOME.who;
  return (
    <section id={w.id} aria-labelledby="who-title" className="scroll-mt-20 border-t border-slate-200 bg-white">
      <div className={WRAP + " grid grid-cols-1 gap-10 py-16 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-20 lg:py-24"}>
        <div className="min-w-0">
          <Eyebrow>{w.eyebrow}</Eyebrow>
          <SectionTitle id="who-title">{w.heading}</SectionTitle>
        </div>
        <div className="min-w-0">
          <dl className="space-y-6">
            {w.items.map((item) => (
              <div key={item.title} className="border-t border-slate-200 pt-4">
                <dt className="text-[1.0625rem] font-medium text-slate-900">{item.title}</dt>
                <dd className="mt-1 text-[0.9375rem] leading-relaxed text-slate-700">{item.body}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-6 text-[0.9375rem] leading-relaxed text-slate-700">
            <span>{`${w.notFor} `}</span>
            <a href={w.compare.href} className={TEXT_LINK}>{w.compare.label}</a>
            <span>.</span>
          </p>
        </div>
      </div>
    </section>
  );
}

/* ---------- What sellers say: shown only when lib/marketing/testimonials.ts has any ---------- */

export function Testimonials() {
  if (TESTIMONIALS.length === 0) return null;
  return (
    <section className="border-t border-slate-200" data-testid="testimonials" aria-label="What sellers say">
      <ul className={WRAP + " grid grid-cols-1 gap-6 py-16 md:grid-cols-2"}>
        {TESTIMONIALS.map((t) => (
          <li key={t.name}>
            <blockquote className="text-[1.0625rem] leading-relaxed text-slate-800">{t.quote}</blockquote>
            <p className="mt-2 text-sm text-slate-600">{t.shop ? `${t.name}, ${t.shop}` : t.name}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ---------- Pricing: the figures are lib/plans.ts's ---------- */

export function PricingTeaser() {
  const p = HOME.pricing;
  return (
    <section id={p.id} aria-labelledby="pricing-title" className="scroll-mt-20 border-t border-slate-200">
      <div className={WRAP + " py-16 lg:py-24"}>
        <div className="max-w-[44rem]">
          <Eyebrow>{p.eyebrow}</Eyebrow>
          <SectionTitle id="pricing-title">{p.heading}</SectionTitle>
          <p className="mt-4 text-[1.0625rem] leading-relaxed text-slate-700" data-testid="home-planned-notice">{PLANNED_NOTICE}</p>
        </div>
        <ul className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4" data-testid="home-plans">
          {PLANS.map((plan) => (
            <li key={plan.name} className="min-w-0 rounded-2xl border border-slate-200 bg-white p-5">
              <p className="text-[1.0625rem] font-medium text-slate-900">{plan.name}</p>
              <p className="mt-1 font-display text-4xl leading-none text-slate-900" translate="no">{priceFact(plan)}</p>
              <p className="mt-3 text-sm leading-relaxed text-slate-700">
                <span className="block">{listingsFact(plan)}</span>
                <span className="block">{shopsFact(plan)}</span>
              </p>
            </li>
          ))}
        </ul>
        <p className="mt-6"><a href={p.link.href} className={TEXT_LINK}>{p.link.label}</a></p>
      </div>
    </section>
  );
}

/* ---------- Questions ---------- */

export function Faq() {
  const f = HOME.faq;
  return (
    <section id={f.id} aria-labelledby="faq-title" className="scroll-mt-20 border-t border-slate-200 bg-white">
      <div className={WRAP + " grid grid-cols-1 gap-10 py-16 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-20 lg:py-24"}>
        <div className="min-w-0">
          <Eyebrow>{f.eyebrow}</Eyebrow>
          <SectionTitle id="faq-title">{f.heading}</SectionTitle>
          <p className="mt-5 text-[1.0625rem] leading-relaxed text-slate-700">
            <span>{`${f.contactLead} `}</span>
            <a href={f.contact.href} className={TEXT_LINK}>{f.contact.label}</a>
            <span>.</span>
          </p>
        </div>
        <div className="min-w-0 divide-y divide-slate-200 border-y border-slate-200">
          {f.items.map((item) => (
            <details key={item.q} className="group">
              <summary className="flex min-h-[2.75rem] cursor-pointer list-none items-center justify-between gap-6 py-5 text-[1.0625rem] font-medium text-slate-900 [&::-webkit-details-marker]:hidden">
                <span>{item.q}</span>
                <span aria-hidden className="text-xl leading-none text-slate-600 transition-transform duration-300 group-open:rotate-45">+</span>
              </summary>
              <div className="pb-6 pr-10 text-[0.9375rem] leading-relaxed text-slate-700">
                <span>{item.a}</span>
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
      <div className={WRAP + " py-16 text-center lg:py-24"}>
        <h2 id="closing-title" className="mx-auto max-w-[40rem] font-display text-[clamp(2.2rem,4.8vw,3.6rem)] leading-[1.03] text-slate-900">
          <span>{`${c.heading} `}</span>
          <em className="text-brand-600">{c.headingAccent}</em>
        </h2>
        <p className="mx-auto mt-5 max-w-[34rem] text-[1.0625rem] leading-relaxed text-slate-700">{c.body}</p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <a href={c.primary.href} className="btn-primary px-5 py-3 text-[0.9375rem]" data-testid="closing-cta">{c.primary.label}</a>
          <a href={c.secondary.href} className="btn-secondary px-5 py-3 text-[0.9375rem]">{c.secondary.label}</a>
        </div>
      </div>
    </section>
  );
}
