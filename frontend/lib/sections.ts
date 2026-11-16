/**
 * The review card's Section control, in words and values (components/ItemOptions.tsx; hotfix/review-regressions).
 * A section is saved by its own id; "No section" is the seller's choice only when they pick it ("none"); a
 * value that is neither is not saved at all (it was sent as null and stored as "no section, as you chose").
 */
import type { ShopSection } from "./types";

/** Said on the card when a section could not be saved; the pick is kept for "Try again". */
export const SECTION_NOT_SAVED = "Couldn't save the section. Try again.";

const SECTION_SOURCE: Record<ShopSection["source"], string> = {
  seller: "your choice",
  carried: "",
  profile: "the profile's in this shop",
  suggested: "suggested",
  none: "",
};

/** Whose choice the section is, and which: "your choice · Western Collection"; "no section, as you chose"
 * only when the seller's own choice is no section (the server says so). */
export function sectionLabel(shop: Pick<ShopSection, "source" | "reason" | "selected_title">): string {
  const which = shop.source === "seller" && shop.selected_title ? shop.selected_title : shop.reason;
  return [SECTION_SOURCE[shop.source], which].filter(Boolean).join(" · ");
}

/** What the select's value means: a section of this shop (its id), "none" (the seller picked "No section"),
 * or undefined: not a value of this list, so nothing is saved. Never "no section" by accident. */
export function pickOf(value: string, sections: { id: number }[]): number | "none" | undefined {
  if (value === "none") return "none";
  return sections.find((s) => String(s.id) === value)?.id;
}
