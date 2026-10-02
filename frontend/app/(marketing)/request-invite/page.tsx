import type { Metadata } from "next";
import { InviteForm } from "@/components/marketing/InviteForm";

export const metadata: Metadata = {
  title: "Request an invite",
  description: "Listyro is in a small, invite-only beta. Tell us about your shop and we will send you an invite.",
  alternates: { canonical: "/request-invite" },
};

export default function RequestInvite() {
  return (
    <div className="mx-auto grid w-full max-w-[1240px] grid-cols-1 gap-12 px-4 pb-24 pt-12 sm:px-6 sm:pt-16 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-20">
      <div className="min-w-0">
        <p className="text-sm font-medium uppercase tracking-[0.12em] text-brand-700">Beta</p>
        <h1 className="mt-3 font-display text-[clamp(2.6rem,6vw,4.25rem)] leading-[1.02] text-slate-900">
          Request an invite.
        </h1>
        <p className="mt-5 max-w-[32rem] text-[1.0625rem] leading-relaxed text-slate-600">
          The beta is small on purpose, so each shop gets real attention. Tell us a little about yours, and you can
          leave whenever you like.
        </p>
        <ul className="mt-8 max-w-[32rem] space-y-3 text-[0.9375rem] text-slate-600">
          {[
            "Made for print-on-demand and apparel sellers who add new designs often",
            "You need an Etsy shop you own, with at least one listing set up the way you like",
            "We read every request ourselves and reply by email",
          ].map((t) => (
            <li key={t} className="flex gap-3">
              <span aria-hidden className="mt-[0.6rem] h-1 w-1 shrink-0 rounded-full bg-brand-600" />
              <span>{t}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="relative min-w-0">
        <InviteForm />
      </div>
    </div>
  );
}
