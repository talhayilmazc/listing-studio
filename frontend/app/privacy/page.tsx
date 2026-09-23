"use client";

import Link from "next/link";
import { Fact, Items, LegalShell, Section, SupportEmail } from "@/components/LegalShell";

const SECTIONS = [
  { id: "who", title: "Who we are" },
  { id: "collect", title: "What we collect" },
  { id: "not-collect", title: "What we do not collect" },
  { id: "use", title: "How we use it" },
  { id: "ai", title: "AI processing by Anthropic" },
  { id: "etsy", title: "Your Etsy data" },
  { id: "sharing", title: "Who we share it with" },
  { id: "storage", title: "Where it is stored and how it is protected" },
  { id: "retention", title: "How long we keep it" },
  { id: "disconnect", title: "Disconnecting your shop" },
  { id: "deletion", title: "Deleting your account and your rights" },
  { id: "cookies", title: "Cookies" },
  { id: "changes", title: "Changes to this policy" },
];

/** One row of the retention table. */
function Row({ data, period }: { data: React.ReactNode; period: React.ReactNode }) {
  return (
    <tr className="border-t border-slate-200 align-top">
      <td className="py-2.5 pr-4 text-slate-700">{data}</td>
      <td className="py-2.5 text-slate-700">{period}</td>
    </tr>
  );
}

