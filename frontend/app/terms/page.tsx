"use client";

import Link from "next/link";
import {
  Conspicuous,
  Fact,
  Items,
  LegalShell,
  Section,
  SupportEmail,
  TRADEMARK_NOTICE,
} from "@/components/LegalShell";

const SECTIONS = [
  { id: "about", title: "About these terms" },
  { id: "service", title: "What the Service does" },
  { id: "etsy", title: "Etsy and this Service" },
  { id: "accounts", title: "Accounts and eligibility" },
  { id: "beta", title: "A free beta" },
  { id: "responsibilities", title: "Your responsibilities" },
  { id: "content", title: "Your content" },
  { id: "use", title: "Acceptable use" },
  { id: "suspension", title: "Suspension and discontinuation" },
  { id: "warranty", title: "No warranty" },
  { id: "liability", title: "Limitation of liability" },
  { id: "changes", title: "Changes to these terms" },
  { id: "law", title: "Governing law and disputes" },
  { id: "contact", title: "Contact" },
];

export default function TermsPage() {
  return (
    <LegalShell title="Terms of Service" updated="23 September 2026" sections={SECTIONS}>
      <Section id="about" n={1} title="About these terms">
        <p>
          These terms govern your use of Listing Studio (&ldquo;the Service&rdquo;). The Service
          is operated by <Fact field="operator_name" />, <Fact field="operator_location" />{" "}
          (&ldquo;we&rdquo;, &ldquo;us&rdquo;). You can reach us at <SupportEmail />.
        </p>
        <p>
          By creating an account you agree to these terms and to our{" "}
          <Link href="/privacy" className="font-medium text-brand-700 hover:underline">
            Privacy Policy
          </Link>
          . If you do not agree, please do not use the Service.
        </p>
      </Section>

      <Section id="service" n={2} title="What the Service does">
        <p>
          Listing Studio helps Etsy sellers turn their own original designs into draft listings.
          In short:
        </p>
        <Items>
          <li>
            You upload a folder of your design images. We resize and order them, and read a SKU
            from the folder or file name.
          </li>
          <li>
            We use an AI model to analyse each design and suggest a title, thirteen tags and a
            description.
          </li>
          <li>
            You choose a <em>profile</em>: one of your own existing Etsy listings, from which we
            copy the category, attributes, price, variations, shipping and production settings,
            so new listings match the ones you already sell.
          </li>
          <li>
            When you approve a listing, we create it in your Etsy shop <strong>as a draft</strong>
            , through Etsy&rsquo;s official API.
          </li>
        </Items>
        <p>
          <strong>Nothing is ever published automatically.</strong> A draft becomes an active
          listing only when you press <em>Publish now</em>, and only for listings you have
          individually approved. We do not create, manage or pay for Etsy Ads, and we do not
          handle any part of checkout or payment.
        </p>
      </Section>

      <Section id="etsy" n={3} title="Etsy and this Service">
        <p className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
          {TRADEMARK_NOTICE}
        </p>
        <p>
          Listing Studio is an independent product. Etsy, Inc. is not a party to these terms, has
          not reviewed or approved the Service, and gives you no promise or warranty about it.
          Questions about the Service come to us, not to Etsy.
        </p>
        <p>
          We reach Etsy only through the official Etsy Open API v3, using the permissions you
          grant when you connect your shop. We never access Etsy by scraping or automating its
          website, we only ever work with your own shop, and we never collect or analyse other
          sellers&rsquo; listings, tags, images or prices. Your use of Etsy itself remains governed
          by Etsy&rsquo;s own{" "}
          <a
            href="https://www.etsy.com/legal/terms-of-use"
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-brand-700 hover:underline"
          >
            Terms of Use
          </a>{" "}
          and{" "}
          <a
            href="https://www.etsy.com/legal/sellers"
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-brand-700 hover:underline"
          >
            Seller Policy
          </a>
          ; our access to Etsy is governed by Etsy&rsquo;s{" "}
          <a
            href="https://www.etsy.com/legal/api"
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-brand-700 hover:underline"
          >
            API Terms of Use
          </a>
          .
        </p>
      </Section>

      <Section id="accounts" n={4} title="Accounts and eligibility">
        <Items>
          <li>
            During the beta, accounts are by invitation only. An invite code is personal and can
            be used once.
          </li>
          <li>
            You must be at least 18 years old, and you may only connect an Etsy shop that you own
            or are authorised to manage. Each account can connect one shop.
          </li>
          <li>
            Keep your password to yourself. You are responsible for what happens under your
            account; if you think someone else has access to it, tell us straight away.
          </li>
        </Items>
      </Section>

      <Section id="beta" n={5} title="A free beta">
        <p>
          The Service is in beta and is <strong>free of charge</strong>. We will not charge you
          for it without telling you first and without your separate agreement.
        </p>
        <p>
          Being a beta means the Service may have errors, may be unavailable at times, and may
          change as we improve it. Data loss is possible.{" "}
          <strong>Please keep your own copies of your design files</strong>; do not rely on the
          Service as the only place they are stored.
        </p>
      </Section>

      <Section id="responsibilities" n={6} title="Your responsibilities">
        <p>
          <strong>You remain responsible for your Etsy account and shop.</strong> That includes
          following Etsy&rsquo;s terms and policies and the law that applies to your business,
          and everything that appears in listings you publish.
        </p>
        <p>
          <strong>Review every listing before you publish it.</strong> AI-generated titles, tags
          and descriptions can be inaccurate or unsuitable, and can include words that belong to
          someone else&rsquo;s trademark or that Etsy&rsquo;s policies do not allow. Settings
          copied from a reference listing may not fit every new product. Our automated checks
          catch some problems, but they are an aid, not a guarantee. Check the title, tags,
          description, category, price, variations and images of each draft; publishing is your
          decision.
        </p>
        <p>
          <strong>Only upload what you have the right to use.</strong> You confirm that you own,
          or are licensed to use, every design you upload, and that selling it does not infringe
          anyone else&rsquo;s rights.
        </p>
        <p>
          Etsy limits how many requests an application may make each day, and that allowance is
          shared by everyone using the Service. When it runs low, some work may be delayed. The
          remaining allowance is always shown in the app.
        </p>
      </Section>

      <Section id="content" n={7} title="Your content">
        <p>
          Your designs, and the listing text generated for you, remain yours. You give us a
          limited, non-exclusive permission to store, process and transmit them only to provide
          the Service to you — including sending design images to our AI provider for analysis,
          as described in the{" "}
          <Link href="/privacy" className="font-medium text-brand-700 hover:underline">
            Privacy Policy
          </Link>
          . That permission ends when your content is deleted.
        </p>
        <p>
          We do not sell your designs, show them to other users, use them for marketing, or use
          them to train AI models.
        </p>
      </Section>

      <Section id="use" n={8} title="Acceptable use">
        <p>You agree not to:</p>
        <Items>
          <li>try to access another user&rsquo;s account or data;</li>
          <li>
            probe, bypass or overload the Service&rsquo;s security, rate limits or usage limits;
          </li>
          <li>
            use the Service to collect data about other sellers, or to direct buyers away from
            Etsy;
          </li>
          <li>
            resell, rent or give others access to the Service or to the Etsy access it holds on
            your behalf;
          </li>
          <li>upload anything unlawful, infringing, or harmful, such as malware.</li>
        </Items>
      </Section>

      <Section id="suspension" n={9} title="Suspension and discontinuation">
        <p>
          We may suspend or close your account if you break these terms, if Etsy or the law
          requires it, or where needed to protect the Service or other users.
        </p>
        <p>
          We may change, pause or stop the Service, in whole or in part, at any time — for example
          if Etsy changes or withdraws our access to its API, or if the beta ends. Where we
          reasonably can, we will give you notice first so you can make any drafts you need.
        </p>
        <p>
          You can stop using the Service at any time and ask us to delete your account (see the
          Privacy Policy). Drafts and listings already created in your Etsy shop stay in your
          shop; closing your account here does not remove them.
        </p>
      </Section>

      <Section id="warranty" n={10} title="No warranty">
        <Conspicuous>
          The Service is provided &ldquo;as is&rdquo; and &ldquo;as available&rdquo;, without
          warranties of any kind, whether express or implied, including any implied warranties
          of merchantability, fitness for a particular purpose, title, and non-infringement. We
          do not warrant that the Service will be uninterrupted, secure or error-free, that
          generated content will be accurate, complete or compliant with Etsy&rsquo;s policies,
          or that any listing will be accepted by Etsy or will sell. Etsy, Inc. makes no
          warranty of any kind regarding the Service.
        </Conspicuous>
        <p>
          Some jurisdictions do not allow certain warranties to be excluded; in that case the
          exclusions above apply only to the extent the law permits.
        </p>
      </Section>

      <Section id="liability" n={11} title="Limitation of liability">
        <Conspicuous>
          To the fullest extent permitted by law, we will not be liable for any indirect,
          incidental, special, consequential or punitive damages, or for any loss of profits,
          sales, revenue, data or goodwill, or for any action Etsy takes regarding your account
          or listings, arising out of or related to the Service. Because the Service is provided
          free of charge, our total liability to you for all claims relating to the Service is
          limited to fifty (50) euros.
        </Conspicuous>
        <p>
          Nothing in these terms limits liability that cannot be limited by law, such as
          liability for fraud, for wilful misconduct or gross negligence, or for death or personal
          injury caused by negligence, and nothing affects rights you have as a consumer that
          cannot be waived.
        </p>
      </Section>

      <Section id="changes" n={12} title="Changes to these terms">
        <p>
          We may update these terms as the Service develops. If a change is significant, we will
          tell you in the app or by email at least 14 days before it takes effect. If you keep
          using the Service after that, the new terms apply; if you do not agree, you can stop
          using it and ask us to delete your account.
        </p>
      </Section>

      <Section id="law" n={13} title="Governing law and disputes">
        <p>
          These terms are governed by the laws of <Fact field="governing_law" />, without regard
          to its conflict-of-law rules. Any dispute will be brought before{" "}
          <Fact field="dispute_venue" />, unless the law of the country where you live gives you
          the right to bring it before your own courts, in which case that right is unaffected.
        </p>
        <p>
          Before starting any formal dispute, please write to us at <SupportEmail /> so we can
          try to resolve it directly.
        </p>
      </Section>

      <Section id="contact" n={14} title="Contact">
        <p>
          Questions about these terms: <SupportEmail />. The Service is operated by{" "}
          <Fact field="operator_name" />, <Fact field="operator_location" />.
        </p>
      </Section>
    </LegalShell>
  );
}
