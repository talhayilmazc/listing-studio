import Link from "next/link";
import {
  AnalyticsMonths,
  Fact,
  Items,
  LegalShell,
  Section,
  SupportEmail,
  WhenErrorTracking,
} from "@/components/LegalShell";
import { LAST_UPDATED, PROVIDER_DISCLAIMER, RETENTION as R } from "@/lib/legal";

export const metadata = {
  title: "Privacy Policy",
  description: "What Listyro collects, why, where it goes, how long it is kept and how to have it deleted.",
  alternates: { canonical: "/privacy" },
};

// Written from the code. Every period below is printed from lib/legal.ts
// (RETENTION), and backend/tests/test_legal_texts.py fails if one of those
// figures differs from the setting the code actually uses, or if a period is
// typed into this text instead of taken from there.
const SECTIONS = [
  { id: "who", title: "Who we are" },
  { id: "collect", title: "What we collect" },
  { id: "etsy", title: "The Etsy data we read, why, and for how long" },
  { id: "never", title: "What is never stored" },
  { id: "use", title: "How we use it" },
  { id: "ai", title: "AI processing by Anthropic" },
  { id: "sharing", title: "Who processes it; no selling, no advertising" },
  { id: "security", title: "Where it is stored and how it is protected" },
  { id: "retention", title: "How long we keep everything" },
  { id: "disconnect", title: "Disconnecting a shop" },
  { id: "deletion", title: "Deleting your account" },
  { id: "rights", title: "Your rights and your data" },
  { id: "cookies", title: "Cookies and browser storage" },
  { id: "changes", title: "Changes to this policy" },
];

const link = "font-medium text-brand-700 hover:underline";

/** One row of a table: what, why (optional), how long. */
function Row({ data, why, period }: { data: React.ReactNode; why?: React.ReactNode; period: React.ReactNode }) {
  // On a phone each row is a block: the columns stack under their own small
  // captions, so nothing has to be scrolled sideways to be read.
  const caption = "mb-0.5 block text-[11px] font-medium uppercase tracking-wide text-slate-500 sm:hidden";
  return (
    <tr className="block border-t border-slate-200 py-3 align-top sm:table-row sm:border-0 sm:py-0">
      <td className="block text-slate-800 sm:table-cell sm:border-t sm:border-slate-200 sm:py-3 sm:pr-4">{data}</td>
      {why !== undefined && (
        <td key="why" className="mt-2 block text-slate-600 sm:mt-0 sm:table-cell sm:border-t sm:border-slate-200 sm:py-3 sm:pr-4">
          <span className={caption}>Why</span>
          <span>{why}</span>
        </td>
      )}
      <td className="mt-2 block text-slate-600 sm:mt-0 sm:table-cell sm:border-t sm:border-slate-200 sm:py-3">
        <span className={caption}>Kept for</span>
        <span>{period}</span>
      </td>
    </tr>
  );
}

function Table({ head, children }: { head: string[]; children: React.ReactNode }) {
  return (
    <table className="block w-full text-left text-sm sm:table">
      <thead className="hidden sm:table-header-group">
        <tr className="text-xs uppercase tracking-wide text-slate-500">
          {head.map((h) => (
            <th key={h} className="pb-2 pr-4 font-medium">{h}</th>
          ))}
        </tr>
      </thead>
      <tbody className="block sm:table-row-group">{children}</tbody>
    </table>
  );
}

