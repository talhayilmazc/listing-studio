import Link from "next/link";
import { BrowserFrame, PhoneFrame } from "./Frames";
import { SCREENS } from "./screens";

/** The landing page's sections after the walkthrough. Static server markup. */

export function Eyebrow({ children }: { children: React.ReactNode }) {
  return <p className="text-sm font-medium uppercase tracking-[0.12em] text-brand-700">{children}</p>;
}

export function SectionTitle({ children, id }: { children: React.ReactNode; id?: string }) {
  return (
    <h2 id={id} className="mt-3 font-display text-[clamp(2.2rem,4.6vw,3.6rem)] leading-[1.04] text-slate-900">
      {children}
    </h2>
  );
}

const FEATURES: { title: string; body: string }[] = [
  {
    title: "Titles, tags and descriptions",
    body: "Written for you from each design: what it shows and who it's for. All 13 tags, every time, worded the way shoppers search.",
  },
  {
    title: "Category, prices and variations",
    body: "Copied from a listing you already sell, so every new listing matches your shop without you typing a thing.",
  },
  {
    title: "Size charts and photos",
    body: "Your size chart is added to every listing. Mockups are resized and put in order, cover first.",
  },
  {
    title: "Shop sections",
    body: "Each listing is put into a matching section of your shop, so new designs land where they belong.",
  },
  {
    title: "All your shops",
    body: "Connect up to eight shops and send the same design to several at once, each with that shop's own settings.",
  },
  {
    title: "Scheduling",
    body: "Line up a whole drop and let it go live at the times you choose, in your own time zone.",
  },
  {
    title: "Trademark filter",
    body: "Optional trademark filter that keeps brand and character names out of your listings, and flags designs that may show one.",
  },
  {
    title: "Profit per listing",
    body: "See what each listing really earns after Etsy's fees, your ad spend and your own costs.",
  },
];

