import type { Metadata } from "next";
import { SupportAddress } from "@/components/marketing/SupportAddress";
import { metadataFor } from "@/lib/marketing/pages";
import { JsonLd } from "@/components/marketing/Page";
import { breadcrumbs } from "@/lib/marketing/jsonld";

export const metadata: Metadata = metadataFor("/contact");

const WAYS: { title: string; body: React.ReactNode }[] = [
  {
    title: "Support",
    body: (
      <>
        <span>A question, a listing that did not come out right, or something that looks wrong. Include the batch name or SKU if it is about a listing; never send your password.</span>
        <SupportAddress className="mt-2" />
      </>
    ),
  },
  {
    title: "Joining early access",
    body: (
      <>
        <span>Use the </span>
        <a href="/request-invite" className="mk-link">
          invite request form
        </a>
        <span>. Each request is read by a person; an invite code comes by email.</span>
      </>
    ),
  },
  {
    title: "Your data",
    body: (
      <>
        <span>To have your account deleted, or to ask what we hold about you, write from the address you signed up with. The </span>
        <a href="/privacy" className="mk-link">
          Privacy Policy
        </a>
        <span> says what we keep and for how long.</span>
      </>
    ),
  },
];

export default function Contact() {
  return (
    <>
    <JsonLd data={breadcrumbs("/contact")} />
    <div className="mk-wrap pb-24 pt-10 sm:pt-14">
      <div className="max-w-[46rem]">
        <h1 className="mk-h1">
          Talk to a person.
        </h1>
        <p className="mk-lead mt-5">
          Listyro is run by a small team. Email is the fastest way to reach us, and we answer it ourselves.
        </p>
      </div>
      <dl className="mt-14 grid grid-cols-1 gap-x-12 gap-y-10 md:grid-cols-3">
        {WAYS.map((w) => (
          <div key={w.title} className="min-w-0 border-t-2 border-ink pt-5">
            <dt className="mk-h3 !text-[1.375rem]">{w.title}</dt>
            <dd className="mk-body mt-3 break-words">{w.body}</dd>
          </div>
        ))}
      </dl>
    </div>
    </>
  );
}
