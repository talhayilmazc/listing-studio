/**
 * The profile form (feature/manual-profiles; backend pipeline/profile_values.py, api/profile_form.py).
 *
 * A profile's settings as plain values: made from scratch for a shop, copied from another profile, or edited.
 * On a profile that follows a reference listing an edited field is "your value" (a Refresh never overwrites
 * it); a profile made by hand has no listing and is never refreshed. The server checks every value against
 * Etsy's own limits and answers each refusal in one plain sentence; nothing here decides what Etsy takes.
 */

export interface FormOption { id: number; label: string; type?: string | null }

export interface FormOptions {
  shop_id: string;
  shop_name: string;
  reading: boolean;
  currency: string | null;
  shipping: FormOption[];
  returns: FormOption[];
  readiness: FormOption[];
  partners: FormOption[];
  partner_note: string | null;
  who_made: { value: string; label: string }[];
  when_made: { value: string; label: string }[];
}

export interface VariationRow { values: string[]; price: number | null; quantity: number | null; enabled: boolean }
export interface Variations { properties: string[]; rows: VariationRow[] }

export interface FormValues {
  taxonomy_id: number | null;
  who_made: string | null;
  when_made: string | null;
  is_supply: boolean | null;
  price: number | null;
  quantity: number | null;
  variations: Variations | null;
  shipping_profile_id: number | null;
  return_policy_id: number | null;
  readiness_state_id: number | null;
  production_partner_ids: number[];
  item_weight?: number | null;
  item_weight_unit?: string | null;
  item_length?: number | null;
  item_width?: number | null;
  item_height?: number | null;
  item_dimensions_unit?: string | null;
  materials: string[];
  attributes: Record<string, string[] | { values: string[]; scale?: string | null }>;
  description: string;
}

export interface Personalization {
  enabled: boolean;
  question_text?: string | null;
  instructions?: string | null;
  required?: boolean;
  max_allowed_characters?: number | null;
}

export interface ProfileValues {
  profile_id: string | null;
  connection_id: string;
  shop_name: string;
  name: string;
  manual: boolean;
  reading: boolean;
  values: Partial<FormValues>;
  own_fields: string[];
  listing_values: Partial<FormValues>;
  listing_style: string;
  title_prefix: string | null;
  default_tags: string[];
  personalization: Personalization | null;
  category_path: string[];
  asked: { field: string; label: string; source: string | null; reason: string; creatable?: boolean }[];
  notes: string[];
  partner_note: string | null;
}

export interface Category { id: number; path: string[] }
export interface CategoryProperty {
  id: number | null; name: string; kind: string; max: number; required: boolean;
  values: { name: string; scale?: string }[]; scales: string[];
}

export interface FormBody {
  name: string;
  values: Partial<FormValues>;
  listing_style: string;
  title_prefix: string;
  default_tags: string[];
  personalization: Personalization | null;
}

/** The groups of the form, in order. */
export const GROUPS = ["Basics", "Price & variations", "Shipping & policies", "Details", "Text"] as const;

export const STYLE_LABELS: Record<string, string> = { search: "Short, clear title", classic: "Long keyword title" };

/** A new profile's starting values: what most made-to-order sellers choose; every one can be changed. */
export function emptyValues(): FormValues {
  return {
    taxonomy_id: null, who_made: "i_did", when_made: "made_to_order", is_supply: false, price: null, quantity: 999,
    variations: null, shipping_profile_id: null, return_policy_id: null, readiness_state_id: null,
    production_partner_ids: [], materials: [], attributes: {}, description: "",
  };
}

/** With the shop's own lists: a setting the shop has exactly one of is chosen (it can be changed). */
export function withDefaults(values: FormValues, options: FormOptions): FormValues {
  const only = (list: FormOption[], current: number | null) => (current === null && list.length === 1 ? list[0].id : current);
  return {
    ...values,
    shipping_profile_id: only(options.shipping, values.shipping_profile_id),
    return_policy_id: only(options.returns, values.return_policy_id),
    readiness_state_id: only(options.readiness, values.readiness_state_id),
  };
}

