/**
 * When the listing allowance resets, in the seller's own time zone (fix/onboarding-followups, Part F).
 *
 * The server says it in the account's time zone ("Tue, Dec 1, 12:00 AM CST"); a seller whose account
 * zone is not where they are read a Chicago midnight as their own. The rail and Settings now format the
 * reset instant (`resets_at`) in the zone of the browser the seller is using: the same moment, in
 * their clock ("Dec 1, 9:00 AM GMT+3").
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
