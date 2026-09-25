// Scheduled publishing (docs/duzeltmeler-v6.md §G). Times are chosen and shown in
// the seller's own time zone and sent to the server in UTC.

/**
 * Go-live times for `count` listings, starting at `start` (local time).
 *
 * With `perDay`, that many go on each day, then the next day starts again at the
 * same local time of day ("5 a day"). Within a day, each is `spacingMinutes`
 * after the one before. Days are counted on the local calendar, so a change to
 * or from daylight saving keeps the chosen time of day.
 */
export function spreadTimes(
  start: Date,
  count: number,
  perDay: number | null,
  spacingMinutes: number,
): Date[] {
  const out: Date[] = [];
  const daily = perDay && perDay > 0 ? perDay : count;
  for (let i = 0; i < count; i++) {
    const day = Math.floor(i / daily);
    const slot = i % daily;
    const t = new Date(start.getTime());
    t.setDate(t.getDate() + day); // local calendar day
    t.setMinutes(t.getMinutes() + slot * spacingMinutes);
    out.push(t);
  }
  return out;
}

/** A value for <input type="datetime-local"> in local time, to the minute. */
export function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** The instant a datetime-local value means in this browser's time zone, or null. */
export function fromLocalInput(value: string): Date | null {
  if (!value) return null;
  const d = new Date(value); // no zone in the string: read as local time
  return Number.isNaN(d.getTime()) ? null : d;
}

/** The next whole hour from now: a sensible first suggestion. */
export function nextHour(now = new Date()): Date {
  const d = new Date(now.getTime());
  d.setMinutes(0, 0, 0);
  d.setHours(d.getHours() + 1);
  return d;
}

const STATUS: Record<string, string> = {
  scheduled: "Scheduled",
  publishing: "Publishing",
  waiting: "Waiting for the daily budget",
  published: "Published",
  failed: "Failed",
  not_published: "Not published",
};

export function scheduleLabel(status: string | null | undefined): string {
  return (status && STATUS[status]) || "Scheduled";
}

/** Local calendar day key (YYYY-MM-DD) for grouping. */
export function dayKey(d: Date): string {
  return toLocalInput(d).slice(0, 10);
}