export function Features() {
  return (
    <section id="features" aria-labelledby="features-title" className="scroll-mt-20 border-t border-slate-200">
      <div className="mx-auto w-full max-w-[1240px] px-4 py-20 sm:px-6 lg:py-28">
        <div className="max-w-[44rem]">
          <Eyebrow>Features</Eyebrow>
          <SectionTitle id="features-title">Every part of the listing, filled in for you.</SectionTitle>
          <p className="mt-5 text-[1.0625rem] leading-relaxed text-slate-600">
            The slow part of a big catalog isn&rsquo;t the design. It&rsquo;s filling in the same listing form again and
            again. Listyro fills in every field from what you have already set up, so a new drop is a quick read-through
            instead of an evening of typing.
          </p>
        </div>

        <div className="mt-14 min-w-0">
          <div className="hidden sm:block">
            <BrowserFrame {...SCREENS.review} sizes="(min-width: 1240px) 1192px, 100vw" />
          </div>
          <div className="sm:hidden">
            <BrowserFrame {...SCREENS.reviewCard} sizes="100vw" />
          </div>
          <p className="mt-3 text-center text-xs text-slate-500">The review page, from a demo account with sample designs.</p>
        </div>

        <ul className="mt-16 grid grid-cols-1 gap-x-10 gap-y-10 sm:grid-cols-2 lg:grid-cols-4">
          {FEATURES.map((f) => (
            <li key={f.title} className="min-w-0 border-t border-slate-200 pt-5">
              <h3 className="text-[1.0625rem] font-medium text-slate-900">{f.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-slate-600">{f.body}</p>
            </li>
          ))}
        </ul>

        <div className="mt-20 grid grid-cols-1 gap-10 lg:grid-cols-2 lg:gap-12">
          <div className="min-w-0">
            <BrowserFrame {...SCREENS.batch} sizes="(min-width: 1024px) 580px, 100vw" />
            <p className="mt-3 text-sm text-slate-600">
              <span className="font-medium text-slate-900">Set it once for the whole drop.</span>{" "}
              <span>Pick the shop and the model listing, and every design in the folder follows. Change any one with a tap.</span>
            </p>
          </div>
          <div className="min-w-0">
            <BrowserFrame {...SCREENS.analytics} sizes="(min-width: 1024px) 580px, 100vw" />
            <p className="mt-3 text-sm text-slate-600">
              <span className="font-medium text-slate-900">Know what each listing earns.</span>{" "}
              <span>Sales, Etsy fees, ad spend and your own costs, side by side for every listing.</span>
            </p>
          </div>
        </div>

        <div className="mt-20 grid grid-cols-1 items-center gap-10 rounded-3xl bg-stone-50 p-6 sm:p-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,19rem)] lg:gap-16 lg:p-14">
          <div className="min-w-0">
            <h3 className="font-display text-3xl leading-tight text-slate-900 sm:text-4xl">Approve from anywhere.</h3>
            <p className="mt-4 max-w-[34rem] text-[1.0625rem] leading-relaxed text-slate-600">
              Everything works on your phone. Read a listing, fix a tag, swap the cover photo, then approve it or
              set it to go live tonight.
            </p>
          </div>
          <PhoneFrame {...SCREENS.phone} sizes="19rem" />
        </div>
      </div>
    </section>
  );
}

const DATA: { title: string; body: React.ReactNode }[] = [
  {
    title: "You stay in control",
    body: "Every listing waits for your one-click approval, or a time you pick, before it goes live. Nothing is ever published on its own.",
  },
  {
    title: "Only your own shops",
    body: "Listyro connects to shops you own, through Etsy's official API, with the permissions you grant on Etsy's own sign-in page. It never reads other sellers' listings and never scrapes Etsy's site.",
  },
  {
    title: "Your designs and the AI",
    body: (
      <>
        To write a listing, the design image is sent to Anthropic, the company behind Claude, to be described. Under
        Anthropic&rsquo;s commercial terms, data sent through its API is not used to train its models, and we do not
        train on your designs either. Uploading alone sends nothing.
      </>
    ),
  },
  {
    title: "No buyer data",
    body: "Sales are read as daily totals per listing. Buyers' names, addresses, emails and messages are never stored.",
  },
  {
    title: "Kept only as long as allowed",
    body: "Listing data from Etsy is kept for six hours at most and other Etsy data for a day, then fetched again when needed. Sales and fee totals are kept 13 months so you can compare seasons.",
  },
  {
    title: "Leave whenever you like",
    body: "Disconnect a shop and its Etsy access and data are deleted at once. Ask us to delete your account and everything in it goes.",
  },
];

export function YourData() {
  return (
    <section id="your-data" aria-labelledby="data-title" className="scroll-mt-20 border-t border-slate-200 bg-slate-900 text-stone-50">
      <div className="mx-auto w-full max-w-[1240px] px-4 py-20 sm:px-6 lg:py-28">
        <div className="grid grid-cols-1 gap-12 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-20">
          <div className="min-w-0">
            <p className="text-sm font-medium uppercase tracking-[0.12em] text-violet-300">Your data</p>
            <h2 id="data-title" className="mt-3 font-display text-[clamp(2.2rem,4.6vw,3.6rem)] leading-[1.04]">
              Your shop stays yours.
            </h2>
            <p className="mt-5 text-[1.0625rem] leading-relaxed text-stone-300">
              An app that adds listings to your shop has to earn your trust. Here is how Listyro treats your shop and your designs. The{" "}
              <Link href="/privacy" className="font-medium text-white underline decoration-violet-300/60 underline-offset-4 hover:decoration-white">
                Privacy Policy
              </Link>{" "}
              has the detail.
            </p>
          </div>
          <dl className="grid min-w-0 grid-cols-1 gap-x-10 gap-y-9 sm:grid-cols-2">
            {DATA.map((d) => (
              <div key={d.title} className="min-w-0 border-t border-white/15 pt-5">
                <dt className="text-[1.0625rem] font-medium text-white">{d.title}</dt>
                <dd className="mt-2 text-sm leading-relaxed text-stone-300">{d.body}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </section>
  );
}

export const QUESTIONS: { q: string; a: React.ReactNode }[] = [
  {
    q: "What does Listyro do?",
    a: "You drop in a folder of mockups. Listyro turns each design into a complete listing: title, 13 tags, description, category, prices, variations, size chart and shop section, in each shop you choose. You look them over and they're ready to go live.",
  },
  {
    q: "Will anything go live without me?",
    a: "No. Every listing waits for your one-click approval, or for a time you pick, before it goes live.",
  },
  {
    q: "Who is it for?",
    a: "Print-on-demand and apparel sellers with lots of their own designs, who add new ones often and want every listing to match the ones they already sell.",
  },
  {
    q: "What do I need to start?",
    a: "An Etsy shop you own, with at least one listing set up the way you like. Listyro uses it as the model for new ones.",
  },
  {
    q: "Is Listyro made by Etsy?",
    a: "No. Listyro is an independent product that uses the Etsy API. It is not endorsed or certified by Etsy, Inc.",
  },
  {
    q: "Will my listings rank higher?",
    a: "Nobody outside Etsy can promise that, and we don't. Listings are written to match what shoppers search for, following Etsy's own advice for titles, tags and descriptions.",
  },
  {
    q: "Can I list designs with characters or brands?",
    a: "There is an optional trademark filter, on unless you turn it off. It keeps brand and character names out of your listings and flags designs that may show one. Either way, you are responsible for having the rights to what you sell.",
  },
  {
    q: "How many shops can I connect?",
    a: "Up to eight. Each one is connected by signing in to Etsy as its owner.",
  },
  {
    q: "What if Etsy turns a listing down?",
    a: "You see Etsy's reason on the listing, fix it if needed, and try again with one click. A retry never creates a duplicate.",
  },
  {
    q: "Is it free?",
    a: (
      <>
        Yes, during the beta.{" "}
        <Link href="/pricing" className="font-medium text-brand-700 underline decoration-brand-100 underline-offset-4">
          See pricing
        </Link>
        .
      </>
    ),
  },
  {
    q: "How do I stop using it?",
    a: "Disconnect your shop in the app and its Etsy access and data are deleted at once. To delete your account, write to support from the address you signed up with.",
  },
];

export function Faq() {
  return (
    <section id="faq" aria-labelledby="faq-title" className="scroll-mt-20 border-t border-slate-200 bg-white">
      <div className="mx-auto grid w-full max-w-[1240px] grid-cols-1 gap-12 px-4 py-20 sm:px-6 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-20 lg:py-28">
        <div className="min-w-0">
          <Eyebrow>Questions</Eyebrow>
          <SectionTitle id="faq-title">Common questions.</SectionTitle>
          <p className="mt-5 text-[1.0625rem] leading-relaxed text-slate-600">
            <span>Something else? </span>
            <Link href="/contact" className="font-medium text-brand-700 underline decoration-brand-100 underline-offset-4">
              Get in touch
            </Link>
            <span>.</span>
          </p>
        </div>
        <div className="min-w-0 divide-y divide-slate-200 border-y border-slate-200">
          {QUESTIONS.map((item) => (
            <details key={item.q} className="group">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-6 py-5 text-[1.0625rem] font-medium text-slate-900 [&::-webkit-details-marker]:hidden">
                <span>{item.q}</span>
                <span aria-hidden className="text-xl leading-none text-slate-500 transition-transform duration-300 group-open:rotate-45">
                  +
                </span>
              </summary>
              <div className="pb-6 pr-10 text-[0.9375rem] leading-relaxed text-slate-600">{item.a}</div>
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}

export function Closing() {
  return (
    <section className="border-t border-slate-200">
      <div className="mx-auto w-full max-w-[1240px] px-4 py-20 text-center sm:px-6 lg:py-28">
        <h2 className="mx-auto max-w-[40rem] font-display text-[clamp(2.4rem,5vw,4rem)] leading-[1.02] text-slate-900">
          Spend your time on designs, <em className="text-brand-600">not listing forms.</em>
        </h2>
        <p className="mx-auto mt-5 max-w-[34rem] text-[1.0625rem] leading-relaxed text-slate-600">
          Listyro is in a small beta. Tell us about your shop and we&rsquo;ll send you an invite.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Link href="/request-invite" className="btn-primary px-5 py-3 text-[0.9375rem]">Request an invite</Link>
          <Link href="/pricing" className="btn-secondary px-5 py-3 text-[0.9375rem]">Pricing</Link>
        </div>
      </div>
    </section>
  );
}
