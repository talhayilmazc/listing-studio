/**
 * Etsy's pages that were really read, and that a claim on the public site may rest on: their words are
 * in docs/guide-sources.md. In a file of its own because the free tools run in the browser, and must
 * not carry every guide with them.
 */
import type { Source } from "./content.ts";

const HELP_CENTER = "https://help.etsy.com/hc/en-us/articles";
export const READ = {
  createListing: { label: "Etsy Help: How to Create a Listing", url: `${HELP_CENTER}/115015628707-How-to-Create-a-Listing` },
  tags: { label: "Etsy Help: How to Use Tags to Get Found in Search", url: `${HELP_CENTER}/360000336307-How-to-Use-Tags-to-Get-Found-in-Search` },
  attributes: { label: "Etsy Help: How to Use Attributes When Listing an Item", url: `${HELP_CENTER}/115014502508-How-to-Use-Attributes-When-Listing-an-Item` },
  search: { label: "Etsy Help: How to Search for Items and Shops on Etsy", url: `${HELP_CENTER}/115015627947-How-to-Search-for-Items-and-Shops-on-Etsy` },
  imageHelp: { label: "Etsy Help: How Do Etsy's Listing Image Requirements Apply to My Listings?", url: `${HELP_CENTER}/44297885802647-How-Do-Etsy-s-Listing-Image-Requirements-Apply-to-My-Listings` },
  images: { label: "Etsy Help: Requirements and Best Practices for Images in Your Etsy Shop", url: `${HELP_CENTER}/115015663347-Requirements-and-Best-Practices-for-Images-in-Your-Etsy-Shop` },
  personalized: { label: "Etsy Help: How to Offer Personalized Listings", url: `${HELP_CENTER}/360000344528-How-to-Offer-Personalized-Listings` },
  partners: { label: "Etsy Help: Working with Production Partners on Etsy", url: `${HELP_CENTER}/360000336547-Working-with-Production-Partners-on-Etsy` },
  secondShop: { label: "Etsy Help: How to Open a Second Shop on Etsy", url: `${HELP_CENTER}/360017604474-How-to-Open-a-Second-Shop-on-Etsy` },
  ipNotice: { label: "Etsy Help: What to Do if You Receive a Notice of Intellectual Property Infringement", url: `${HELP_CENTER}/360034079714-What-to-Do-if-You-Receive-a-Notice-of-Intellectual-Property-Infringement` },
  ipReport: { label: "Etsy Help: How to Report Intellectual Property Infringement", url: `${HELP_CENTER}/360000344448-How-to-Report-Intellectual-Property-Infringement` },
  counter: { label: "Etsy Help: How to File a DMCA Counter Notice", url: `${HELP_CENTER}/360040496854-How-to-File-a-DMCA-Counter-Notice` },
  removed: { label: "Etsy Help: Why Was My Listing Removed by Etsy?", url: `${HELP_CENTER}/360061329494-Why-Was-My-Listing-Removed-by-Etsy` },
  processing: { label: "Etsy Help: How to Set Processing Times, Processing Profiles and \"Ship By\" Dates", url: `${HELP_CENTER}/115015588087-How-to-Set-Processing-Times-Processing-Profiles-and-Ship-By-Dates` },
  api: { label: "Etsy Open API v3 reference: createDraftListing", url: "https://developers.etsy.com/documentation/reference/#operation/createDraftListing" },
  sellerPolicy: { label: "Etsy: Seller Policy, section 1c item 7 (page last updated 5 October 2026)", url: "https://www.etsy.com/legal/sellers" },
  listingFees: { label: "Etsy Help: Fees and Listing Multiple Quantities", url: `${HELP_CENTER}/360000344908-What-Are-the-Fees-and-Taxes-for-Selling-on-Etsy` },
  feeBasics: { label: "Etsy Help: Etsy Fee Basics", url: `${HELP_CENTER}/360035902374` },
  feesAndTaxes: { label: "Etsy Help: What are the Fees and Taxes for Selling on Etsy?", url: `${HELP_CENTER}/115014483627-What-are-the-Fees-and-Taxes-for-Selling-on-Etsy-` },
  paymentFees: { label: "Etsy Help: What are Payment Processing Fees for Selling on Etsy?", url: `${HELP_CENTER}/115015628847-Fees-and-Payments` },
  offsiteAds: { label: "Etsy Help: How Etsy's Offsite Ads Work", url: `${HELP_CENTER}/360000338367` },
  feesPolicy: { label: "Etsy: Fees & Payments Policy (page last updated 5 October 2026)", url: "https://www.etsy.com/legal/fees" },
  etsyPayments: { label: "Etsy: Etsy Payments Policy (page last updated 31 July 2026)", url: "https://www.etsy.com/legal/etsy-payments" },
  regulatoryFee: { label: "Etsy Help: What is a Regulatory Operating Fee?", url: "https://help.etsy.com/hc/articles/1500011073202" },
} satisfies Record<string, Source>;
