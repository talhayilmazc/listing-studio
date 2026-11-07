/**
 * The site's primary call to action, from one switch. While billing is off (early access) every
 * primary button asks for an invite; when billing starts, set NEXT_PUBLIC_BILLING=on at build time
 * and the same buttons say "Start free" and lead to sign-up. Nothing else on the site types the label.
 */

export const BILLING_ON = process.env.NEXT_PUBLIC_BILLING === "on";

export const PRIMARY_CTA = BILLING_ON
  ? { label: "Start free", href: "/register" }
  : { label: "Request an invite", href: "/request-invite" };
