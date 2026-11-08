/**
 * The site's primary call to action, from one switch. The label is the owner's ("Start free", site v3);
 * where it leads follows billing: while billing is off (invite-only early access) it opens the invite form,
 * and when billing starts (NEXT_PUBLIC_BILLING=on at build time) it opens sign-up. Nothing else on the site
 * types the label or the address.
 */

export const BILLING_ON = process.env.NEXT_PUBLIC_BILLING === "on";

export const START_HREF = BILLING_ON ? "/register" : "/request-invite";

export const PRIMARY_CTA = { label: "Start free", href: START_HREF };