export default function PrivacyPage() {
  return (
    <LegalShell title="Privacy Policy" updated={LAST_UPDATED} sections={SECTIONS}>
      <Section id="who" n={1} title="Who we are">
        <p>
          Listyro (&ldquo;the Service&rdquo;) is provided by <Fact field="operator_name" />,{" "}
          <Fact field="operator_location" />, who is responsible for the personal data described here.
          {" "}
          <span className="text-sm uppercase tracking-wide text-slate-600">{PROVIDER_DISCLAIMER}</span>{" "}
          For anything in this policy, including privacy requests, write to <SupportEmail />.
        </p>
        <p>
          This policy says what we collect, why, where it goes, how long we keep it, and how to have it
          deleted. The short version: we collect what the Service needs to work, we read only your own
          shops&rsquo; data from Etsy and keep it only as long as stated here, we store nothing about
          your buyers, and we never sell or share anything.
        </p>
      </Section>

      <Section id="collect" n={2} title="What we collect">
        <p>
          <strong>Your account.</strong> Your email address, and your password stored only as a one-way
          argon2id hash: we never see or keep the password itself. We record when the account was
          created, which invite was used (the code is stored hashed), the time zone you choose, and
          which version of the Terms of Service and this policy you agreed to and when.
        </p>
        <p>
          <strong>Your Etsy connections.</strong> You can connect more than one of your own shops; each
          is connected separately, by signing in to Etsy as that shop&rsquo;s owner. For each shop, Etsy
          gives us access and refresh tokens that let the Service act on that shop with the permissions
          you granted. We store them encrypted; they are never shown in the app, never sent to your
          browser, and never written to logs. We also keep each shop&rsquo;s Etsy user ID, shop ID, shop
          name, the name you give it in the app, and the permissions granted.
        </p>
        <p>
          <strong>Your uploads.</strong> The design images you upload are resized and stored in that
          resized form only (the file as you sent it is not kept), with their file and folder names,
          the SKU read from them, and their dimensions. If you make mockups, the blank garment photos you
          save in templates are kept too, with the template&rsquo;s name and the photos&rsquo; order, the
          garment&rsquo;s outline, a map of where the garments are in the photo, the print area of each
          garment (as suggested and as you set it), whether and when you confirmed it, whether the photo
          shows the front or the back, the settings you chose, and when you confirmed you may use each
          photo. Where something is in front of the
          garment (hair, skin, accessories or a thing held in front of it), a mask of it is kept with the
          photo, so designs can be placed behind it, together with anything you paint with the
          &ldquo;Keep in front&rdquo; brush. When you confirm a photo, we also keep a few numbers about
          where you placed each print on the garment (its width, its distance below the collar and its
          shape, relative to the garment&rsquo;s size, with the garment&rsquo;s type and layout), only to
          suggest your next photos the same way; they are never used for any other account, and
          &ldquo;Reset&rdquo; on the Templates page deletes them. Photos are analysed on our own server by bundled
          open-source image-analysis models; no photo is sent to any outside service for this. The
          mockups made from your designs are kept like your other images; a ZIP of them you ask to
          download is kept for a short time, as below. Mockups are made on our own server; nothing about
          them is sent to our AI provider.
        </p>
        <p>
          <strong>The listings you prepare.</strong> The titles, tags and descriptions suggested for
          your designs and every edit you make, whether you approved them, the options you chose
          (profile, shops, SKU, personalization, section, schedule), and the Etsy ID and status of the
          drafts we create for you.
        </p>
        <p>
          <strong>What you tell us about your costs.</strong> The product costs and fee settings you
          enter for your profit reporting, and the figures from the monthly statements and Etsy Ads
          reports you choose to upload (the files themselves are read and not kept).
        </p>
        <p>
          <strong>If you ask for an invite.</strong> What you type into the request form on our site:
          your email address, your shop&rsquo;s name or link, how many shops you run, and your note. We
          use it only to decide on your request and to send you an invite. When a request arrives, a
          short notification (the email address, the shop and the number of shops; not your note)
          is sent to the private messaging channel where we receive our own operational alerts, so
          that we answer it. The form also counts requests per connection for an hour to stop abuse;
          your IP address is used for that count and is not stored with your request.
        </p>
        <p>
          <strong>Usage and security records.</strong> A daily count of the Etsy API requests made for
          your account (Etsy caps these, so we must track them); records of the tasks we ran for you
          and whether they succeeded; a log of administrative and destructive actions on your account
          (what was done and when, with counts and internal ids, never content); counts of the AI
          calls made for you and their cost to us; your sign-in session; counters of failed sign-in
          attempts and request rates, keyed by email and IP address; and server logs, which contain IP
          addresses, the pages and actions requested, and response codes. Before a log line is written
          we remove passwords, tokens, session identifiers, OAuth codes and email addresses from it.
        </p>
      </Section>

      <Section id="etsy" n={3} title="The Etsy data we read, why, and for how long">
        <p>
          We get Etsy data only from <strong>your own shops</strong>, only through{" "}
          <strong>Etsy&rsquo;s official Open API</strong>, and only with the permissions you grant when
          you connect a shop. We never scrape Etsy&rsquo;s website, and we never read another
          seller&rsquo;s shop. This is every kind of Etsy data we read:
        </p>
        <Table head={["What we read from your shop", "Why", "Kept for"]}>
          <Row
            data="Your shop's listings as Etsy returns them: titles, states, SKUs, sections, prices, tags, descriptions, and links to their images"
            why="To show your shop's listings, to let you pick a reference listing for a profile, and to name the listings in your reports"
            period={<span>{`${R.listingCacheHours} hours at most, then deleted and read again when you next need them. Nothing older than that is ever shown`}</span>}
          />
          <Row
            data="A reference listing's images, as shown on its profile"
            why="So you can see the profile's images and mark which are size charts"
            period={<span>{`${R.referenceImagesHours} hours at most; then the links are deleted and the images are no longer shown until the profile is refreshed. The image files themselves are never stored: they are fetched, checked in memory to tell size charts from artwork, and discarded`}</span>}
          />
          <Row
            data="A reference listing's settings: category, attributes, price, variations, shipping profile, return policy, processing profile, production partners, who made it, when it was made, description, and which of its images are size charts"
            why="To build your new drafts so they match the listing you chose. These are used, not displayed"
            period={<span>{`${R.otherEtsyContentHours} hours at most, then cleared and read again when the profile is next used`}</span>}
          />
          <Row
            data="Your shop's own lists: its sections, shipping profiles, return policies, processing profiles and production partners (names and ids)"
            why="To put a draft in one of your sections, and to use a profile in another of your shops with that shop's own settings"
            period={<span>{`${R.otherEtsyContentHours} hours at most for the lists. The ids a profile uses in a shop are kept until you stop using the profile there or disconnect that shop`}</span>}
          />
          <Row
            data="A copy of a listing as it was, taken just before the Service changes it"
            why="So a change the Service made to your listing can be checked and undone"
            period={<span>{`${R.listingSnapshotDays} days, then deleted automatically`}</span>}
          />
          <Row
            data="The record of each draft the Service made in your shop: its Etsy listing id, its state (draft, live or deleted on Etsy) and when it went live; for a draft that was started but not finished, its listing id and the title that was sent"
            why="To publish a draft you approved or scheduled, to finish a half-made draft without ever making a second one, and to count what you published"
            period={<span>{`Until you disconnect the shop or delete your account. A draft that was started and never finished: ${R.draftAttemptDays} days. A half-made draft you took off the list: ${R.draftDismissalDays} days`}</span>}
          />
          <Row
            data="Views and favourites of the listings you published with the Service (two totals per listing, read once a day)"
            why="To show how each listing is doing over time"
            period={<span><span>Daily figures, for </span><AnalyticsMonths fallback={R.analyticsMonths} /><span> months</span></span>}
          />
          <Row
            data="Your shop's sales, line by line: the order number, which listing sold, the quantity, the price, the shipping paid and the date"
            why="For your profit, cost and ads reporting: which of your listings sell and what they earn"
            period={<span><span>Daily totals per listing and the order lines, for </span><AnalyticsMonths fallback={R.analyticsMonths} /><span> months. Everything else in Etsy&rsquo;s answer, including anything about the buyer, is discarded as soon as those fields are read, and is written nowhere</span></span>}
          />
          <Row
            data="Your shop's payment account ledger: for each entry its type (for example a transaction fee, a listing fee or an Etsy Ads charge), its amount and its date"
            why="So your profit is worked out from what Etsy actually charged, not from an estimate"
            period={<span><span>One total per type per day, for </span><AnalyticsMonths fallback={R.analyticsMonths} /><span> months. The entries themselves, their descriptions and their references are not stored</span></span>}
          />
          <Row
            data="How many listings your shop has in each state (five counts)"
            why="For the summary line at the top of the app"
            period={<span>{`${R.otherEtsyContentHours} hours at most`}</span>}
          />
        </Table>
        <p>
          We write to your shop only to do what you asked: create drafts, upload their images, set
          their variations, attributes, personalization and SKUs, publish a draft when you press{" "}
          <em>Publish now</em> or at a time you scheduled for a listing you approved, replace a
          listing&rsquo;s images when you ask for that, and create a section, shipping profile or
          return policy only when you confirm it.
        </p>
        <p>
          Reading sales is a permission added on 26 September 2026. A shop connected before then is
          asked to reconnect once to grant it; until it does, everything else keeps working and sales
          figures are simply not shown.
        </p>
      </Section>

      <Section id="never" n={4} title="What is never stored">
        <Items>
          <li>
            <strong>Nothing about your buyers.</strong> No buyer names, postal addresses, email
            addresses, usernames or messages are stored, in any table or file. Etsy&rsquo;s sales
            answers contain some buyer fields; the Service reads only the fields listed above from
            each line and drops the rest in memory. We do not use Etsy&rsquo;s receipts endpoint, which
            returns buyer names and addresses, at all.
          </li>
          <li>
            <strong>Nothing about other sellers.</strong> No other shop&rsquo;s listings, tags, images,
            prices or sales, in any form.
          </li>
          <li>
            <strong>No statement or report files.</strong> A monthly statement or Etsy Ads report you
            upload is read in memory and discarded; only totals, order numbers and amounts are kept.
          </li>
          <li>
            <strong>No tracking.</strong> There are no third-party trackers, pixels, analytics or
            advertising scripts in the Service or on this site.
          </li>
        </Items>
      </Section>

      <Section id="use" n={5} title="How we use it">
        <Items>
          <li>
            <strong>To provide the Service</strong>: to analyse your designs, prepare listings, create
            and publish drafts in your shops when you ask, and report your own shops&rsquo; figures to
            you. This is necessary to perform our agreement with you.
          </li>
          <li>
            <strong>To keep it secure</strong>: rate limits, sign-in lockouts and logs protect your
            account and the Service. This is our legitimate interest in running a secure service.
          </li>
          <li>
            <strong>To support you</strong>: when you write to us about a problem.
          </li>
          <li>
            <strong>To meet our obligations</strong>: under the law and under Etsy&rsquo;s API terms,
            which govern how we may handle Etsy&rsquo;s data.
          </li>
        </Items>
        <p>
          Each seller&rsquo;s data is used only for that seller. We do not combine one seller&rsquo;s
          Etsy data or sales with another&rsquo;s, in any report, comparison or model. We do not use
          your data, or any Etsy data, for advertising or profiling, and we do not use it to train or
          fine-tune any AI model.
        </p>
      </Section>

      <Section id="ai" n={6} title="AI processing by Anthropic">
        <p>
          <strong>Your design images and listing text are sent to Anthropic for analysis.</strong> To
          describe a design and suggest its listing text, the Service sends the following to the API of
          Anthropic, PBC, the company behind the Claude models:
        </p>
        <Items>
          <li>each design image you ask us to write a listing for, in resized form;</li>
          <li>
            its SKU, the kind of product it is (for example, apparel), and the title prefix of the
            profile you chose;
          </li>
          <li>
            if you chose one of your own listings as an example to follow, that listing&rsquo;s title
            and tags;
          </li>
          <li>
            occasionally, an image from one of your own Etsy reference listings, when our local check
            cannot tell whether that image is a size chart or artwork.
          </li>
        </Items>
        <p>
          <strong>Never anything about buyers, sales or orders.</strong> Anthropic processes what it
          receives to return the analysis and nothing else. Under Anthropic&rsquo;s{" "}
          <a href="https://www.anthropic.com/legal/commercial-terms" target="_blank" rel="noopener noreferrer" className={link}>
            commercial terms
          </a>
          , content sent through its API is <strong>not used to train its models</strong>. Anthropic may
          retain API inputs and outputs for a limited period for safety and abuse monitoring, as
          described in its{" "}
          <a href="https://www.anthropic.com/legal/privacy" target="_blank" rel="noopener noreferrer" className={link}>
            privacy policy
          </a>
          .
        </p>
        <p>
          We ourselves never use your designs or any Etsy data to train any model. If you do not want a
          design sent to Anthropic, do not ask for a listing to be written for it; uploading alone does
          not send it.
        </p>
      </Section>

      <Section id="sharing" n={7} title="Who processes it; no selling, no advertising">
        <p>
          <strong>We do not sell, rent or share your data, and we send nothing to advertising or
          analytics platforms.</strong> It is processed only by:
        </p>
        <Items>
          <li>
            <strong>Anthropic</strong> (United States): the images and listing text listed above, for
            AI analysis.
          </li>
          <li>
            <strong>Cloudflare</strong>: all traffic to the Service passes through Cloudflare&rsquo;s
            network, which carries it encrypted to our server.
          </li>
          <li>
            <strong>Our own server in Türkiye</strong>: the Service&rsquo;s database and files live on
            one server that we rent and administer ourselves.
          </li>
          <li>
            <strong>Etsy</strong>: only what you instruct us to send to your own shop.
          </li>
          <WhenErrorTracking>
            <li>
              <strong>Sentry</strong>: when something in the Service fails, a technical report of the
              error goes to Sentry so we can fix it. Before it leaves our server we remove your email
              address, your IP address, cookies, passwords, tokens and anything you submitted; what
              remains is which part of the code failed and why.
            </li>
          </WhenErrorTracking>
          <li>
            <strong>Our alert channel</strong>: short operational messages (a deploy failed, the disk is
            filling) reach us through a private messaging channel. They carry no seller data and no
            Etsy data; the one exception is the invite-request notification described above.
          </li>
          <li>
            <strong>Authorities</strong>: only where the law requires us to disclose information.
          </li>
        </Items>
        <p>
          Because the Service is hosted in Türkiye, using it transfers your data to Türkiye if you live
          elsewhere. Anthropic is based in the United States and may process what it receives there,
          and Cloudflare carries traffic through its global network. Where data crosses borders, we
          rely on the safeguards these providers offer for international transfers.
        </p>
      </Section>

      <Section id="security" n={8} title="Where it is stored and how it is protected">
        <Items>
          <li>
            <strong>Encrypted tokens.</strong> Etsy access and refresh tokens are encrypted at rest and
            decrypted only at the moment a request is sent to Etsy for you. They never appear in the
            app, in your browser, in an error message or in a log.
          </li>
          <li>
            <strong>HTTPS everywhere.</strong> Connections to the Service are encrypted in transit; the
            server is reachable only through Cloudflare&rsquo;s encrypted tunnel.
          </li>
          <li>
            <strong>Access control.</strong> Every account&rsquo;s data is kept separate, and the
            Service checks on every request that you can reach only your own. Administrators of the
            Service see account-level facts and counts (your email address, your shops&rsquo; names,
            how many listings and requests) and not your designs, listings, or sales figures.
          </li>
          <li>
            <strong>Passwords</strong> are stored only as argon2id hashes, and repeated failed sign-ins
            are locked out.
          </li>
        </Items>
        <p>
          No method of storage is perfectly secure. If a security incident affects your data, we will
          tell you without delay and within 24 hours of discovering it, and we will notify Etsy as its
          API terms require.
        </p>
      </Section>

      <Section id="retention" n={9} title="How long we keep everything">
        <p>
          <span>Expired data is deleted automatically; the Service looks for it every <span>{R.cleanupMinutes}</span>{" "}
          minutes. The periods for Etsy data are in the table above; these are the rest.</span>
        </p>
        <Table head={["Data", "Kept for"]}>
          <Row
            data="The image files you upload (the resized version)"
            period={<span>{`${R.uploadsAfterDraftsDays} days after a draft of the listing made from them exists in every shop it is meant for, with nothing scheduled for it; otherwise ${R.uploadsOtherwiseDays} days after you last worked on them. They are then deleted automatically, except one small thumbnail of the cover, which stays with the listing's record. Deleted at once when you delete the batch`}</span>}
          />
          <Row
            data="The blank garment photos you save as mockup templates, with each one's outline, the map of where its garments are, the print area of each garment and its confirmation, front or back, the template it is in and the mask of what is in front of the garment"
            period="Until you delete the template or your account. The designs you upload to make mockups, and the mockups made from them, are image files like the rest and are kept as above"
          />
          <Row
            data="A ZIP of mockups you ask to download (only you can download it)"
            period={<span>{`${R.mockupZipHours} hours after it is ready, then deleted automatically`}</span>}
          />
          <Row
            data="Analytics history: daily sales totals and order lines, daily fee and ad totals, the figures from statements and Etsy Ads reports you uploaded, daily views and favourites, and the versions of a listing's text that have ended"
            period={<span><AnalyticsMonths fallback={R.analyticsMonths} /><span> months, then deleted automatically; deleted at once when you disconnect the shop</span></span>}
          />
          <Row
            data="The listings you prepare, your profiles' own settings, your costs, task records, usage counts, and the names and order of your uploads"
            period="While your account exists, or until you delete them"
          />
          <Row
            data="Etsy access tokens"
            period="Until you disconnect the shop or delete your account; deleted at once at that moment"
          />
          <Row
            data="An invite request from the form on our site"
            period={<span>{`${R.inviteRequestDays} days from the request if it is still waiting, or ${R.inviteRequestDays} days after we approve or decline it; then deleted automatically`}</span>}
          />
          <Row
            data="Your sign-in session"
            period={<span>{`${R.sessionDays} days after you last used it; ended at once when you sign out or change your password`}</span>}
          />
          <Row
            data="Failed sign-in and request-rate counters"
            period={<span>{`${R.counterMinutes} minutes at most`}</span>}
          />
          <Row
            data="Our records of AI calls made for your account (model, token counts, cost; no content)"
            period={<span>{`${R.aiCallMonths} months`}</span>}
          />
          <Row
            data="The log of administrative and destructive actions (what and when, with counts and internal ids; no content, no email addresses)"
            period="For the life of the Service; after an account is deleted its entries no longer point to anyone"
          />
          <Row
            data="Server logs"
            period="Rotated automatically by size, and kept only as long as needed for security and troubleshooting"
          />
          <Row
            data="Backups of our database (the image files you upload are not backed up)"
            period={<span>{`${R.backupDays} days; data you delete may remain in a backup until that backup expires`}</span>}
          />
        </Table>
      </Section>

      <Section id="disconnect" n={10} title="Disconnecting a shop">
        <p>
          You can disconnect any of your shops at any time on the{" "}
          <Link href="/connect" className={link}>
            Shops
          </Link>{" "}
          page. When you do, at once:
        </p>
        <Items>
          <li>that shop&rsquo;s Etsy tokens are deleted, so no further request can be made for it;</li>
          <li>
            everything queued or scheduled for it is cancelled (jobs waiting to run, scheduled
            go-lives, planned drafts), and the page tells you what was cancelled;
          </li>
          <li>
            every piece of Etsy data we hold from it is deleted: the copy of its listings, the settings
            read from its reference listings, the saved copies of its listings, its sales, fee and
            statement figures, its views and favourites, the records of its drafts, and the settings
            that connect your profiles to it.
          </li>
        </Items>
        <p>
          A profile you also use in another shop stays, without the details read from the disconnected
          shop, until you choose a reference listing in a remaining shop; a profile used only in the
          disconnected shop is deleted. Your other shops are not affected, and your uploads and the
          listings you prepared stay, so you can reconnect later and carry on. Listings and drafts
          already in your Etsy shop are not affected: they live in your shop, and you manage them on
          Etsy.
        </p>
        <p>
          You can also withdraw the Service&rsquo;s access in your Etsy account settings. That stops us
          using the connection, but to have us delete the Etsy data we hold, also press Disconnect
          here or write to us.
        </p>
      </Section>

      <Section id="deletion" n={11} title="Deleting your account">
        <p>
          You can delete your account yourself in{" "}
          <Link href="/settings" className={link}>
            Settings
          </Link>
          , under <em>Delete account</em>. When you confirm with your password:
        </p>
        <Items>
          <li>
            <strong>At once</strong>: every shop is disconnected as described above (tokens and Etsy
            data deleted, queued and scheduled work cancelled), every sign-in session ends, and the
            account can no longer be used.
          </li>
          <li>
            <strong><span>Then, by an automatic job, within <span>{R.accountDeletionDays}</span> days at the latest</span></strong>{" "}
            (normally within minutes): your uploaded image files, your batches, the listings you
            prepared, your profiles, your costs and reports, your task and usage records, and the
            account itself are deleted.
          </li>
        </Items>
        <p>
          <strong>What is kept, and why.</strong><span> The log of administrative and destructive actions
          keeps its entries about the account (for example &ldquo;a shop was disconnected&rdquo;, with
          counts), because it is our record that deletions happened; they contain no content and no
          email address, and no longer point to an account. Our records of AI calls keep their numbers
          (model, tokens, cost), unlinked from the account, because they are our own accounts. Copies
          in database backups expire within <span>{R.backupDays}</span> days. Nothing on Etsy is touched: your
          drafts and listings are yours and stay in your shop.</span>
        </p>
        <p>
          If you cannot sign in, write to <SupportEmail /><span> from the email address of the account and we
          will delete it for you within the same <span>{R.accountDeletionDays}</span> days.</span>
        </p>
      </Section>

      <Section id="rights" n={12} title="Your rights and your data">
        <p>
          <strong>Getting your data out.</strong> The tables on the Analytics page can be downloaded as
          CSV files, and the listings you prepare are in your own Etsy shop as drafts. There is no
          one-click export of a whole account; if you want a copy of the data we hold about you, write
          to <SupportEmail /> and we will send it.
        </p>
        <p>
          Depending on where you live, you may have the right to ask for a copy of your data, to have
          it corrected or deleted, to restrict or object to how we use it, and to receive it in a
          portable format. To use any of these rights, write to <SupportEmail />; we reply within 30
          days. You also have the right to complain to the data protection authority in your country.
        </p>
        <p>The Service is not intended for anyone under 18.</p>
      </Section>

      <Section id="cookies" n={13} title="Cookies and browser storage">
        <p>
          The Service sets a single cookie, named <code className="rounded bg-slate-100 px-1 text-sm">session</code><span>,
          which keeps you signed in. It is strictly necessary, cannot be read by scripts on the page,
          and lasts <span>{R.sessionDays}</span> days from your last visit. Your browser also remembers two
          conveniences on your own device: which of your shops you last had selected, and how you last
          chose to group an upload. There are no advertising, analytics or third-party cookies.</span>
        </p>
      </Section>

      <Section id="changes" n={14} title="Changes to this policy">
        <p>
          When we change this policy or the Terms of Service in a way that matters, the app asks you to
          read and agree to the new version before you go on. The date at the top shows when it was
          last updated.
        </p>
        <p>
          Questions and privacy requests: <SupportEmail />. Provided by <Fact field="operator_name" />,{" "}
          <Fact field="operator_location" />.
        </p>
      </Section>
    </LegalShell>
  );
}
