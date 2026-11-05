/**
 * A listing's attributes (backend pipeline/listing_attributes.py, api/listing_attributes.py): every one Etsy offers for
 * its category, with Etsy's values or units only. The server checks every value; these are what the editor shows.
 */

export type AttributeProperty = {
  name: string;
  kind: "choice" | "number";
  max_values: number;
  required: boolean;
  values: string[];
  value_units: Record<string, string>;
  units: string[];
  current: string[];
  unit: string | null;
  source: "seller" | "shop" | "writer" | "profile" | "none";
  source_words: string;
  suggested: string[];
};

export type Attributes = {
  content_id: string;
  shop_id: string | null;
  shop_name: string | null;
  properties: AttributeProperty[];
  dropped: { name: string; values: string[] }[];
  note: string | null;
  waiting: boolean;
};

export type AttributeValue = { values: string[]; unit?: string | null } | null;

export type AttributeBulk = {
  rows: { content_id: string; label: string; before: string[]; after: string[]; changed: boolean; reason: string | null; note: string | null }[];
  changed: number;
  skipped: number;
  applied: boolean;
  sentence: string;
};

/** How a value reads on the card: "Christmas, Easter", "12 Inches", or "Not set". */
export function shown(p: Pick<AttributeProperty, "current" | "unit">): string {
  if (!p.current.length) return "Not set";
  return p.current.join(", ") + (p.unit ? ` ${p.unit}` : "");
}

/** The values a choice offers under a unit (sizes in US or EU), all when it has no units. */
export function valuesFor(p: Pick<AttributeProperty, "values" | "value_units">, unit: string | null): string[] {
  if (!unit) return p.values;
  return p.values.filter((v) => !p.value_units[v] || p.value_units[v] === unit);
}

/** Adding one more value to a property that takes several, never past Etsy's limit. */
export function withValue(current: string[], value: string, max: number): string[] {
  if (current.includes(value)) return current;
  return max <= 1 ? [value] : current.length >= max ? current : [...current, value];
}

/** How many attributes have a value (said on the collapsed section). */
export const setCount = (props: AttributeProperty[]): number => props.filter((p) => p.current.length > 0).length;

/** A number Etsy would take: above 0. */
export const goodNumber = (text: string): boolean => /^\d+(\.\d+)?$/.test(text.trim()) && Number(text) > 0;

/** "set by you" beats "suggested": one is the seller's to take back, the other theirs to change. */
export const canFollow = (p: Pick<AttributeProperty, "source">, shop: boolean): boolean => (shop ? p.source === "shop" : p.source === "seller");
