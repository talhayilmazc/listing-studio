/**
 * When the listing allowance resets, in the seller's own time zone (fix/onboarding-followups, Part F).
 *
 * The rail and Settings format the reset instant (`resets_at`) in the seller's time zone: the account's
 * zone (`tenant.time_zone`, set in Settings), the one every scheduled time is shown in, with its
 * abbreviation ("Dec 1, 12:00 AM CST"). Only an account with no zone yet falls back to the browser's
 * (`useSession().timeZone`).
 */
export function resetTime(resetsAt: string, zone: string, opts: { weekday?: boolean } = {}): string {
  const d = new Date(resetsAt);
  if (Number.isNaN(d.getTime())) return "";
  const parts = new Intl.DateTimeFormat("en-US", {
    ...(opts.weekday ? { weekday: "short" as const } : {}),
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: zone,
    timeZoneName: "short",
  }).format(d);
  return parts.replace(/ /g, " ");
}
