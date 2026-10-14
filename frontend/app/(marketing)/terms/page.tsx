import Link from "next/link";
import {
  Conspicuous,
  Fact,
  Items,
  LegalShell,
  Section,
  SupportEmail,
} from "@/components/LegalShell";
import { LAST_UPDATED, PROVIDER_DISCLAIMER, RETENTION, TRADEMARK_NOTICE } from "@/lib/legal";

export const metadata = {
  title: "Terms of Service",
  description: "The terms for using Listyro.",
  alternates: { canonical: "/terms" },
};

// Every statement here describes what the code does (docs/etsy-compliance.md names
// the file behind each). Changing one in a way sellers must agree to means a new
// TERMS_VERSION (lib/legal.ts and backend/app/core/terms.py): everyone is asked again.
const SECTIONS = [
  { id: "provider", title: "Who provides the Service" },
  { id: "service", title: "What the Service does" },
  { id: "etsy", title: "Etsy and this Service" },
  { id: "accounts", title: "Accounts and eligibility" },
  { id: "fees", title: "Fees: none during early access" },
  { id: "responsibilities", title: "Your responsibilities" },
  { id: "publishing", title: "Drafts and publishing" },
  { id: "content", title: "Your content" },
  { id: "use", title: "Acceptable use" },
  { id: "suspension", title: "Suspending or withdrawing the Service" },
  { id: "etsy-access", title: "If Etsy ends our access" },
  { id: "deletion", title: "Deleting your account" },
  { id: "warranty", title: "No warranty" },
  { id: "liability", title: "Limitation of liability" },
  { id: "changes", title: "Changes to these terms" },
  { id: "law", title: "Governing law and disputes" },
  { id: "contact", title: "Contact" },
];

const link = "font-medium text-brand-700 hover:underline";

