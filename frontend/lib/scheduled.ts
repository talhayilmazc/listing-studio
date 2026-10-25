/**
 * The Scheduled page (B8): filters, the timeline by day, selection and the bulk actions'
 * words. Pure; the server keeps every rule and says what it moved (api/scheduled.py).
 */

export interface ScheduledItem {
  key: string;
  kind: "planned" | "draft";
  content_id: string | null;
  title: string;
  sku: string | null;
  batch_id: string | null;
  batch_name: string | null;
  shop_id: string;
  shop_name: string;
  group_ids: string[];
  profile_id: string | null;
  profile_name: string | null;
  at: string;
  local: string;
  local_day: string;
  local_time: string;
  status: string;
  note: string | null;
  movable: boolean;
  paused: boolean;
  listing_link: string | null;
}

export interface GroupSettings {
  per_shop_per_day: number;
  window_start: string;
  window_end: string;
  spacing_minutes: number;
  stagger_minutes: number;
}

export interface ScheduledGroup {
  id: string;
  name: string;
  shop_ids: string[];
  settings: GroupSettings | null;
  paused: boolean;
  upcoming: number;
  first_local: string | null;
  last_local: string | null;
}

export interface ScheduledData {
  time_zone: string;
  paused_all: boolean;
  groups: ScheduledGroup[];
  shops: { id: string; name: string; daily_limit: number | null }[];
  profiles: { id: string; name: string }[];
  items: ScheduledItem[];
}

export type BulkOp = "set_time" | "shift" | "spread" | "move_day" | "publish_now" | "remove";

export const BULK_LABELS: Record<BulkOp, string> = {
  set_time: "Set time…",
  shift: "Shift by…",
  spread: "Spread between…",
  move_day: "Move to day…",
  publish_now: "Publish now",
  remove: "Remove from schedule",
};

/** The status word on each row (one chip per status, the same everywhere on the page). */
export const STATUS_LABELS: Record<string, string> = {
  planned: "Planned",
  drafting: "Making the draft",
  held: "Held: fix personalization",
  scheduled: "Scheduled",
  missed: "Missed",
  publishing: "Publishing",
  waiting: "Waiting",
  published: "Published",
  failed: "Failed",
  not_published: "Not published",
};

export interface Filters {
  group: string;
  shop: string;
  profile: string;
  status: string;
}

export const NO_FILTERS: Filters = { group: "", shop: "", profile: "", status: "" };

export function filterItems(items: ScheduledItem[], f: Filters, groups: ScheduledGroup[]): ScheduledItem[] {
  const groupShops = f.group ? new Set(groups.find((g) => g.id === f.group)?.shop_ids ?? []) : null;
  return items.filter((i) =>
    (!groupShops || groupShops.has(i.shop_id)) &&
    (!f.shop || i.shop_id === f.shop) &&
    (!f.profile || i.profile_id === f.profile) &&
    (!f.status || i.status === f.status));
}

/** [day, items] in time order ("2026-10-22"). */
export function byDay(items: ScheduledItem[]): [string, ScheduledItem[]][] {
  const out = new Map<string, ScheduledItem[]>();
  for (const i of [...items].sort((a, b) => a.at.localeCompare(b.at))) {
    out.set(i.local_day, [...(out.get(i.local_day) ?? []), i]);
  }
  return [...out.entries()];
}

/** "Thursday, Oct 22" for a day key. */
export function dayLabel(day: string): string {
  const d = new Date(`${day}T12:00:00Z`);
  return d.toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric", timeZone: "UTC" });
}

/** Only rows whose time can still change are selected. */
export function movableKeys(items: ScheduledItem[]): string[] {
  return items.filter((i) => i.movable).map((i) => i.key);
}

/** Tick or untick a set of rows at once (a day, a group, every row shown). */
export function toggleMany(selected: string[], keys: string[], on: boolean): string[] {
  const set = new Set(selected);
  for (const k of keys) {
    if (on) set.add(k);
    else set.delete(k);
  }
  return [...set];
}

export function allIn(selected: string[], keys: string[]): boolean {
  return keys.length > 0 && keys.every((k) => selected.includes(k));
}

/** The keys a drag moves: the whole selection when the dragged row is part of it. */
export function dragged(selected: string[], key: string): string[] {
  return selected.includes(key) ? selected : [key];
}

/** The datetime-local value of a row ("2026-10-22T09:00"). */
export function wallValue(i: Pick<ScheduledItem, "local_day" | "local_time">): string {
  return `${i.local_day}T${i.local_time}`;
}

/** A group's summary line: "12 to go live · Thu Oct 22, 9:00 AM CDT → Sat Oct 24, 4:30 PM CDT". */
export function groupLine(g: ScheduledGroup): string {
  if (!g.upcoming) return "Nothing scheduled";
  const span = g.first_local && g.last_local && g.first_local !== g.last_local ? ` · ${g.first_local} → ${g.last_local}` : g.first_local ? ` · ${g.first_local}` : "";
  return `${g.upcoming} to go live${span}`;
}

/** A group's settings, as one line. */
export function settingsLine(s: GroupSettings | null): string {
  if (!s) return "No settings yet: listings keep the times they were given";
  return `${s.per_shop_per_day} a day per shop · ${s.window_start}–${s.window_end} · ${s.spacing_minutes} min apart · shops ${s.stagger_minutes} min apart`;
}
