// Light and dark variants of one design (backend pipeline/variants.py): what the upload page
// says before any mockup is made, and the template photo's colour chip. Pure; the server
// decides which file goes on which photo (POST /batches/{id}/mockups/plan) and this only words it.

import type { MockupPlan } from "./types";

export type Colour = "light" | "dark";
export type Meaning = "shirt" | "ink";

export const COLOUR_LABEL: Record<Colour, string> = { light: "Light shirt", dark: "Dark shirt" };

/** The upload page's setting: what a suffix such as "-white" names. */
export const MEANING_LABEL: Record<Meaning, string> = {
  shirt: "The shirt colour: a design named “-white” goes on light shirts",
  ink: "The ink colour: a design named “-white” has white ink and goes on dark shirts",
};

export const other = (c: Colour): Colour => (c === "light" ? "dark" : "light");

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

type PlanDesign = MockupPlan["designs"][number];
type PlanPhoto = MockupPlan["photos"][number];

/** The photos' colour group, by id ("unknown" when not measured). */
function colourOf(photos: PlanPhoto[], id: string): Colour | "unknown" {
  return photos.find((p) => p.id === id)?.colour ?? "unknown";
}

/** One line per file: "CDM290-white.png → 6 light-shirt photos". */
export function fileLines(design: PlanDesign, photos: PlanPhoto[]): string[] {
  return design.files.map((f) => {
    const colours = Array.from(new Set(f.photos.map((id) => colourOf(photos, id))));
    const kind = colours.length === 1 && colours[0] !== "unknown" ? `${colours[0]}-shirt ` : "";
    return `${f.filename} → ${plural(f.photos.length, `${kind}photo`)}`;
  });
}

/** Why some photos get no mockup of this design, in plain words, or null when every photo gets one. */
export function missingLine(design: PlanDesign, photos: PlanPhoto[], meaning: Meaning): string | null {
  if (!design.missing.length) return null;
  const names = design.missing.map((id) => photos.find((p) => p.id === id)?.name ?? "").filter(Boolean);
  const colours = design.missing_colours.filter((c): c is Colour => c === "light" || c === "dark");
  const where = colours.length === 1 ? `${colours[0]}-shirt ` : "";
  // The suffix such a photo would need: the same colour for shirts, the other for ink.
  const need = colours.length === 1 ? (meaning === "shirt" ? colours[0] : other(colours[0])) : null;
  const which = need ? `There is no ${need === "light" ? "light (-white)" : "dark (-dark)"} version of this design, so ` : "";
  return `${which}${plural(design.missing.length, `${where}photo`)} get no mockup of it: ${names.join(", ")}.`;
}

/** The one-click fix: use the variant there is on the other group too. */
export function fillLabel(design: PlanDesign): string | null {
  if (!design.missing.length || design.fill_other) return null;
  const file = design.files[0]?.filename;
  const colours = design.missing_colours.filter((c): c is Colour => c === "light" || c === "dark");
  if (!file) return null;
  return colours.length === 1 ? `Use ${file} on ${colours[0]} shirts too` : `Use ${file} on the other photos too`;
}

/** Mockups a design with variants makes (one per photo it goes on). */
export const variantMockups = (design: PlanDesign) => design.files.reduce((n, f) => n + f.photos.length, 0);

/** The template photo's chip: its colour group and whether the seller should look. */
export function colourChip(photo: { colour?: Colour | null; colour_source?: string | null }): { text: string; ask: boolean } {
  if (!photo.colour) return { text: "Light or dark?", ask: true };
  if (photo.colour_source === "uncertain") return { text: `${COLOUR_LABEL[photo.colour]}? Check`, ask: true };
  return { text: COLOUR_LABEL[photo.colour], ask: false };
}
