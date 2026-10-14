// The legal facts every page states, in one place. No client code here: the
// public site (static pages) and the app both import it.
//
// The backend has the same notice and disclaimer (app/api/schemas.py) and the
// same figures as its own settings; tests fail if a copy differs
// (backend/tests/test_legal_texts.py reads this file).

/** The notice Etsy's API terms ask every application to show. Never altered or shortened. */
export const TRADEMARK_NOTICE =
  "The term 'Etsy' is a trademark of Etsy, Inc. This application uses Etsy's API, but is not endorsed or certified by Etsy.";

/** Who provides the application, in the words Etsy's API terms ask for. */
export const PROVIDER_DISCLAIMER =
  "THIS APPLICATION IS SOLELY PROVIDED BY TALHA YILMAZ AND AHMET DÜZGÜN, OPERATING AS LISTYRO.";

/**
 * The version of the Terms of Service and Privacy Policy accounts agree to: the
 * date of the text. Changing either text in a way sellers must agree to means
 * changing this and TERMS_VERSION in backend/app/core/terms.py together; every
 * account is then asked again.
 */
export const TERMS_VERSION = "2026-10-14";
/** The same date as the pages show it. */
export const LAST_UPDATED = "14 October 2026";

/** The sentence beside the tick at signup and on the one-time acceptance screen. */
export const CONSENT_STATEMENT = "I agree to the Terms of Service and Privacy Policy";

/**
 * How long each kind of data is kept. Each figure is the code's own setting
 * (the test names the setting beside each one); the Privacy Policy prints them
 * from here, never from a number typed into its text.
 */
export const RETENTION = {
  /** shop_listing_cache: ShopListingCache.STALE_SECONDS */
  listingCacheHours: 6,
  /** A profile's reference images (links): ListingProfile.DISPLAY_MAX_AGE_SECONDS */
  referenceImagesHours: 6,
  /** A profile's reference details and a shop's section list: ListingProfile.CACHE_MAX_AGE_SECONDS, SECTIONS_MAX_AGE */
  otherEtsyContentHours: 24,
  /** Uploaded image files once a draft exists in every target shop: DRAFTED_RETENTION_DAYS */
  uploadsAfterDraftsDays: 3,
  /** Uploaded image files otherwise, after the last work on them: UNPUBLISHED_RETENTION_DAYS */
  uploadsOtherwiseDays: 30,
  /** Analytics history, the default (an operator setting; /api/meta has the figure in force): ANALYTICS_MONTHS */
  analyticsMonths: 24,
  /** Copies of listings taken before the app changes them: ListingSnapshot.RETENTION_DAYS */
  listingSnapshotDays: 90,
  /** An invite request, decided or not: InviteRequest.*_RETENTION_DAYS */
  inviteRequestDays: 90,
  /** Database backups: RETENTION_DAYS in deploy/backup.sh */
  backupDays: 14,
  /** The sign-in session: SESSION_TTL_DAYS */
  sessionDays: 30,
  /** Failed sign-in and request-rate counters: AUTH_RATE_LIMIT_WINDOW_SECONDS */
  counterMinutes: 15,
  /** The longest account deletion may take: workers/accounts.py DEADLINE_DAYS */
  accountDeletionDays: 30,
  /** Our own records of AI calls (model, tokens, cost): AiCall.RETENTION_DAYS */
  aiCallMonths: 25,
  /** How often expired data is looked for: the purge_expired cron */
  cleanupMinutes: 15,
} as const;

/** Why each Etsy permission is asked for: the feature that uses it. */
export const SCOPE_REASONS: { scope: string; reason: string }[] = [
  { scope: "listings_r", reason: "Read your own listings: to choose a reference listing for a profile, show your shop's listings, and read the views and favourites of listings made with Listyro." },
  { scope: "listings_w", reason: "Create draft listings from your designs, upload their images, set their variations and SKUs, and publish a draft when you approve it." },
  { scope: "shops_r", reason: "Read your shop's name, sections, shipping profiles, return policies and production partners, so drafts use your shop's own settings." },
  { scope: "shops_w", reason: "Create a shop section, shipping profile or return policy in your shop, only when you confirm it (to use a profile in another of your shops)." },
  { scope: "transactions_r", reason: "Read your own shop's sales and its payment account ledger for your profit, cost and ads reporting. Nothing about buyers is stored." },
];
