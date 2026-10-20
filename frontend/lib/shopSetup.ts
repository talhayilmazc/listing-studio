// Setting a profile up in other shops: what each shop will use for its shipping
// profile, return policy, processing profile and production partners, and what
// the seller's one confirm sends. Pure (components/ShopSetupDialog.tsx draws it;
// lib/shopSetup.test.ts checks it).
//
// For each setting the server says what the shop uses now and how it was chosen (the
// account's map, the shop's own setting with the same name or the same terms, or a copy
// made when the seller set the profile up there). The dialog asks only what needs the
// seller (a copy Etsy refused, two settings that match); every choice can be changed to
// another of the shop's own settings, and the choice is the account's for that setting.

import type { SettingUse, SetupChoice, SetupFailure, ShopSetup } from "./types";

/** A choice in the dialog: "copy", "pick:<id>[,<id>]", or "" (still to choose). */
export type Choice = string;

export const COPY = "copy";

export const pickValue = (ids: number[]): Choice => `pick:${ids.join(",")}`;

export function idsOf(choice: Choice): number[] {
  return choice.startsWith("pick:") ? choice.slice(5).split(",").filter(Boolean).map(Number) : [];
}

export const choiceKey = (shopId: string, resource: string) => `${shopId}:${resource}`;

/** What the dialog preselects: the matched (or earlier chosen) setting, a copy, or nothing.
 * A copy Etsy just refused is never preselected again: the same request would be refused. */
export function defaultChoice(s: SettingUse): Choice {
  if (s.default === "keep" && s.linked_ids.length > 0) return pickValue(s.linked_ids);
  if (s.default === "copy" && s.creatable && !s.failure) return COPY;
  return "";
}

/** What the seller can do about a refused copy, in order: the fix first, then Refresh
 * (re-read the shop's lists, so a setting made by hand in Shop Manager can be picked). */
export type FailureAction =
  | { kind: "reconnect"; href: string; label: string }
  | { kind: "refresh"; label: string };

export function failureActions(f: SetupFailure, shopName: string): FailureAction[] {
  const refresh: FailureAction = { kind: "refresh", label: `Refresh ${shopName}'s settings` };
  if (f.action === "reconnect") return [{ kind: "reconnect", href: "/connect", label: `Reconnect ${shopName}` }, refresh];
  return [refresh];
}

/** Any refused copy among these shops: the dialog stays open on the choices, not "done". */
export function anyFailure(shops: ShopSetup[]): boolean {
  return shops.some((s) => s.settings.some((x) => Boolean(x.failure)));
}

/** Every shop asked for is set up: the dialog has nothing to ask and closes by itself.
 * (A setting a shop lacks is copied there when the seller sets the profile up; the
 * dialog is for what Etsy refused, what matches twice, or what cannot be copied.) */
export function nothingToAsk(shops: ShopSetup[], wanted: number): boolean {
  return shops.length >= wanted && shops.length > 0 && shops.every((s) => s.link.ready && s.link.status !== "checking");
}

/** Fired when a profile's setup in a shop changed: every profile card on the page reloads,
 * since one setting is shared by every reference listing that uses it (the account's map). */
export const PROFILES_CHANGED = "listyro:profiles-changed";

export function announceProfilesChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(PROFILES_CHANGED));
}

