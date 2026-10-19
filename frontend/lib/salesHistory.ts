// "Delete my sales history" on a shop's profit report, and its "Updated <time>" line (backend
// pipeline/sales_history.py, api/analytics.py). The words are here once; the backend's
// WHAT_IS_DELETED is the same sentence (backend/tests/test_sales_history.py compares them).
export const DELETE_LABEL = "Delete my sales history";

/** What is deleted, word for word as the server says it. */
export const WHAT_IS_DELETED =
  "This shop's order lines, its daily sales and fee totals, and the figures from statements and Etsy Ads reports you imported. Nothing on Etsy changes.";

/** The confirmation's question and what happens after. */
export const CONFIRM_QUESTION = "Delete this shop's sales history?";
export const AFTER_DELETE =
  "The profit report then starts today: later reads store nothing from before today. Disconnecting the shop or deleting your account deletes it too.";

/** "Updated 5:10 AM CDT · the last 30 days are read again every night". */
export function updatedLine(when: string | null, format: (iso: string) => string, days: number): string {
  const again = `the last ${days} days are read again every night`;
  return when ? `Updated ${format(when)} · ${again}` : `Not updated yet · ${again}`;
}

/** Said once the history was deleted (the day, as the report starts there). */
export function deletedLine(from: string | null): string {
  if (!from) return "";
  const day = new Date(`${from}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  return `You deleted this shop's earlier sales history; the report starts on ${day}.`;
}
