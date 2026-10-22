/**
 * The Shops table on a profile (B7): one row per shop, a column per setting. Shipping,
 * returns and processing are each shop's own Etsy settings (the account's map: one choice
 * serves every profile using the same main-shop setting); section and title prefix are the
 * profile's in that shop; "a day" is the shop's daily limit for spread-out schedules.
 *
 * "Apply to all shops" and "Copy from shop…" go by name, never by id (an id is only valid in
 * its own shop): a shop with nothing of that name is skipped and said. Pure; the component
 * (components/ProfileShopTable.tsx) runs the actions this returns.
 */

export const COLUMNS = ["shipping_profile", "return_policy", "readiness_state", "section", "prefix", "daily"] as const;
export type Column = (typeof COLUMNS)[number];

export const COLUMN_LABELS: Record<Column, string> = {
  shipping_profile: "Shipping",
  return_policy: "Returns",
  readiness_state: "Processing",
  section: "Section",
  prefix: "Title prefix",
  daily: "A day",
};

export const SETTING_COLUMNS = ["shipping_profile", "return_policy", "readiness_state"] as const;

export interface Option {
  id: number;
  label: string;
}

/** One shop's row, as the component reads it from the two endpoints. */
export interface TableRow {
  shopId: string;
  shopName: string;
  main: boolean;
  /** Per setting column: the shop's choices and the one in use (main shop: the reference's own). */
  settings: Partial<Record<(typeof SETTING_COLUMNS)[number], { value: number | null; options: Option[] }>>;
  sectionTitle: string | null;
  /** The shop's section titles, or null while they are read. */
  sections: string[] | null;
  prefix: string | null;
  daily: number | null;
}

export type Action =
  | { kind: "pick"; shopId: string; resource: string; id: number }
  | { kind: "section"; shopId: string; title: string | null }
  | { kind: "prefix"; shopId: string; prefix: string | null }
  | { kind: "daily"; shopId: string; daily: number | null };

export interface Plan {
  actions: Action[];
  skipped: { shopName: string; why: string }[];
}

const norm = (s: string | null | undefined) => (s ?? "").replace(/\s+/g, " ").trim().toLowerCase();

/** The value a cell shows, as text (what "apply" and "copy" carry to another shop). */
export function cellValue(row: TableRow, column: Column): string | number | null {
  if (column === "section") return row.sectionTitle;
  if (column === "prefix") return row.prefix;
  if (column === "daily") return row.daily;
  const s = row.settings[column];
  if (!s || s.value === null) return null;
  return s.options.find((o) => o.id === s.value)?.label ?? null;
}

/** What setting one column's value in ``target`` takes: an action, a skip, or nothing to do. */
function one(column: Column, value: string | number | null, target: TableRow): Action | { why: string } | null {
  if (column === "daily") {
    return target.daily === value ? null : { kind: "daily", shopId: target.shopId, daily: (value as number | null) ?? null };
  }
  if (column === "prefix") {
    return norm(target.prefix) === norm(value as string) ? null : { kind: "prefix", shopId: target.shopId, prefix: (value as string | null) ?? null };
  }
  if (column === "section") {
    if (value === null) return target.sectionTitle === null ? null : { kind: "section", shopId: target.shopId, title: null };
    if (target.sections === null) return { why: "its sections are still being read" };
    const hit = target.sections.find((t) => norm(t) === norm(value as string));
    if (!hit) return { why: `no section named "${value}"` };
    return norm(target.sectionTitle) === norm(hit) ? null : { kind: "section", shopId: target.shopId, title: hit };
  }
  if (target.main) return null; // the main shop uses its reference listing's own settings
  const s = target.settings[column];
  if (value === null) return null;
  if (!s) return { why: "its settings are still being read" };
  const hit = s.options.find((o) => norm(o.label) === norm(value as string));
  if (!hit) return { why: `no ${COLUMN_LABELS[column].toLowerCase()} setting named "${value}"` };
  return hit.id === s.value ? null : { kind: "pick", shopId: target.shopId, resource: column, id: hit.id };
}

function plan(columns: readonly Column[], source: TableRow, targets: TableRow[]): Plan {
  const out: Plan = { actions: [], skipped: [] };
  for (const column of columns) {
    // The main shop's shipping, returns and processing are its reference listing's: nothing to carry.
    if (source.main && (SETTING_COLUMNS as readonly string[]).includes(column)) continue;
    const value = cellValue(source, column);
    for (const target of targets) {
      if (target.shopId === source.shopId) continue;
      const step = one(column, value, target);
      if (step === null) continue;
      if ("why" in step) out.skipped.push({ shopName: target.shopName, why: `${COLUMN_LABELS[column]}: ${step.why}` });
      else out.actions.push(step);
    }
  }
  return out;
}

/** "Apply to all shops": one column's value in ``source`` given to every other shop. */
export function applyToAll(column: Column, source: TableRow, rows: TableRow[]): Plan {
  return plan([column], source, rows);
}

/** "Copy from shop…": every column of ``from`` given to ``to``. */
export function copyFrom(from: TableRow, to: TableRow): Plan {
  return plan(COLUMNS, from, [to]);
}

/** One line after an apply or copy: "Set in 3 shops. Skipped Frost Mugs (Section: no section named "Mugs")." */
export function planResult(p: Plan): string {
  const shops = new Set(p.actions.map((a) => a.shopId)).size;
  const done = p.actions.length ? `Set in ${shops} shop${shops === 1 ? "" : "s"}.` : "Nothing to change.";
  const skipped = p.skipped.length ? ` Skipped ${p.skipped.map((s) => `${s.shopName} (${s.why})`).join("; ")}.` : "";
  return done + skipped;
}
