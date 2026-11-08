import type { Metadata } from "next";
import { InviteForm } from "@/components/marketing/InviteForm";
import { metadataFor } from "@/lib/marketing/pages";
import { JsonLd } from "@/components/marketing/Page";
import { breadcrumbs } from "@/lib/marketing/jsonld";
import { TRIAL } from "@/lib/plans";

export const metadata: Metadata = metadataFor("/request-invite");

export default function RequestInvite() {
  return (
    <>
    <JsonLd data={breadcrumbs("/request-invite")} />
    <div className="mk-wrap grid grid-cols-1 gap-12 pb-24 pt-10 sm:pt-14 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-20">
      <div className="min-w-0">
        <h1 className="mk-h1">
          Request an invite.
        </h1>
        <p className="mk-lead mt-5 max-w-[32rem]">
          Listyro is invite-only while we onboard sellers in small groups. Tell us a little about your shop and
          we&apos;ll email you when a spot opens. You can leave whenever you like.
        </p>
        <ul className="mk-body mt-8 max-w-[32rem] space-y-3">
          {[
            "Made for print-on-demand and apparel sellers who add new designs often",
            "You need an Etsy shop you own, with at least one listing set up the way you like",
            "We read every request ourselves and reply by email",
          ].map((t) => (
            <li key={t} className="flex gap-3">
              <span aria-hidden className="mt-[0.45rem] h-3 w-3 shrink-0 rounded-full bg-go" />
              <span>{t}</span>
            </li>
          ))}
        </ul>
        <p className="mt-8 max-w-[32rem] rounded-[24px] bg-[#FFF3D1] px-6 py-5 text-[1rem] font-bold leading-relaxed text-ink" data-testid="trial-line">{TRIAL.line}</p>
      </div>
      <div className="relative min-w-0">
        <InviteForm />
      </div>
    </div>
    </>
  );
}
