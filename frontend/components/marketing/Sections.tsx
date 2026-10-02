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
    title: "Profiles built from your own listings",
    body: "Choose a listing you already sell as the model for a product. New drafts copy its category and attributes, variations and prices, shipping and processing, personalization question, size-chart images and shop section.",
  },
  {
    title: "Images handled",
    body: "Folders become listings by SKU. Images are resized, ordered and given a cover you can crop; a profile's size charts are added to every draft.",
  },
  {
    title: "Titles, tags and descriptions",
    body: "Written from what the design shows and who it is for: 13 distinct tags, a title in your shop's prefix and length, and your own description below. Choose your current style or one that follows Etsy's search guidance.",
  },
  {
    title: "Compliance checks before you see it",
    body: "Brand and character names are refused in the text, character artwork is flagged, file words are kept off physical items. Each finding says why.",
  },
  {
    title: "Several shops, one place",
    body: "Each shop has its own profiles. Send a listing to more than one of your shops, choosing listing by shop in one grid; every draft and figure names its shop.",
  },
  {
    title: "Scheduling in your time zone",
    body: "Approve a draft and give it a time. It goes live then, in your own time zone, with the requests it needs kept aside for it.",
  },
  {
    title: "Profit you can trace",
    body: "Sales totals from your shop, Etsy's fees and ad spend from your payment account, and your own costs. Every figure says where it came from, and estimates say they are estimates.",
  },
  {
    title: "Your Etsy request budget, visible",
    body: "Etsy limits how often any app may call it. You always see what today's work used and what is left, and long reads spread themselves over days.",
  },
];

export function Features() {
  return (
    <section id="features" aria-labelledby="features-title" className="scroll-mt-20 border-t border-slate-200">
      <div className="mx-auto w-full max-w-[1240px] px-4 py-20 sm:px-6 lg:py-28">
        <div className="max-w-[44rem]">
          <Eyebrow>Features</Eyebrow>
          <SectionTitle id="features-title">The listing form, done the way you already do it.</SectionTitle>
          <p className="mt-5 text-[1.0625rem] leading-relaxed text-slate-600">
            The written text is one part. Most of the time a listing takes goes into the parts that have to match your
            shop exactly, and those are copied from listings you set up yourself.
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
              <span className="font-medium text-slate-900">Choose the shop first.</span>{" "}
              <span>Each group of images gets one of that shop&rsquo;s profiles; the choice carries to the next groups.</span>
            </p>
          </div>
          <div className="min-w-0">
            <BrowserFrame {...SCREENS.analytics} sizes="(min-width: 1024px) 580px, 100vw" />
            <p className="mt-3 text-sm text-slate-600">
              <span className="font-medium text-slate-900">Profit per listing.</span>{" "}
              <span>Sales, Etsy fees and ad spend from your own shop, and your costs, each labelled with its source.</span>
            </p>
          </div>
        </div>

        <div className="mt-20 grid grid-cols-1 items-center gap-10 rounded-3xl bg-stone-50 p-6 sm:p-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,19rem)] lg:gap-16 lg:p-14">
          <div className="min-w-0">
            <h3 className="font-display text-3xl leading-tight text-slate-900 sm:text-4xl">Review from your phone.</h3>
            <p className="mt-4 max-w-[34rem] text-[1.0625rem] leading-relaxed text-slate-600">
              Every screen works at phone width: read a listing, fix a tag, reorder images with a tap, approve, and
              schedule it for tonight. No hover-only controls, nothing that needs dragging.
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
    title: "Drafts first, always",
    body: "Every listing is created in your shop as a draft. It goes live only when you press Publish now, or at a time you set for that one draft. There is no setting that publishes for you.",
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
    body: "Listing data from Etsy is kept for six hours at most and other Etsy data for a day, then fetched again when needed. Sales and fee totals are kept 13 months for season comparisons.",
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
              An app that can create listings in your shop has to earn that. These are the rules it works by, and the{" "}
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
    q: "Is Listyro made by Etsy?",
    a: "No. Listyro is an independent product that uses the Etsy API. It is not endorsed or certified by Etsy, Inc.",
  },
  {
    q: "Will anything go live without me?",
    a: "No. Listings are created as drafts. A draft goes live only when you publish it, or at a time you set for that specific draft.",
  },
  {
    q: "Who is it for?",
    a: "Print-on-demand and apparel sellers with large catalogs of their own designs, who add new designs often and want every listing to match the ones they already sell. There is also a profile type for digital products.",
  },
  {
    q: "What do I need before I start?",
    a: "An Etsy shop you own, with at least one listing set up the way you want new ones to be. That listing becomes the profile new drafts are built from.",
  },
  {
    q: "Will my listings rank higher?",
    a: "Nobody outside Etsy can promise that, and we do not. Listings are optimised for search matching: the words a buyer would type, spread across the title, tags, description and attributes the way Etsy's own guidance describes.",
  },
  {
    q: "Can I list designs with characters or brands?",
    a: "Brand and character names are refused in the text, and artwork showing recognisable characters is flagged before you publish. You are responsible for having the rights to what you sell.",
  },
  {
    q: "How many shops can I connect?",
    a: "Up to eight per account during the beta. Each shop is connected separately, by signing in to Etsy as its owner.",
  },
  {
    q: "What happens when Etsy refuses a draft?",
    a: "You see the step that failed and Etsy's own reason on the listing's card, and one click tries again. A retry carries on where it stopped; it never makes a second draft.",
  },
  {
    q: "What does it cost?",
    a: (
      <>
        Nothing during the beta. Paid plans will start only after it, and you will be told well before.{" "}
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
          <SectionTitle id="faq-title">Asked before signing up.</SectionTitle>
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
          Your next drop, <em className="text-brand-600">ready</em> for review.
        </h2>
        <p className="mx-auto mt-5 max-w-[34rem] text-[1.0625rem] leading-relaxed text-slate-600">
          The beta is free and small on purpose. Tell us about your shop and we will send you an invite.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Link href="/request-invite" className="btn-primary px-5 py-3 text-[0.9375rem]">Request an invite</Link>
          <Link href="/pricing" className="btn-secondary px-5 py-3 text-[0.9375rem]">Pricing</Link>
        </div>
      </div>
    </section>
  );
}
