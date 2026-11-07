import { HOME } from "@/lib/homeCopy";
import { SCREENS, type Screen } from "@/lib/marketing/screens";
import { TESTIMONIALS } from "@/lib/marketing/testimonials";
import { PLANNED_NOTICE, PLANS, TRIAL, listingsFact, priceFact, shopsFact } from "@/lib/plans";
import { MOCKUPS } from "./HeroDemo";
import { Shot } from "./Page";

/**
 * The home page's sections. Static server markup: no script, no session.
 * Every word comes from lib/homeCopy.ts, where the page's rules are checked; prices from lib/plans.ts.
 * Shapes by role (docs/design-v2.md): numbered rule rows, real crops at their natural size, a fabric panel,
 * plan tags, an ink band. Nothing fades in.
 */

const SECTION = "scroll-mt-20 border-t border-heather";

/* ---------- How it works: four steps, each with a crop of the real app ---------- */

const STEP_SCREENS: Screen[] = [SCREENS.upload, SCREENS.write, SCREENS.review, SCREENS.send];

export function How() {
  const h = HOME.how;
  return (
    <section id={h.id} aria-labelledby="how-title" className={SECTION}>
      <div className="mk-wrap py-16 lg:py-24">
        <h2 id="how-title" className="mk-h2">{h.heading}</h2>
        <p className="mk-lead mt-4">{h.before}</p>
        <ol className="mt-10 grid grid-cols-1 gap-x-10 gap-y-12 md:grid-cols-2 lg:mt-12">
          {h.steps.map((step, i) => (
            <li key={step.title} className="min-w-0" data-testid="how-step">
              <h3 className="flex items-baseline gap-3">
                <span aria-hidden className="inline-flex h-8 w-8 shrink-0 items-center justify-center bg-ink text-base font-extrabold text-white [font-stretch:125%]">{i + 1}</span>
                <span className="mk-h3">{step.title}</span>
              </h3>
              <p className="mk-body mt-2 max-w-[44ch]">{step.body}</p>
              <Shot screen={STEP_SCREENS[i]} className="mt-5" />
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/* ---------- Why sellers switch: each shown, not described ---------- */


function MockupsShown() {
  const a = HOME.why.items[0];
  const colours = HOME.hero.demo.colours;
  return (
    <div className="bg-fabric p-4 sm:p-6" data-testid="why-mockups">
      <div className="grid grid-cols-2 gap-3 sm:gap-4">
        <figure className="min-w-0">
          <img src="/hero/blank-white-large.webp" alt={a.before} width={180} height={210} loading="lazy" className="block h-auto w-full border border-heather" />
          <figcaption className="mt-1.5 text-[0.8125rem] font-semibold text-ink">{a.before}</figcaption>
        </figure>
        <figure className="min-w-0">
          <img src="/hero/mockup-white-large.webp" alt={a.after} width={180} height={210} loading="lazy" className="block h-auto w-full border border-heather" />
          <figcaption className="mt-1.5 text-[0.8125rem] font-semibold text-ink">{a.after}</figcaption>
        </figure>
      </div>
      <ul className="mt-3 grid grid-cols-5 gap-1.5 sm:gap-2">
        {MOCKUPS.map((src, i) => (
          <li key={src} className="min-w-0">
            <img src={src} alt={colours[i]} width={96} height={112} loading="lazy" className="block h-auto w-full border border-heather" />
          </li>
        ))}
      </ul>
    </div>
  );
}

function WriterShown() {
  const b = HOME.why.items[1];
  const d = HOME.hero.demo;
  return (
    <div className="bg-fabric p-4 sm:p-6" data-testid="why-writer">
      <figure className="flex items-center gap-3">
        <div className="checkerboard w-24 shrink-0 border border-heather p-1.5 sm:w-28">
          <img src="/hero/design.webp" alt={d.fileAlt} width={120} height={101} loading="lazy" className="block h-auto w-full" />
        </div>
        <figcaption className="text-[0.8125rem] font-semibold text-ink">{b.design}</figcaption>
      </figure>
      <p className="mb-1.5 mt-4 text-[0.8125rem] font-semibold text-ink">{b.text}</p>
      <Shot screen={SCREENS.writer} />
    </div>
  );
}

export function Why() {
  const w = HOME.why;
  const shown = [<MockupsShown key="mockups" />, <WriterShown key="writer" />, <Shot key="shops" screen={SCREENS.shops} />];
  return (
    <section id={w.id} aria-labelledby="why-title" className={SECTION}>
      <div className="mk-wrap py-16 lg:py-24">
        <h2 id="why-title" className="mk-h2">{w.heading}</h2>
        <ul className="mt-10 space-y-14 lg:mt-12 lg:space-y-16">
          {w.items.map((item, i) => (
            <li key={item.title} className="grid min-w-0 grid-cols-1 items-start gap-6 border-t border-heather pt-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] lg:gap-12" data-testid="differentiator">
              <div className="min-w-0">
                <h3 className="mk-h3 !text-[1.375rem]">{item.title}</h3>
                <p className="mk-body mt-3 max-w-[46ch]">{item.body}</p>
                <p className="mt-4"><a href={item.link.href} className="mk-link">{item.link.label}</a></p>
              </div>
              <div className="min-w-0 max-w-[32.5rem]">{shown[i]}</div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

/* ---------- What sellers say: shown only when lib/marketing/testimonials.ts has any ---------- */

export function Testimonials() {
  if (TESTIMONIALS.length === 0) return null;
  return (
    <section className={SECTION} data-testid="testimonials" aria-label="What sellers say">
      <ul className="mk-wrap grid grid-cols-1 gap-8 py-16 md:grid-cols-2">
        {TESTIMONIALS.map((t) => (
          <li key={t.name} className="border-l-4 border-tag pl-5">
            <blockquote className="mk-lead text-ink">{t.quote}</blockquote>
            <p className="mk-small mt-2">{t.shop ? `${t.name}, ${t.shop}` : t.name}</p>
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
    <section id={p.id} aria-labelledby="pricing-title" className={SECTION}>
      <div className="mk-wrap py-16 lg:py-24">
        <div className="bg-fabric p-5 sm:p-8 lg:p-10">
          <h2 id="pricing-title" className="mk-h2">{p.heading}</h2>
          <p className="mk-body mt-3 max-w-[60ch]" data-testid="home-planned-notice">{PLANNED_NOTICE}</p>
          <ul className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="home-plans">
            {PLANS.map((plan) => (
              <li key={plan.name} className={"mk-tag min-w-0 px-5 pb-5 pt-9 " + (plan.highlight ? "bg-tag" : "bg-white")} data-testid={plan.highlight ? "home-plan-highlight" : undefined}>
                <p className="text-[1.0625rem] font-bold text-ink">{plan.name}</p>
                <p className="mt-2 flex items-baseline gap-1 text-ink">
                  <span className="text-[2.25rem] font-extrabold leading-none [font-stretch:125%]" translate="no">{priceFact(plan)}</span>
                  <span className="text-[0.9375rem] font-medium">/mo</span>
                </p>
                <p className="mt-3 text-[0.9375rem] leading-relaxed text-ink">
                  <span className="block">{listingsFact(plan)}</span>
                  <span className="block">{shopsFact(plan)}</span>
                </p>
              </li>
            ))}
          </ul>
          <p className="mk-small mt-6 flex flex-wrap items-center gap-x-6 gap-y-3">
            <span>{TRIAL.line}</span>
            <a href={p.link.href} className="mk-link">{p.link.label}</a>
          </p>
        </div>
      </div>
    </section>
  );
}

/* ---------- Questions ---------- */

export function Faq() {
  const f = HOME.faq;
  return (
    <section id={f.id} aria-labelledby="faq-title" className={SECTION}>
      <div className="mk-wrap grid grid-cols-1 gap-8 py-16 lg:grid-cols-[minmax(0,4fr)_minmax(0,8fr)] lg:gap-16 lg:py-24">
        <div className="min-w-0">
          <h2 id="faq-title" className="mk-h2">{f.heading}</h2>
          <p className="mk-body mt-4">
            <span>{`${f.contactLead} `}</span>
            <a href={f.contact.href} className="mk-link">{f.contact.label}</a>
            <span>.</span>
          </p>
        </div>
        <div className="min-w-0 divide-y divide-heather border-y border-heather">
          {f.items.map((item) => (
            <details key={item.q} className="group">
              <summary className="flex min-h-[2.75rem] cursor-pointer list-none items-center justify-between gap-6 py-5 text-[1.125rem] font-bold text-ink [&::-webkit-details-marker]:hidden">
                <span>{item.q}</span>
                <span aria-hidden className="text-2xl font-normal leading-none text-ink transition-transform group-open:rotate-45">+</span>
              </summary>
              <p className="mk-body max-w-[62ch] pb-6 pr-10">{item.a}</p>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ---------- Final call to action: the one tag-yellow button, on an ink band ---------- */

export function Closing() {
  const c = HOME.closing;
  return (
    <section aria-labelledby="closing-title" className="bg-ink">
      <div className="mk-wrap py-16 lg:py-20">
        <h2 id="closing-title" className="mk-h2 max-w-[24ch] !text-white">{c.heading}</h2>
        <p className="mk-body mt-4 max-w-[56ch] !text-[#D9DCE1]">{c.body}</p>
        <div className="mt-8 flex flex-wrap items-center gap-x-6 gap-y-4">
          <a href={c.primary.href} className="mk-btn !bg-tag !text-ink hover:!bg-white" data-testid="closing-cta">{c.primary.label}</a>
          <a href={c.secondary.href} className="font-semibold text-white underline decoration-tag decoration-[3px] underline-offset-[5px] hover:decoration-white">{c.secondary.label}</a>
        </div>
      </div>
    </section>
  );
}