export const list = (text: string): string[] => text.split(",").map((s) => s.trim()).filter(Boolean);

const num = (text: string): number | null => {
  const t = text.trim().replace(",", ".");
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
};
export const toNumber = num;

/**
 * The variation table from each variation's name and its options ("Size": "S, M, L"; "Colour": "Black, White"):
 * one row for each combination, in the order typed. Rows that were there keep their price, quantity and switch;
 * a new row starts with the profile's own price and quantity.
 */
export function buildVariations(
  names: string[], options: string[][], before: Variations | null, price: number | null, quantity: number | null,
): Variations | null {
  const used = names.map((n, i) => ({ name: n.trim(), options: options[i] ?? [] })).filter((p) => p.name || p.options.length);
  if (!used.length) return null;
  let combos: string[][] = [[]];
  for (const p of used) combos = combos.flatMap((c) => (p.options.length ? p.options : [""]).map((o) => [...c, o]));
  const key = (v: string[]) => v.map((x) => x.toLowerCase()).join("\u0000");
  const old = new Map((before?.rows ?? []).map((r) => [key(r.values), r]));
  return {
    properties: used.map((p) => p.name),
    rows: combos.filter((c) => c.some(Boolean)).map((values) => old.get(key(values)) ?? { values, price, quantity, enabled: true }),
  };
}

/** Each variation's options, in the order its rows first show them (to fill the option boxes from a table). */
export function optionsOf(table: Variations | null): string[][] {
  if (!table) return [];
  return table.properties.map((_, i) => [...new Set(table.rows.map((r) => r.values[i] ?? "").filter(Boolean))]);
}

/** Whether a field is the seller's own value on a profile that follows a listing. */
export const isOwn = (field: string, own: string[], manual: boolean): boolean => !manual && own.includes(field);

/** What a refused save says: the message and each field's sentence (the server's own words). */
export function refusal(error: unknown): { message: string; fields: Record<string, string> } {
  const text = error instanceof Error ? error.message : String(error);
  try {
    const parsed = JSON.parse(text) as { message?: string; fields?: Record<string, string> };
    if (parsed && typeof parsed === "object" && parsed.fields) return { message: parsed.message ?? "Not saved.", fields: parsed.fields };
  } catch {
    // a plain sentence
  }
  return { message: text, fields: {} };
}

/** The group a field's refusal is shown under (the first such group is opened). */
export const FIELD_GROUP: Record<string, (typeof GROUPS)[number]> = {
  name: "Basics", taxonomy_id: "Basics", who_made: "Basics", when_made: "Basics", is_supply: "Basics", listing_style: "Basics",
  title_prefix: "Basics", price: "Price & variations", quantity: "Price & variations", variations: "Price & variations",
  shipping_profile_id: "Shipping & policies", return_policy_id: "Shipping & policies", readiness_state_id: "Shipping & policies",
  production_partner_ids: "Shipping & policies", item_weight: "Shipping & policies", item_length: "Shipping & policies",
  materials: "Details", attributes: "Details", personalization: "Details", description: "Text", default_tags: "Text",
};