export default function PrivacyPage() {
  return (
    <LegalShell title="Privacy Policy" updated="23 September 2026" sections={SECTIONS}>
      <Section id="who" n={1} title="Who we are">
        <p>
          Listing Studio (&ldquo;the Service&rdquo;) is operated by{" "}
          <Fact field="operator_name" />, <Fact field="operator_location" />, who is responsible
          for the personal data described here. For anything in this policy, write to{" "}
          <SupportEmail />.
        </p>
        <p>
          This policy explains what we collect, why, where it goes, how long we keep it, and how
          to have it deleted. It is written to be read, not skimmed past; the short version is
          that we collect what the Service needs to work, we keep Etsy&rsquo;s data only as long
          as Etsy allows, and we never sell anything.
        </p>
      </Section>

      <Section id="collect" n={2} title="What we collect">
        <p>
          <strong>Your account.</strong> Your email address, and your password stored only as a
          one-way argon2id hash — we never see or keep the password itself. We also record when
          the account was created and which invite code was used (the code is stored hashed).
        </p>
        <p>
          <strong>Your Etsy connection.</strong> When you connect your shop, Etsy gives us access
          and refresh tokens that let the Service act on your shop with the permissions you
          granted. We store them encrypted, and they are never shown in the app, never sent to
          your browser, and never written to logs. We also keep your Etsy user ID, shop ID, shop
          name, and the permissions granted.
        </p>
        <p>
          <strong>Data from your own Etsy shop.</strong> Your listings as Etsy returns them
          (titles, states, SKUs, sections, prices, descriptions and links to their images);
          for each profile, the reference listing&rsquo;s category, attributes, price,
          variations, shipping and production settings, description and image list; and, before
          we change an existing listing, a copy of how it looked, so the change can be undone.
        </p>
        <p>
          <strong>Your uploads.</strong> The design images you upload, the resized versions we
          make from them, their file and folder names, the SKU read from them, and their size
          and dimensions.
        </p>
        <p>
          <strong>Generated content.</strong> The titles, tags and descriptions created for your
          designs and any edits you make, whether you approved them, which AI model produced
          them and how many tokens it used, and the Etsy ID and status of drafts we create.
        </p>
        <p>
          <strong>Usage and security records.</strong> A daily count of Etsy API requests made
          for your account (Etsy caps these, so we must track them); records of the tasks we ran
          for you and whether they succeeded; your sign-in session; counters of failed sign-in
          attempts and request rates, keyed by email and IP address; and server logs, which
          contain IP addresses, the pages and actions requested, and response codes. Before a
          log line is written we remove passwords, tokens, session identifiers, OAuth codes and
          email addresses from it.
        </p>
      </Section>

      <Section id="not-collect" n={3} title="What we do not collect">
        <Items>
          <li>
            Payment details — the Service is free and takes no payments.
          </li>
          <li>Anything about your buyers, your orders, or your Etsy messages.</li>
          <li>
            Any data about other Etsy sellers — their listings, tags, images or prices — in any
            form.
          </li>
          <li>
            Advertising or analytics tracking. There are no third-party trackers, pixels or
            analytics scripts in the Service.
          </li>
        </Items>
      </Section>

      <Section id="use" n={4} title="How we use it">
        <Items>
          <li>
            <strong>To provide the Service</strong>: to analyse your designs, prepare listings,
            and create and publish drafts in your shop when you ask. This is necessary to perform
            our agreement with you.
          </li>
          <li>
            <strong>To keep it secure</strong>: rate limits, sign-in lockouts and logs protect
            your account and the Service. This is our legitimate interest in running a secure
            service.
          </li>
          <li>
            <strong>To support you</strong>: when you write to us about a problem.
          </li>
          <li>
            <strong>To meet our obligations</strong>: under the law and under Etsy&rsquo;s API
            terms, which govern how we may handle Etsy&rsquo;s data.
          </li>
        </Items>
        <p>We do not use your data for advertising, profiling, or selling to anyone.</p>
      </Section>

      <Section id="ai" n={5} title="AI processing by Anthropic">
        <p>
          <strong>Your design images are sent to Anthropic for analysis.</strong> To describe a
          design and write its listing text, the Service sends the following to the API of
          Anthropic, PBC, the company behind the Claude models:
        </p>
        <Items>
          <li>each design image you ask us to generate a listing for, in resized form;</li>
          <li>its SKU and the kind of product it is (for example, apparel);</li>
          <li>
            occasionally, an image from one of your own Etsy reference listings, when our local
            check cannot tell whether that image is a size chart or artwork.
          </li>
        </Items>
        <p>
          Anthropic processes this to return the analysis and nothing else. Under
          Anthropic&rsquo;s{" "}
          <a
            href="https://www.anthropic.com/legal/commercial-terms"
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-brand-700 hover:underline"
          >
            commercial terms
          </a>
          , content sent through its API is <strong>not used to train its models</strong>.
          Anthropic may retain API inputs and outputs for a limited period for safety and abuse
          monitoring, as described in its{" "}
          <a
            href="https://www.anthropic.com/legal/privacy"
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-brand-700 hover:underline"
          >
            privacy policy
          </a>
          .
        </p>
        <p>
          We ourselves never use your designs to train any model. If you do not want a design
          sent to Anthropic, do not generate a listing for it; uploading alone does not send it.
        </p>
      </Section>

      <Section id="etsy" n={6} title="Your Etsy data">
        <p>
          We get Etsy data only from <strong>your own shop</strong>, only through{" "}
          <strong>Etsy&rsquo;s official Open API</strong>, and only with the permissions you
          grant when you connect: reading and writing your listings and your shop. We never
          scrape Etsy&rsquo;s website, and we never access another seller&rsquo;s shop.
        </p>
        <p>
          We write to your shop only to do what you asked: create drafts, upload their images,
          set their variations and SKUs, publish a draft when you press <em>Publish now</em>, and
          replace a listing&rsquo;s images when you ask for that. We never publish on our own.
        </p>
        <p>
          Etsy limits how long an application may keep its data, and we follow those limits; see{" "}
          <a href="#retention" className="font-medium text-brand-700 hover:underline">
            How long we keep it
          </a>
          .
        </p>
      </Section>

      <Section id="sharing" n={7} title="Who we share it with">
        <p>We do not sell or rent your data. It is shared only with:</p>
        <Items>
          <li>
            <strong>Anthropic</strong> — the images and details listed above, for AI analysis.
          </li>
          <li>
            <strong>Etsy</strong> — only what you instruct us to send to your own shop.
          </li>
          <li>
            <strong>Cloudflare</strong> — traffic to the Service passes through Cloudflare&rsquo;s
            network, which carries it securely to our server.
          </li>
          <li>
            <strong>Our server hosting provider</strong> — the Service&rsquo;s database and files
            live on a server we rent and administer.
          </li>
          <li>
            <strong>Authorities</strong> — only where the law requires us to disclose information.
          </li>
        </Items>
        <p>
          Some of these providers may process data outside the country where you live. Where
          they do, we rely on the safeguards they offer for international transfers.
        </p>
      </Section>

      <Section id="storage" n={8} title="Where it is stored and how it is protected">
        <p>
          Your data is stored in a database and file store on a server administered by the
          operator. Connections to the Service are encrypted in transit. Etsy tokens are
          encrypted at rest, and passwords are stored only as hashes. Every account&rsquo;s data
          is kept separate, and the Service checks on every request that you can reach only your
          own. No method of storage is perfectly secure, but we work to protect your data and
          will tell you promptly if a breach affects it.
        </p>
      </Section>

      <Section id="retention" n={9} title="How long we keep it">
        <p>
          Data from Etsy is kept only as long as Etsy&rsquo;s API terms allow, and is deleted
          automatically when that time runs out. The Service checks for expired data every 15
          minutes.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-[0.08em] text-slate-500">
                <th className="pb-2 pr-4 font-medium">Data</th>
                <th className="pb-2 font-medium">Kept for</th>
              </tr>
            </thead>
            <tbody>
              <Row
                data="Your shop's listings, including image links"
                period="6 hours at most, then deleted and fetched again when you next need them"
              />
              <Row
                data="Reference listing details for each profile"
                period="24 hours at most, then cleared. Your profile's own settings — its name, template, title prefix and chosen size charts — stay until you delete the profile"
              />
              <Row
                data="Copies of listings taken before we change them"
                period="90 days, then deleted automatically"
              />
              <Row
                data="Etsy access tokens"
                period="Until you disconnect your shop or your account is deleted"
              />
              <Row
                data="Your uploads, generated content, profiles, task records and usage counts"
                period="While your account exists, or until you ask us to delete them"
              />
              <Row
                data="Your sign-in session"
                period="30 days after you last used it; ended at once when you sign out or change your password"
              />
              <Row
                data="Failed sign-in and request-rate counters"
                period="15 minutes at most"
              />
              <Row
                data="Server logs"
                period="Rotated automatically, and kept only as long as needed for security and troubleshooting"
              />
              <Row
                data="Backups"
                period="14 days; data you delete may remain in a backup until that backup expires"
              />
            </tbody>
          </table>
        </div>
        <p>
          We do not store the images from your Etsy reference listings: they are fetched, checked
          in memory to tell size charts from artwork, and discarded. We keep only the result of
          that check, which follows the 24-hour limit above.
        </p>
      </Section>

      <Section id="disconnect" n={10} title="Disconnecting your shop">
        <p>
          You can disconnect your Etsy shop at any time from the{" "}
          <Link href="/connect" className="font-medium text-brand-700 hover:underline">
            Connection
          </Link>{" "}
          page. When you do, we <strong>immediately delete</strong> your Etsy tokens and every
          piece of Etsy data we hold for you: the copy of your shop&rsquo;s listings, the
          reference details of your profiles, and all saved copies of listings. Your uploads,
          generated drafts and profile settings stay, so you can reconnect later and carry on.
        </p>
        <p>
          Listings and drafts already created in your Etsy shop are not affected: they live in
          your shop, and you manage them on Etsy.
        </p>
        <p>
          You can also withdraw the Service&rsquo;s access from your Etsy account settings. That
          stops us using the connection, but to have us delete the Etsy data we hold, also press
          Disconnect here or write to us.
        </p>
      </Section>

      <Section id="deletion" n={11} title="Deleting your account and your rights">
        <p>
          To delete your account, write to <SupportEmail /> from the email address you signed up
          with. We will delete your account and everything associated with it — your tokens,
          Etsy data, uploads, generated content, profiles and records — within 30 days and
          confirm when it is done. Copies may remain in backups for up to 14 more days until
          those backups expire.
        </p>
        <p>
          Depending on where you live, you may have the right to ask for a copy of your data, to
          have it corrected or deleted, to restrict or object to how we use it, and to receive it
          in a portable format. To use any of these rights, write to <SupportEmail />; we reply
          within 30 days. You also have the right to complain to the data protection authority
          in your country.
        </p>
        <p>The Service is not intended for anyone under 18.</p>
      </Section>

      <Section id="cookies" n={12} title="Cookies">
        <p>
          The Service sets a single cookie, named <code className="text-sm">session</code>, which
          keeps you signed in. It is strictly necessary, cannot be read by scripts on the page,
          and lasts 30 days from your last visit. There are no advertising, analytics or
          third-party cookies.
        </p>
      </Section>

      <Section id="changes" n={13} title="Changes to this policy">
        <p>
          If we change this policy in a way that matters, we will tell you in the app or by email
          at least 14 days before the change takes effect. The date at the top shows when it was
          last updated.
        </p>
        <p>
          Questions: <SupportEmail />. Operator: <Fact field="operator_name" />,{" "}
          <Fact field="operator_location" />.
        </p>
      </Section>
    </LegalShell>
  );
}
