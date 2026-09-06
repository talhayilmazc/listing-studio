/** Presentation helpers shared across screens. No API or state logic here. */

/** "2 hours ago". Pair with `title={new Date(iso).toLocaleString()}` for the exact date. */
export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return mins + (mins === 1 ? " minute ago" : " minutes ago");
  const hours = Math.round(mins / 60);
  if (hours < 24) return hours + (hours === 1 ? " hour ago" : " hours ago");
  const days = Math.round(hours / 24);
  if (days < 30) return days + (days === 1 ? " day ago" : " days ago");
  const months = Math.round(days / 30);
  return months + (months === 1 ? " month ago" : " months ago");
}

/**
 * The ToU back-link for one of the seller's own listings: a draft has no working
 * public URL, so drafts link to the Shop Manager editor and active listings to
 * their public page. Mirrors `listing_url` / `listing_edit_url` in the backend.
 */
export function etsyListingLink(listingId: number, state: string | null, url?: string | null) {
  if (state === "active") return url || `https://www.etsy.com/listing/${listingId}`;
  return `https://www.etsy.com/your/shops/me/listing-editor/edit/${listingId}`;
}
