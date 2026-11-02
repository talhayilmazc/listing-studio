/**
 * The seasonal planner's words and small sums. The dates, the windows and every count come from the server
 * (backend/app/pipeline/season_calendar.py, season_plan.py); nothing is worked out twice here.
 */
import { shortDay } from "./whereWhen.ts";

export type Season = { key: string; name: string; on: string; opens: string; closes: string; first_left: string | null; why: string };
export type Named = { id: string; name: string };
export type PlannerState = {
  today: string; reviewed: string; about: string; seasons: Season[];
  batches: (Named & { approved: number; not_approved: number })[];
  shops: Named[]; groups: (Named & { shop_ids: string[] })[];
  defaults: { per_shop_per_day: number; window_start: string; window_end: string; spacing_minutes: number; stagger_minutes: number };
  time_zone: string;
};
export type PlannerChoice = {
  holiday: string; batch_ids: string[]; connection_ids: string[]; group_ids: string[];
  first_day: string | null; last_day: string | null;
  per_shop_per_day: number; window_start: string; window_end: string; spacing_minutes: number; stagger_minutes: number;
};
export type PlanDay = { day: string; new: number; taken: number };
export type PlanRow = { shop_id: string; name: string; total: number; cap: number; limit: number | null; moved: number; taken: number; days: PlanDay[] };
export type PlanPreview = {
  holiday: string; first_day: string | null; last_day: string | null; days: string[]; time_zone: string;
  listings: number; shops: number; total: number; fingerprint: string | null; sentence: string; button: string; disabled: string | null;
  rows: PlanRow[]; conflicts: string[]; did_not_fit: { title: string; shop_name: string; reason: string }[];
  left_out: { title: string; batch: string; reason: string }[];
};

const daysBetween = (a: string, b: string): number => Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000);

/** Where a holiday's window stands today: said in words beside its dates. */
export function windowState(season: Season, today: string): string {
  if (season.first_left === null) return `Window closed ${shortDay(season.closes)}`;
  if (season.opens > today) return `Opens ${shortDay(season.opens)}`;
  const left = daysBetween(today, season.closes) + 1;
  return `Open now, ${left} day${left === 1 ? "" : "s"} left`;
}

export const windowLine = (season: Season): string => `Go live ${shortDay(season.opens)} – ${shortDay(season.closes)}`;

/** Months over the timeline's days: each month's name and how many days it spans. */
export function monthHeads(days: string[]): { label: string; span: number }[] {
  const out: { key: string; label: string; span: number }[] = [];
  for (const day of days) {
    const key = day.slice(0, 7);
    const last = out[out.length - 1];
    if (last?.key === key) last.span += 1;
    else out.push({ key, label: shortDay(day).split(" ")[0], span: 1 });
  }
  return out.map(({ label, span }) => ({ label, span }));
}

/** A day in the timeline: how full it is against the shop's limit. */
export function dayTone(day: PlanDay, row: Pick<PlanRow, "cap" | "limit">): "empty" | "taken" | "some" | "full" {
  const used = day.new + day.taken;
  if (!used) return "empty";
  if (row.limit !== null && used >= row.limit) return "full";
  if (!day.new) return "taken";
  return day.new >= row.cap ? "full" : "some";
}

export const dayTitle = (day: PlanDay, shop: string): string =>
  `${shop}, ${shortDay(day.day)}: ${day.new} new${day.taken ? `, ${day.taken} already scheduled` : ""}`;

/** The shops a choice reaches, each once. */
export function chosenShops(state: Pick<PlannerState, "groups">, choice: Pick<PlannerChoice, "connection_ids" | "group_ids">): Set<string> {
  const out = new Set(choice.connection_ids);
  for (const group of state.groups) if (choice.group_ids.includes(group.id)) group.shop_ids.forEach((id) => out.add(id));
  return out;
}

export const toggled = (list: string[], id: string): string[] => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

export function firstChoice(state: PlannerState): PlannerChoice {
  const season = state.seasons.find((s) => s.first_left !== null) ?? state.seasons[0];
  return {
    holiday: season?.key ?? "", batch_ids: [], connection_ids: state.shops.length === 1 ? [state.shops[0].id] : [], group_ids: [],
    first_day: null, last_day: null, ...state.defaults,
  };
}

/** What the server needs: dates only when the seller chose their own, times as HH:MM. */
export const planBody = (choice: PlannerChoice): PlannerChoice => ({
  ...choice, window_start: choice.window_start.slice(0, 5), window_end: choice.window_end.slice(0, 5),
});