export default function TermsPage() {
  return (
    <LegalShell title="Terms of Service" updated={LAST_UPDATED} sections={SECTIONS}>
      <Section id="provider" n={1} title="Who provides the Service">
        <Conspicuous>{PROVIDER_DISCLAIMER}</Conspicuous>
        <p>
          These terms govern your use of Listyro (&ldquo;the Service&rdquo;). The Service is provided
          by <Fact field="operator_name" />, <Fact field="operator_location" /> (&ldquo;we&rdquo;,
          &ldquo;us&rdquo;), and by nobody else. Etsy, Inc. does not provide it, is not a party to these
          terms, and gives you no promise or warranty about it. You can reach us at <SupportEmail />.
        </p>
        <p>
          You agree to these terms and to our{" "}
          <Link href="/privacy" className={link}>
            Privacy Policy
          </Link>{" "}
          by ticking the box when you create your account, or by pressing the button on the screen that
          asks you to when they change. Without that, an account cannot be created or used.
        </p>
      </Section>

      <Section id="service" n={2} title="What the Service does">
        <p>
          Listyro helps Etsy sellers turn their own original designs into draft listings in their own
          shops, and shows them what those listings earn. In short:
        </p>
        <Items>
          <li>
            You upload your design images. We resize and order them, read a SKU from the folder or file
            name, and group them into listings the way you choose.
          </li>
          <li>
            You choose a <em>profile</em>: one of your own existing Etsy listings (the reference
            listing), from which the category, attributes, price, variations, shipping and return
            settings, production partners and description are copied, so a new listing matches the ones
            you already sell.
          </li>
          <li>
            AI suggests a title, 13 tags and a description from your own images and the reference
            listing you chose. You review and edit every listing; nothing is generated or published
            without you.
          </li>
          <li>
            When you approve a listing and ask for it, we create it in your Etsy shop{" "}
            <strong>as a draft</strong>, through Etsy&rsquo;s official API.
          </li>
          <li>
            If you choose, mockups: you save photos of a blank garment you sell (in several colours if you
            like), and your designs are placed on them as if printed on the fabric, by image processing
            on our own server. Your design is fitted, warped and shaded, never redrawn, and no AI is used
            for it. You can use the mockups as your listings&rsquo; photos, download them, or both.
          </li>
          <li>
            If you run several shops, you manage each from one account: each shop has its own listings,
            its own shipping and return settings, and its own drafts.
          </li>
          <li>
            Profit, cost and ads reporting for your own shop, worked out from your shop&rsquo;s sales
            and fees and from the statements you upload.
          </li>
        </Items>
        <p>
          We do not create, manage or pay for Etsy Ads, and we do not handle any part of checkout or
          payment between you and your buyers.
        </p>
      </Section>

      <Section id="etsy" n={3} title="Etsy and this Service">
        <p className="rounded-lg border border-slate-200 bg-white p-4 text-sm" data-testid="terms-trademark-notice">
          {TRADEMARK_NOTICE}
        </p>
        <p>
          Listyro is an independent product. It has not been reviewed, approved or certified by Etsy,
          and it is not a partner of Etsy. Questions about the Service come to us, not to Etsy.
        </p>
        <p>
          We reach Etsy only through the official Etsy Open API v3, using the permissions you grant
          when you connect a shop. We never access Etsy by scraping or automating its website, we work
          only with your own shops, and we never collect or analyse other sellers&rsquo; listings,
          tags, images or prices. Your use of Etsy itself remains governed by Etsy&rsquo;s own{" "}
          <a href="https://www.etsy.com/legal/terms-of-use" target="_blank" rel="noopener noreferrer" className={link}>
            Terms of Use
          </a>{" "}
          and{" "}
          <a href="https://www.etsy.com/legal/sellers" target="_blank" rel="noopener noreferrer" className={link}>
            Seller Policy
          </a>
          ; our access to Etsy is governed by Etsy&rsquo;s{" "}
          <a href="https://www.etsy.com/legal/api" target="_blank" rel="noopener noreferrer" className={link}>
            API Terms of Use
          </a>
          .
        </p>
      </Section>

      <Section id="accounts" n={4} title="Accounts and eligibility">
        <Items>
          <li>
            During early access, accounts are by invitation only. An invite is personal and can be used
            once; an invite issued in answer to a request works only for the email address that asked.
          </li>
          <li>
            You must be at least 18 years old, and you may only connect Etsy shops that you own or are
            authorised to manage. Each shop is connected separately, by signing in to Etsy as that
            shop&rsquo;s owner, and a shop can be connected to one Listyro account at a time. The Shops
            page shows how many shops your account may connect.
          </li>
          <li>
            Keep your password to yourself. You are responsible for what happens under your account; if
            you think someone else has access to it, tell us straight away.
          </li>
        </Items>
      </Section>

      <Section id="fees" n={5} title="Fees: none during early access">
        <p>
          The Service is in invite-only early access and is <strong>free of charge</strong>. We take no payment
          of any kind during early access, and there is no checkout or payment form in the Service. If we
          later introduce paid plans, we will tell you the prices in advance, and you will not be
          charged without your separate, explicit agreement. The plans we intend to offer after launch
          are shown on the{" "}
          <Link href="/pricing" className={link}>
            pricing page
          </Link>
          <span>; they are plans, not an offer, and may change before launch.</span>
        </p>
        <p>
          Early access means the Service may have errors, may be unavailable at times, and may change
          as we improve it. Data loss is possible.{" "}
          <strong>Please keep your own copies of your design files</strong><span>: uploaded image files are
          working copies and are deleted <span>{RETENTION.uploadsAfterDraftsDays}</span> days after a listing&rsquo;s
          drafts exist in every shop it is meant for, and <span>{RETENTION.uploadsOtherwiseDays}</span> days after you
          last worked on them otherwise. The blank garment photos you save as mockup templates are kept until you
          delete them.</span>
        </p>
      </Section>

      <Section id="responsibilities" n={6} title="Your responsibilities">
        <p>
          <strong>Your designs and your rights.</strong> You confirm that you own, or are licensed to
          use, every design you upload, and that selling it does not infringe anyone else&rsquo;s
          rights. You are responsible for the intellectual property in what you upload and publish. The
          trademark filter, and the warning shown when a design appears to contain a recognisable
          character, are aids, not clearance: if you turn the filter off in Settings, you do so having
          accepted that risk.
        </p>
        <p>
          <strong>Your photos and mockups.</strong> You confirm that every blank garment photo you save as
          a mockup template is your own or that you have the right to use it, and that your listing
          photos, mockups included, show the item you sell accurately, as Etsy requires.
        </p>
        <p>
          <strong>Etsy&rsquo;s rules for sellers.</strong> You remain responsible for your Etsy account
          and shops and for following Etsy&rsquo;s{" "}
          <a href="https://www.etsy.com/legal/sellers" target="_blank" rel="noopener noreferrer" className={link}>
            Seller Policy
          </a>
          , including:
        </p>
        <Items>
          <li>
            <strong>No duplicate shops and no duplicate listings.</strong> Do not use the Service to
            open or run shops that duplicate one another, or to list the same item again and again to
            fill search results. Each of your shops should have its own listings.
          </li>
          <li>
            <strong>No search manipulation.</strong> Titles, tags and attributes must describe the item
            truthfully. Do not stuff them with keywords that do not describe it, and do not use them to
            mislead buyers or Etsy&rsquo;s search.
          </li>
          <li>
            <strong>Etsy&rsquo;s Creativity Standards.</strong> What you list must be made or designed
            by you as those standards require, and if a production partner makes it (for example a
            print-on-demand provider), you must say so on the listing. The Service copies &ldquo;who
            made it&rdquo;, &ldquo;when it was made&rdquo;, whether it is a supply, and your production
            partners from your reference listing; make sure the reference listing states them correctly.
          </li>
          <li>
            <strong>Nothing Etsy prohibits.</strong> Do not use the Service to list items that
            Etsy&rsquo;s Prohibited Items Policy or the law does not allow.
          </li>
        </Items>
        <p>
          <strong>Review every AI suggestion before you approve it.</strong> AI-suggested titles, tags
          and descriptions can be inaccurate or unsuitable, and can include words that belong to
          someone else&rsquo;s trademark or that Etsy&rsquo;s policies do not allow. Settings copied
          from a reference listing may not fit every new product. Our automated checks catch some
          problems, but they are an aid, not a guarantee. Check the title, tags, description, category,
          price, variations and images of each listing; approving and publishing are your decisions.
        </p>
        <p>
          Etsy limits how many requests an application may make each day, and that allowance is shared
          by everyone using the Service. When it runs low, some work is delayed until it resets. Your
          own remaining allowance is always shown in the app.
        </p>
      </Section>

      <Section id="publishing" n={7} title="Drafts and publishing">
        <p>
          <strong>Nothing is ever published without you.</strong> Every listing is created in your
          shop as a draft, and only for listings you have approved one by one. A draft becomes an
          active listing in exactly three ways, each of which is your own instruction:
        </p>
        <Items>
          <li>you press <em>Publish now</em> on an approved draft;</li>
          <li>you schedule a time for an approved draft, and it goes live at that time;</li>
          <li>
            you confirm a schedule for a set of listings you approved, after being shown every listing,
            shop and time in it.
          </li>
        </Items>
        <p>
          At the scheduled moment the Service checks again that the listing is still approved, that the
          shop is still connected and that the listing still passes our checks; if any of these fails,
          it is not published and you are told why. There is no setting that publishes listings you have
          not approved.
        </p>
      </Section>

      <Section id="content" n={8} title="Your content">
        <p>
          Your designs, and the listing text written for you, remain yours. You give us a limited,
          non-exclusive permission to store, process and transmit them only to provide the Service to
          you, including sending design images to our AI provider for analysis, as described in the{" "}
          <Link href="/privacy" className={link}>
            Privacy Policy
          </Link>
          . That permission ends when your content is deleted.
        </p>
        <p>
          We do not sell your designs or your data, show them to other users, use them for marketing,
          or use them or any Etsy data to train AI models.
        </p>
      </Section>

      <Section id="use" n={9} title="Acceptable use">
        <p>You agree not to:</p>
        <Items>
          <li>try to access another user&rsquo;s account or data;</li>
          <li>probe, bypass or overload the Service&rsquo;s security, rate limits or usage limits;</li>
          <li>
            use the Service to collect data about other sellers or about buyers, or to direct buyers
            away from Etsy;
          </li>
          <li>
            resell, rent or give others access to the Service or to the Etsy access it holds on your
            behalf;
          </li>
          <li>upload anything unlawful, infringing, or harmful, such as malware.</li>
        </Items>
      </Section>

      <Section id="suspension" n={10} title="Suspending or withdrawing the Service">
        <p>
          <strong>
            We will give you at least 30 days&rsquo; written notice before we suspend or withdraw the
            Service
          </strong>
          , in whole or in a part you rely on. The notice is sent by email to your account&rsquo;s
          address and shown in the app, so that you can finish the drafts you need and take your
          figures with you.
        </p>
        <p>
          Two things are not waited for. If Etsy ends our access to its API, we must stop at once (see
          the next section). And we may suspend an individual account without that notice where the
          law requires it, or where the account is being used to break these terms or to harm other
          users or Etsy; we will tell you why at the address of the account.
        </p>
        <p>
          Drafts and listings already created in your Etsy shop stay in your shop; suspending or
          withdrawing the Service does not remove them.
        </p>
      </Section>

      <Section id="etsy-access" n={11} title="If Etsy ends our access">
        <p>
          If Etsy suspends or ends our access to its API, <strong>we stop all use of Etsy data
          immediately</strong>: the Service stops making requests to Etsy, and we delete the Etsy data
          we hold (your shops&rsquo; tokens, the copies of your listings, and the sales, fee and
          statistics figures read from Etsy). We will tell you by email what happened. Your drafts and
          listings on Etsy are not affected.
        </p>
      </Section>

      <Section id="deletion" n={12} title="Deleting your account">
        <p>
          You can stop using the Service at any time. You can disconnect a shop on the Shops page,
          which deletes that shop&rsquo;s tokens and the Etsy data we hold for it at once, and you can
          delete your whole account in <strong>Settings</strong><span>. Deleting the account disconnects every
          shop at once, cancels anything queued or scheduled, and then deletes your uploads, your
          listings in the Service and the account itself within <span>{RETENTION.accountDeletionDays}</span> days.
          The Privacy Policy says exactly what is deleted and the little that is kept.</span>
        </p>
      </Section>

      <Section id="warranty" n={13} title="No warranty">
        <Conspicuous>
          The Service is provided &ldquo;as is&rdquo; and &ldquo;as available&rdquo;, without
          warranties of any kind, whether express or implied, including any implied warranties of
          merchantability, fitness for a particular purpose, title, and non-infringement. We do not
          warrant that the Service will be uninterrupted, secure or error-free, that suggested content
          will be accurate, complete or compliant with Etsy&rsquo;s policies, or that any listing will
          be accepted by Etsy or will sell. Etsy, Inc. makes no warranty of any kind regarding the
          Service.
        </Conspicuous>
        <p>
          Some jurisdictions do not allow certain warranties to be excluded; in that case the
          exclusions above apply only to the extent the law permits.
        </p>
      </Section>

      <Section id="liability" n={14} title="Limitation of liability">
        <Conspicuous>
          To the fullest extent permitted by law, we will not be liable for any indirect, incidental,
          special, consequential or punitive damages, or for any loss of profits, sales, revenue, data
          or goodwill, or for any action Etsy takes regarding your account or listings, arising out of
          or related to the Service. Our total liability to you for all claims relating to the Service
          is limited to the greater of (a) the amount you have paid us for the Service in the twelve
          months before the claim arose, and (b) fifty United States dollars (US$50). During free
          early access, the amount in (a) is zero. Etsy, Inc. has no liability to you in connection with the
          Service.
        </Conspicuous>
        <p>
          Nothing in these terms limits liability that cannot be limited by law, such as liability for
          fraud, for wilful misconduct or gross negligence, or for death or personal injury caused by
          negligence, and nothing affects rights you have as a consumer that cannot be waived.
        </p>
      </Section>

      <Section id="changes" n={15} title="Changes to these terms">
        <p>
          We may update these terms as the Service develops. When we change them, or the Privacy
          Policy, in a way that matters, the app asks you to read and agree to the new version before
          you go on; if you do not agree, you can stop using the Service and delete your account. The
          date at the top shows when the terms were last updated.
        </p>
      </Section>

      <Section id="law" n={16} title="Governing law and disputes">
        <p>
          These terms are governed by the laws of <Fact field="governing_law" />, without regard to its
          conflict-of-law rules. Any dispute will be brought before <Fact field="dispute_venue" />,
          unless the law of the country where you live gives you the right to bring it before your own
          courts, in which case that right is unaffected.
        </p>
        <p>
          Before starting any formal dispute, please write to us at <SupportEmail /> so we can try to
          resolve it directly.
        </p>
      </Section>

      <Section id="contact" n={17} title="Contact">
        <p>
          Questions about these terms, and support: <SupportEmail />. The Service is provided by{" "}
          <Fact field="operator_name" />, <Fact field="operator_location" />.
        </p>
      </Section>
    </LegalShell>
  );
}
