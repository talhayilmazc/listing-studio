/** How the admin's funnel reads (components/admin/Funnel.tsx). */

/** "64%", or "–" for the first step (nothing before it). */
export function conversion(share: number | null): string {
  if (share === null) return "–";
  return `${Math.round(share * 100)}%`;
}

/** "4 min 30 s", "2 h 5 min", "3 days 4 h"; "no approvals yet" when nobody has approved. */
export function waited(seconds: number | null): string {
  if (seconds === null) return "no approvals yet";
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ""}`;
  if (s < 86400) return `${Math.floor(s / 3600)} h${Math.floor((s % 3600) / 60) ? ` ${Math.floor((s % 3600) / 60)} min` : ""}`;
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  return `${days} day${days === 1 ? "" : "s"}${hours ? ` ${hours} h` : ""}`;
}