/** How a listing's own value reads beside "your value" (one short line). */
export function shown(field: string, value: unknown, options?: FormOptions | null): string {
  if (value === null || value === undefined || value === "") return "none";
  if (field === "price" && typeof value === "number") return `${money(value, options?.currency)}${options?.currency ? ` ${options.currency}` : ""}`;
  const named = (items: FormOption[] | undefined, id: unknown) => items?.find((o) => o.id === Number(id))?.label ?? `#${id}`;
  if (field === "shipping_profile_id") return named(options?.shipping, value);
  if (field === "return_policy_id") return named(options?.returns, value);
  if (field === "readiness_state_id") return named(options?.readiness, value);
  if (field === "production_partner_ids") return (value as number[]).map((id) => named(options?.partners, id)).join(", ") || "none";
  if (field === "variations") {
    const table = value as Variations;
    return `${table.properties.join(" × ")}: ${table.rows.length} option${table.rows.length === 1 ? "" : "s"}`;
  }
  if (field === "attributes") return Object.keys(value as object).join(", ") || "none";
  if (Array.isArray(value)) return value.join(", ") || "none";
  if (typeof value === "boolean") return value ? "A supply or tool to make things" : "A finished product";
  const text = String(value);
  return text.length > 80 ? `${text.slice(0, 77)}…` : text;
}

/** "For Juniper Print Co." : a name that already ends a sentence is not given a second full stop. */
export const sentence = (text: string): string => (/[.!?]$/.test(text.trim()) ? text.trim() : `${text.trim()}.`);

/** How many decimals the shop's currency is written with (2 for most; 0 for yen). */
export function decimals(currency?: string | null): number {
  if (!currency) return 2;
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).resolvedOptions().maximumFractionDigits ?? 2;
  } catch {
    return 2;
  }
}

/** A price as the form shows it: 24.50 (the currency's own decimals); nothing for none. */
export function money(value: number | null | undefined, currency?: string | null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  return value.toFixed(decimals(currency));
}

/** A typed price as it is kept: rounded to the currency's decimals. */
export function rounded(value: number | null, currency?: string | null): number | null {
  if (value === null) return null;
  const f = 10 ** decimals(currency);
  return Math.round(value * f) / f;
}

/** How long the form waits for a category's attributes before it says it could not read them. */
export const ATTRIBUTES_WAIT_MS = 15_000;
export const ATTRIBUTES_FAILED = "Couldn't read this category's attributes.";

/**
 * What Etsy needs on every draft (its API reference, createDraftListing: quantity, price, who_made, when_made and
 * taxonomy_id are required, a shipping profile "when listing type is physical", a return policy "for active
 * physical listings", and a processing profile on every offering of the inventory), plus the profile's own name
 * and description. Returned as the fields' labels, in the form's order, for the one line under the button.
 *
 * A profile that follows a reference listing takes these from the listing: only a value the seller emptied
 * (the listing has one) is missing there.
 */
export const REQUIRED: [string, string][] = [
  ["name", "Profile name"], ["taxonomy_id", "Category"], ["who_made", "Who made it"], ["when_made", "When it was made"],
  ["price", "Price"], ["quantity", "Quantity"], ["shipping_profile_id", "Shipping profile"],
  ["return_policy_id", "Return policy"], ["readiness_state_id", "Processing profile"], ["description", "Description"],
];

const empty = (v: unknown): boolean => v === null || v === undefined || (typeof v === "string" && !v.trim());

export function missing(
  name: string, values: Partial<FormValues>, opts: { manual: boolean; listing?: Partial<FormValues> | null },
): string[] {
  const out: string[] = [];
  for (const [field, label] of REQUIRED) {
    if (field === "name") {
      if (!name.trim()) out.push(label);
      continue;
    }
    const value = (values as Record<string, unknown>)[field];
    let gone = empty(value);
    if (field === "price" && gone) {
      const rows = values.variations?.rows ?? [];
      gone = !(rows.length > 0 && rows.every((r) => r.price !== null && r.price !== undefined));
    }
    if (!gone) continue;
    if (!opts.manual) {
      // It follows a listing: nothing is asked for that the listing itself does not have.
      const theirs = (opts.listing as Record<string, unknown> | null | undefined)?.[field];
      if (empty(theirs)) continue;
    }
    out.push(label);
  }
  return out;
}

/** The one line beside a button that cannot be pressed yet. */
export const missingLine = (labels: string[]): string => `Still needed: ${labels.join(", ")}.`;
