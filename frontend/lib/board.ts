/**
 * The grouping board's selection and grouping, kept apart from the page so they
 * can be tested. Photos are selected like files in a file manager: a click picks
 * one, Ctrl/Cmd-click adds or removes one, Shift-click picks the run from the
 * last one clicked. A tap (no keyboard) adds or removes, so a phone can pick
 * several without modifier keys.
 */
import type { Asset } from "./types";

// As in lib/grouping.ts (kept import-free so the tests run without a bundler).
const UNSORTED_KEY = "~unsorted";
const isUnsorted = (key: string | null | undefined): boolean => key === UNSORTED_KEY;
const groupLabel = (key: string): string => (isUnsorted(key) ? "Unsorted" : key || "Root folder");

export interface Selection {
  ids: string[];
  /** Where a Shift-click run starts. */
  anchor: string | null;
}

export const EMPTY: Selection = { ids: [], anchor: null };

export interface ClickMods {
  shift?: boolean;
  /** Ctrl or Cmd. */
  toggle?: boolean;
  /** A touch: no modifier keys, so a tap adds or removes. */
  touch?: boolean;
}

/** The selection after clicking ``id``. ``order`` is every photo as shown, in order. */
export function clickSelect(sel: Selection, id: string, mods: ClickMods, order: string[]): Selection {
  if (mods.shift && sel.anchor && order.includes(sel.anchor)) {
    const a = order.indexOf(sel.anchor);
    const b = order.indexOf(id);
    const run = order.slice(Math.min(a, b), Math.max(a, b) + 1);
    return { ids: [...new Set([...sel.ids, ...run])], anchor: sel.anchor };
  }
  if (mods.toggle || mods.touch) {
    const has = sel.ids.includes(id);
    return { ids: has ? sel.ids.filter((x) => x !== id) : [...sel.ids, id], anchor: id };
  }
  return sel.ids.length === 1 && sel.ids[0] === id ? EMPTY : { ids: [id], anchor: id };
}

/** What a drag carries: the whole selection when it starts on a selected photo, else that photo. */
export function dragged(sel: Selection, id: string): string[] {
  return sel.ids.includes(id) ? sel.ids : [id];
}

export interface BoardGroup {
  key: string;
  label: string;
  sku: string | null;
  assets: Asset[];
  /** A title, tags and description are written for it. */
  written: boolean;
}

/** The board's groups: Unsorted first (it is where work is left), then by name. */
export function boardGroups(assets: Asset[], writtenOn: Set<string>): { tray: BoardGroup; groups: BoardGroup[] } {
  const by = new Map<string, Asset[]>();
  for (const a of assets) {
    const key = a.group_key ?? "";
    (by.get(key) ?? by.set(key, []).get(key)!).push(a);
  }
  const make = (key: string, list: Asset[]): BoardGroup => {
    const sorted = [...list].sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0) || a.original_filename.localeCompare(b.original_filename));
    return {
      key,
      label: groupLabel(key),
      sku: isUnsorted(key) ? null : sorted.find((a) => a.parsed_sku)?.parsed_sku ?? null,
      assets: sorted,
      written: sorted.some((a) => writtenOn.has(a.id)),
    };
  };
  const tray = make(UNSORTED_KEY, by.get(UNSORTED_KEY) ?? []);
  const groups = [...by.entries()]
    .filter(([key]) => !isUnsorted(key))
    .map(([key, list]) => make(key, list))
    .sort((a, b) => a.label.localeCompare(b.label));
  return { tray, groups };
}

/** Every photo in the order the board shows them (for Shift-click runs). */
export function boardOrder(tray: BoardGroup, groups: BoardGroup[]): string[] {
  return [tray, ...groups].flatMap((g) => g.assets.map((a) => a.id));
}

/** Why a move can't happen, said before it is sent: a written group keeps a photo. */
export function moveProblem(ids: string[], to: string, groups: BoardGroup[]): string | null {
  const moving = new Set(ids);
  for (const g of groups) {
    if (g.key === to || !g.written) continue;
    if (g.assets.length > 0 && g.assets.every((a) => moving.has(a.id)))
      return `${g.label} has a listing written for it, so at least one photo has to stay in it. Merge it into another group instead, or delete its photos to remove it.`;
  }
  return null;
}

/** The groups whose cover a move would change (their listing was written from the old cover). */
export function coversChanged(ids: string[], to: string, groups: BoardGroup[]): string[] {
  const moving = new Set(ids);
  return groups
    .filter((g) => g.written && g.key !== to && g.assets[0] && moving.has(g.assets[0].id) && g.assets.some((a) => !moving.has(a.id)))
    .map((g) => g.label);
}