/** The Shops page line for one shop's reference listings, and whether to offer the button. */
export function shopProfilesLine(s: { total: number; set_up: number; checking: number; not_set_up: number;
  waiting: unknown[] }): { text: string; offer: boolean } {
  if (s.total === 0) return { text: "", offer: false };
  if (s.checking > 0) return { text: `Setting up ${s.checking} of ${s.total} reference listings here…`, offer: false };
  const left = s.not_set_up + s.waiting.length;
  if (left === 0) return { text: `All ${s.total} reference listing${s.total === 1 ? " is" : "s are"} set up here.`, offer: false };
  return {
    text: `${s.set_up} of ${s.total} reference listing${s.total === 1 ? "" : "s"} set up here.`
      + (s.waiting.length ? ` ${s.waiting.length} need${s.waiting.length === 1 ? "s" : ""} you.` : ""),
    offer: s.not_set_up > 0,
  };
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** The options of one setting's dropdown, in order: what is used now, the shop's own, a copy. */
export function choiceOptions(s: SettingUse): { value: Choice; label: string }[] {
  const out: { value: Choice; label: string }[] = [];
  const linked = s.linked_ids.length > 0 ? pickValue(s.linked_ids) : null;
  if (linked && !(s.linked_ids.length === 1 && s.options.some((o) => o.id === s.linked_ids[0]))) {
    out.push({ value: linked, label: `${s.linked_label ?? "Current"}${s.how ? ` (${s.how})` : ""}` });
  }
  for (const o of s.options) {
    const current = s.linked_ids.length === 1 && s.linked_ids[0] === o.id;
    out.push({ value: pickValue([o.id]), label: current && s.how ? `${o.label} (${s.how})` : o.label });
  }
  if (s.creatable) {
    out.push({
      value: COPY,
      // The card names the shop; the line under the dropdown says it again in full.
      label: `Create a copy of ${s.source_label ?? `the main shop's ${s.label.toLowerCase()}`} (${plural(s.requests, "request")})`,
    });
  }
  return out;
}

/** One sentence under the dropdown: what this shop will use. */
export function willUse(s: SettingUse, choice: Choice, shopName: string): string {
  if (choice === COPY) {
    return `A copy of ${s.source_label ?? "the main shop's"} is created in ${shopName} when you confirm (${plural(s.requests, "Etsy request")}).`;
  }
  const ids = idsOf(choice);
  if (ids.length === 0) {
    return s.creatable
      ? `Choose one of ${shopName}'s, or create a copy.`
      : `Choose one of ${shopName}'s${s.reason ? ` (${s.reason})` : ""}.`;
  }
  const same = ids.length === s.linked_ids.length && ids.every((id) => s.linked_ids.includes(id));
  if (same) return `Uses ${s.linked_label ?? "its own"}${s.how ? ` (${s.how})` : ""}.`;
  const labels = ids.map((id) => s.options.find((o) => o.id === id)?.label ?? `#${id}`);
  return `Uses ${labels.join(", ")} (your choice).`;
}

/** What the seller's confirm sends for one shop: only what changes (a different pick, or a copy). */
export function shopChoices(shop: ShopSetup, choices: Record<string, Choice>): SetupChoice[] {
  const out: SetupChoice[] = [];
  for (const s of shop.settings) {
    const choice = choices[choiceKey(shop.connection_id, s.resource)] ?? defaultChoice(s);
    if (choice === COPY) {
      out.push({ connection_id: shop.connection_id, resource: s.resource, use: "copy", ids: [] });
      continue;
    }
    const ids = idsOf(choice);
    if (ids.length === 0) continue;
    const same = ids.length === s.linked_ids.length && ids.every((id) => s.linked_ids.includes(id));
    if (!same) out.push({ connection_id: shop.connection_id, resource: s.resource, use: "pick", ids });
  }
  return out;
}

export interface SetupTotals {
  /** Copies to create, and their Etsy requests. */
  copies: number;
  requests: number;
  /** Settings with nothing chosen: those shops stay unready until they are chosen. */
  unresolved: number;
  /** Shops where everything is settled once this is confirmed. */
  ready: number;
  shops: number;
}

export function totals(shops: ShopSetup[], choices: Record<string, Choice>): SetupTotals {
  let copies = 0, requests = 0, unresolved = 0, ready = 0;
  for (const shop of shops) {
    let open = 0;
    for (const s of shop.settings) {
      const choice = choices[choiceKey(shop.connection_id, s.resource)] ?? defaultChoice(s);
      if (choice === COPY) {
        copies += 1;
        requests += s.requests;
      } else if (idsOf(choice).length === 0) {
        open += 1;
      }
    }
    unresolved += open;
    if (open === 0) ready += 1;
  }
  return { copies, requests, unresolved, ready, shops: shops.length };
}

/** The confirm button's words. */
export function confirmLabel(t: SetupTotals): string {
  if (t.copies > 0) {
    return `Confirm: create ${t.copies === 1 ? "1 copy" : `${t.copies} copies`} and set up ${plural(t.shops, "shop")}`;
  }
  return `Confirm and set up ${plural(t.shops, "shop")}`;
}

/**
 * The shops "Use in" offers for a profile: every shop of the account except the
 * profile's main shop, each with where it stands. A shop the profile was set up in
 * before (unfinished, failed, or ready) is offered too: choosing it again finishes,
 * retries, or lets the seller change what it uses. (Only offering shops with no
 * setup at all hid every unfinished or failed shop, and disabled the choice once
 * every shop had been tried: bug 1.)
 */
export function useInShops(
  shops: { id: string; name: string }[],
  links: { connection_id: string; main: boolean; ready: boolean; status: string }[],
): { id: string; name: string; note: string | null }[] {
  const main = links.find((l) => l.main)?.connection_id;
  return shops
    .filter((s) => s.id !== main)
    .map((s) => {
      const link = links.find((l) => l.connection_id === s.id);
      const note = !link ? null
        : link.status === "checking" ? "being set up"
        : link.status === "error" ? "failed: try again"
        : link.ready ? "set up"
        : "needs a choice";
      return { id: s.id, name: s.name, note };
    });
}

/** Which of the asked shops still need starting (no link, or a failed one) or reading again. */
export function toStart(shops: ShopSetup[], wanted: string[]): { start: string[]; again: string[] } {
  const start: string[] = [];
  const again: string[] = [];
  for (const id of wanted) {
    const shop = shops.find((s) => s.connection_id === id);
    if (!shop || shop.link.status === "error") start.push(id);
    else if (shop.choices_expired && shop.link.status !== "checking") again.push(id);
  }
  return { start, again };
}
