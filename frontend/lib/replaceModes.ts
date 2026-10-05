/**
 * "Replace images" on a listing that is already on Etsy: the two ways to do it
 * and, for each, exactly what changes and what stays. One place, so the batch
 * page and the Profiles page say the same thing.
 */
export type ReplaceMode = "photos" | "full";

/** What the server does when no mode is sent, and what the form opens on. */
export const DEFAULT_REPLACE_MODE: ReplaceMode = "photos";

export interface ReplaceModeText {
  mode: ReplaceMode;
  label: string;
  changes: string[];
  keeps: string[];
  /** How it counts against "Listings generated". */
  counts: string;
}

/** `photos`: where the new photos come from and in what order. */
export function replaceModes(photos: string): ReplaceModeText[] {
  return [
    {
      mode: "photos",
      label: "Photos only (keep title and tags)",
      changes: [`The listing's photos: replaced with ${photos}.`],
      keeps: [
        "Title, tags and description, exactly as they are on Etsy.",
        "Category, price, variations, shipping and whether the listing is live.",
        "Size charts already on the listing, and any image that might be one: they stay, after the new photos.",
      ],
      counts: "Does not count as a listing generated. Uses Etsy requests only.",
    },
    {
      mode: "full",
      label: "Photos, title and tags",
      changes: [
        `The listing's photos: replaced with ${photos}.`,
        "Title and all 13 tags: written again from the new cover photo.",
        "The title at the top of the description, to match the new one.",
      ],
      keeps: [
        "The rest of the description.",
        "Category, price, variations, shipping and whether the listing is live.",
        "Size charts already on the listing: they stay, after the new photos.",
      ],
      counts: "Counts as 1 listing generated, and uses Etsy requests.",
    },
  ];
}

/** What to say when it is done. */
export function replacedNotice(mode: ReplaceMode, label: string): string {
  return mode === "photos"
    ? `Photos replaced on Etsy for ${label}. Its title, tags and description were not changed.`
    : `Photos, title and tags replaced on Etsy for ${label}.`;
}
