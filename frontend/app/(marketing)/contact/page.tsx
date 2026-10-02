import type { Metadata } from "next";
import Link from "next/link";
import { SupportAddress } from "@/components/marketing/SupportAddress";

export const metadata: Metadata = {
  title: "Contact",
  description: "How to reach the people behind Listyro: support, privacy requests and invites.",
  alternates: { canonical: "/contact" },
};

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
    title: "Joining the beta",
    body: (
      <>
        <span>Use the </span>
        <Link href="/request-invite" className="font-medium text-brand-700 underline decoration-brand-100 underline-offset-4">
          invite request form
        </Link>
        <span>. Each request is read by a person; an invite code comes by email.</span>
      </>
    ),
  },
  {
    title: "Your data",
    body: (
      <>
        <span>To have your account deleted, or to ask what we hold about you, write from the address you signed up with. The </span>
        <Link href="/privacy" className="font-medium text-brand-700 underline decoration-brand-100 underline-offset-4">
          Privacy Policy
        </Link>
        <span> says what we keep and for how long.</span>
      </>
    ),
  },
];

export default function Contact() {
  return (
    <div className="mx-auto w-full max-w-[1240px] px-4 pb-24 pt-12 sm:px-6 sm:pt-16">
      <div className="max-w-[46rem]">
        <p className="text-sm font-medium uppercase tracking-[0.12em] text-brand-700">Contact</p>
        <h1 className="mt-3 font-display text-[clamp(2.6rem,6vw,4.25rem)] leading-[1.02] text-slate-900">
          Talk to a person.
        </h1>
        <p className="mt-5 text-[1.0625rem] leading-relaxed text-slate-600">
          Listyro is run by a small team. Email is the fastest way to reach us, and we answer it ourselves.
        </p>
      </div>
      <dl className="mt-14 grid grid-cols-1 gap-x-12 gap-y-10 md:grid-cols-3">
        {WAYS.map((w) => (
          <div key={w.title} className="min-w-0 border-t border-slate-200 pt-5">
            <dt className="font-display text-2xl text-slate-900">{w.title}</dt>
            <dd className="mt-3 break-words text-[0.9375rem] leading-relaxed text-slate-600">{w.body}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
