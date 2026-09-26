// Scheduled publishing (docs/duzeltmeler-v6.md §G). Times are chosen and shown in
// the ACCOUNT's time zone (an IANA name such as "America/Chicago", set in
// Settings), not the computer's. The browser works in wall-clock values
// ("2026-09-28T17:00", what a datetime-local input holds) and sends them as they
// are; the server converts each to UTC once, with that zone's rules for that
// date, so a daylight-saving change never moves a chosen time.

/** A wall-clock time in some zone, to the minute: "YYYY-MM-DDTHH:MM". */
export type WallClock = string;

const formatters = new Map<string, Intl.DateTimeFormat>();

function partsFormatter(zone: string): Intl.DateTimeFormat {
  let f = formatters.get(zone);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
    formatters.set(zone, f);
  }
  return f;
}

/** The computer's own zone (what the account zone is detected from). */
export function browserZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

export function isValidZone(zone: string): boolean {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

/** What the clock on the wall in `zone` reads at `instant`. */
export function toWallClock(instant: Date, zone: string): WallClock {
  const p = Object.fromEntries(partsFormatter(zone).formatToParts(instant).map((x) => [x.type, x.value]));
  const hour = p.hour === "24" ? "00" : p.hour;
  return `${p.year}-${p.month}-${p.day}T${hour}:${p.minute}`;
}

const WALL = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/;

export function isWallClock(value: string): boolean {
  return WALL.test(value);
}

// Wall-clock arithmetic is done on the calendar itself (as if in UTC, which has
// no daylight saving), never on instants: "the next day at 17:00" stays 17:00.
function wallToNaive(w: WallClock): number {
  const m = WALL.exec(w);
  if (!m) return NaN;
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
}

function naiveToWall(ms: number): WallClock {
  return new Date(ms).toISOString().slice(0, 16);
}

export function addDays(w: WallClock, days: number): WallClock {
  return naiveToWall(wallToNaive(w) + days * 86_400_000);
}

export function addMinutes(w: WallClock, minutes: number): WallClock {
  return naiveToWall(wallToNaive(w) + minutes * 60_000);
}

/**
 * The instant a wall-clock time in `zone` means (for previews and the "already
 * passed" check; the server does the conversion that counts). Null for a time
 * that doesn't exist there (the hour the clocks skip in spring).
 */
export function wallToInstant(w: WallClock, zone: string): Date | null {
  const naive = wallToNaive(w);
  if (Number.isNaN(naive)) return null;
  // The zone's offset at a moment: how far its wall clock is from UTC.
  const offset = (at: number) => wallToNaive(toWallClock(new Date(at), zone)) - Math.floor(at / 60_000) * 60_000;
  let instant = naive - offset(naive);
  instant = naive - offset(instant); // settle across a transition
  const d = new Date(instant);
  return toWallClock(d, zone) === w ? d : null;
}

/** The next whole hour from now, on the wall clock in `zone`. */
export function nextHour(zone: string, now = new Date()): WallClock {
  const w = toWallClock(now, zone);
  return addMinutes(`${w.slice(0, 13)}:00`, 60);
}

/** "CDT", "EST", "GMT+3": the zone's short name at that instant. */
export function zoneAbbreviation(instant: Date, zone: string): string {
  const part = new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: "short" })
    .formatToParts(instant)
    .find((p) => p.type === "timeZoneName");
  return part?.value ?? zone;
}

/** "Sep 28, 2026, 5:00 PM CDT": a date and time, always with its zone. */
export function formatWhen(instant: Date | string, zone: string): string {
  const d = typeof instant === "string" ? new Date(instant) : instant;
  const text = new Intl.DateTimeFormat(undefined, { timeZone: zone, dateStyle: "medium", timeStyle: "short" }).format(d);
  return `${text} ${zoneAbbreviation(d, zone)}`;
}

/** "5:00 PM CDT": the time of day, with its zone. */
export function formatTime(instant: Date | string, zone: string): string {
  const d = typeof instant === "string" ? new Date(instant) : instant;
  const text = new Intl.DateTimeFormat(undefined, { timeZone: zone, hour: "numeric", minute: "2-digit" }).format(d);
  return `${text} ${zoneAbbreviation(d, zone)}`;
}

/** Calendar day (YYYY-MM-DD) of an instant in `zone`, for grouping. */
export function dayKey(instant: Date, zone: string): string {
  return toWallClock(instant, zone).slice(0, 10);
}

/**
 * Go-live times for `count` listings, starting at `start` (a wall-clock time in
 * the account's zone).
 *
 * With `perDay`, that many go on each day, then the next day starts again at the
 * same time of day ("5 a day"). Within a day, each is `spacingMinutes` after the
 * one before. Days are calendar days, so a daylight-saving change keeps the
 * chosen time of day.
 */
export function spreadTimes(start: WallClock, count: number, perDay: number | null, spacingMinutes: number): WallClock[] {
  const out: WallClock[] = [];
  const daily = perDay && perDay > 0 ? perDay : count;
  for (let i = 0; i < count; i++) {
    const day = Math.floor(i / daily);
    const slot = i % daily;
    out.push(addMinutes(addDays(start, day), slot * spacingMinutes));
  }
  return out;
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
