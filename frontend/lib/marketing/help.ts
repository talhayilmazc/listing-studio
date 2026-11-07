/** The help center and the Free tools page's words. Each answer is what the product does today. */

export const HELP = {
  title: "Help center: getting started with Listyro | Listyro",
  description: "Answers about connecting a shop, profiles, drafts, scheduling, mockups, your data and leaving Listyro.",
  h1: "Help center",
  lead: "Short answers to what sellers ask most. Something else? Use the [contact page](/contact).",
  groups: [
    {
      title: "Getting started",
      items: [
        { q: "What do I need to start?", a: "An invite, an Etsy shop you own, and at least one listing set up the way you like it. That listing becomes your first profile." },
        { q: "How do I connect a shop?", a: "Open Shops & groups and press Connect a shop. You sign in on Etsy's own page and choose what Listyro may do. We never see your Etsy password." },
        { q: "What is a profile?", a: "One of your own listings, used as the model for new ones. New drafts copy its price, variations, shipping and the rest. See [Profiles](/features/profiles)." },
        { q: "How should I name my files?", a: "Put the SKU in the file name, like BR5229-front.png, or use one folder per listing. You choose how photos become listings before anything is uploaded, and you can move them afterwards." },
      ],
    },
    {
      title: "Drafts and publishing",
      items: [
        { q: "Will anything go live without me?", a: "No. Every listing is created as a draft. A draft is published only when you press Publish, or at the time you scheduled for a listing you approved." },
        { q: "Why was my listing not approved?", a: "Only what [Etsy would refuse](https://developers.etsy.com/documentation/reference/#operation/createDraftListing) stops a listing, along with the trademark filter if it is on. The card says what to change. Other points appear as notes and do not stop anything." },
        { q: "A draft is half made. What now?", a: "Open the review page or Overview. Unfinished drafts are listed with the step that failed and Etsy's reason. Finish draft carries on with the same listing; it never makes a second one." },
        { q: "Does Listyro delete anything in my shop?", a: "No. Listyro never deletes anything in your shop. If it finds a draft that has been replaced, it lists it for you to delete on Etsy." },
        { q: "Will my listings rank higher?", a: "Nobody outside Etsy can promise that, and we do not. The text follows Etsy's own published guidance, and the search-readiness checklist says which of it a listing meets." },
      ],
    },
    {
      title: "Trademarks",
      items: [
        { q: "What does the trademark warning mean?", a: "The text names a well-known brand or character. [Etsy's help](https://help.etsy.com/hc/en-us/articles/360000344448-How-to-Report-Intellectual-Property-Infringement) says that if an owner's report complies with its policies, it will remove the reported content. You decide what to do. See [the trademark warning](/features/ai-listing-writer#trademark-warning)." },
        { q: "Can I turn the trademark filter off?", a: "Yes, in Settings, after accepting the risk. Listings naming a brand then get a warning instead of being stopped. You remain responsible for what you list." },
      ],
    },
    {
      title: "Your data",
      items: [
        { q: "Do you keep anything about my buyers?", a: "No. Buyer names, addresses, emails and messages are never stored. For your own profit report we keep order numbers, listing ids, amounts and dates." },
        { q: "What happens to my designs?", a: "Uploads are working copies and are deleted on the schedule in the [Privacy Policy](/privacy). To write a listing's text, the design image is sent to Anthropic; under its commercial terms it is not used to train models." },
        { q: "How do I stop using Listyro?", a: "Disconnect a shop and its tokens and Etsy data are deleted at once. Delete your account in Settings and everything in it is removed, within the time the [Privacy Policy](/privacy) states." },
      ],
    },
    {
      title: "Plans",
      items: [
        { q: "What does it cost?", a: "Nothing during early access: no payment of any kind is taken. The launch prices are on the [pricing page](/pricing)." },
        { q: "What counts as a listing?", a: "In the launch plans, a listing is one design written by AI, with all its photos. Rewriting the same design is free up to 3 times. Listings you write yourself, photos, and sending a listing to more shops do not count. See the [pricing page](/pricing)." },
        { q: "Who makes Listyro?", a: "No. Listyro is an independent product that works through Etsy's API. It is not made, endorsed or certified by Etsy." },
      ],
    },
  ],
};

export const FREE_TOOLS = {
  title: "Free tools for sellers: title checker, fee calculator | Listyro",
  description: "Free tools for Etsy sellers, with no account needed: a title checker, a tag checker and a fee calculator.",
  h1: "Free tools for sellers",
  lead: "Small tools that need no account. The checkers and the calculator run in your browser, and what you type in them stays there.",
};
