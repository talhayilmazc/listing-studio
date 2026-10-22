/**
 * "Where and when" on the review page (B4): the words and small rules of the one panel that
 * sends a batch's approved listings. Every approved listing goes to every chosen shop (the
 * owner, 2026-10-22: no splitting); the server counts and plans (api/send.py), this file
 * only names things and remembers the seller's last "When" in this browser.
 */
import type { Destination, GroupSchedule, SendCell, SendState, SendWhen } from "./types";

export const WHEN_LABELS: Record<SendWhen, string> = {
  drafts: "Now as drafts",
  publish: "Publish now",
  spread: "Spread out",
};

/** Said once, under "Send to". */
export const SETTINGS_LINE = "Each shop uses the profile's shipping, return and processing settings automatically.";

export const DEFAULT_SCHEDULE: Omit<GroupSchedule, "start_date"> = {
  per_shop_per_day: 5,
  window_start: "09:00",
  window_end: "17:00",
  spacing_minutes: 30,
  stagger_minutes: 5,
};

const STORE = "listyro.sendWhen";

export interface Remembered {
  when: SendWhen;
  schedule: Omit<GroupSchedule, "start_date"> | null;
}

/** The seller's last choice in this browser (a convenience; never needed to work). */
export function rememberedWhen(): Remembered {
  try {
    const raw = globalThis.localStorage?.getItem(STORE);
    const parsed = raw ? JSON.parse(raw) : null;
    if (parsed && (parsed.when === "drafts" || parsed.when === "publish" || parsed.when === "spread")) {
      return { when: parsed.when, schedule: parsed.schedule ?? null };
    }
  } catch {
    /* private window or blocked storage: the default */
  }
  return { when: "drafts", schedule: null };
}

export function rememberWhen(when: SendWhen, schedule: GroupSchedule | null): void {
  try {
    const keep = schedule ? (({ start_date: _s, ...rest }) => rest)(schedule) : rememberedWhen().schedule;
    globalThis.localStorage?.setItem(STORE, JSON.stringify({ when, schedule: keep }));
  } catch {
    /* not remembered: the default next time */
  }
}

/** A spread-out schedule starting today (the seller's day), from what was remembered. */
export function startingToday(today: string, remembered: Omit<GroupSchedule, "start_date"> | null): GroupSchedule {
  return { start_date: today, ...(remembered ?? DEFAULT_SCHEDULE) };
}

/** "09:00" -> "9:00 AM". */
export function clock(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h < 12 ? "AM" : "PM";
  const hour = h % 12 === 0 ? 12 : h % 12;
  return `${hour}:${String(m).padStart(2, "0")} ${suffix}`;
}

/** "2026-10-22" -> "Oct 22". */
export function shortDay(iso: string): string {
  const [y, mo, d] = iso.split("-").map(Number);
  const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][mo - 1];
  return y && month ? `${month} ${d}` : iso;
}

/** The one summary line shown for "Spread out" (with "change" next to it). */
export function scheduleLine(s: GroupSchedule): string {
  return [
    `From ${shortDay(s.start_date)}`,
    `${s.per_shop_per_day} a day per shop`,
    `${clock(s.window_start)}–${clock(s.window_end)}`,
    `${s.spacing_minutes} min apart`,
  ].join(" · ");
}

/** The groups and shops to show for a search: groups first. */
export function destinationOptions(state: SendState, query: string): { groups: SendState["groups"]; shops: SendState["shops"] } {
  const q = query.trim().toLowerCase();
  const hit = (name: string) => !q || name.toLowerCase().includes(q);
  return { groups: state.groups.filter((g) => hit(g.name)), shops: state.shops.filter((s) => hit(s.name)) };
}

export function isChosen(dest: Destination | null, kind: "group" | "shop", id: string): boolean {
  if (!dest) return false;
  return (kind === "group" ? dest.group_ids : dest.connection_ids).includes(id);
}

/** Ticking or unticking one group or shop; null when nothing is left (back to the default). */
export function toggle(dest: Destination | null, kind: "group" | "shop", id: string): Destination | null {
  const cur: Destination = dest ?? { group_ids: [], connection_ids: [] };
  const key = kind === "group" ? "group_ids" : "connection_ids";
  const list = cur[key].includes(id) ? cur[key].filter((x) => x !== id) : [...cur[key], id];
  const next = { ...cur, [key]: list };
  return next.group_ids.length || next.connection_ids.length ? next : null;
}

/** "GROUP 1 + Frost Tees": what a destination is called. */
export function destinationLabel(dest: Destination | null, state: SendState, fallback: string): string {
  if (!dest) return fallback;
  const parts = [
    ...dest.group_ids.map((g) => state.groups.find((x) => x.id === g)?.name ?? "a deleted group"),
    ...dest.connection_ids.map((c) => state.shops.find((x) => x.id === c)?.name ?? "a disconnected shop"),
  ];
  return parts.join(" + ") || fallback;
}

/** One shop of one listing in the list: "Frost Tees · Oct 22, 9:00 AM CDT", "Frost Mugs · already a draft". */
export function cellLine(cell: SendCell): string {
  const what: Record<SendCell["state"], string> = {
    new: "new draft",
    publish: "its draft goes live",
    draft: "already a draft",
    live: "already live",
    scheduled: "already scheduled",
    cannot: cell.reason ?? "cannot go there",
  };
  const at = cell.local && (cell.state === "new" || cell.state === "publish" || cell.state === "scheduled") ? cell.local : null;
  return [cell.shop_name, at ?? what[cell.state], at && cell.state === "scheduled" ? "already scheduled" : null, cell.note]
    .filter(Boolean)
    .join(" · ");
}
