import type { Disconnected } from "./types";

const n = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

/**
 * What the seller is told after disconnecting a shop: its tokens and Etsy data
 * are gone, and exactly what was waiting for it and will now not happen.
 */
export function disconnectedNotice(d: Disconnected): string {
  const shop = d.shop_name ?? "The shop";
  const cancelled = [
    d.queued_jobs ? n(d.queued_jobs, "queued job", "queued jobs") : "",
    d.scheduled_publishes ? n(d.scheduled_publishes, "scheduled go-live", "scheduled go-lives") : "",
    d.planned_drafts ? n(d.planned_drafts, "planned draft", "planned drafts") : "",
  ].filter(Boolean);
  const head = `${shop} is disconnected. Its tokens and the Etsy data we held for it are deleted, and no further request is made for it.`;
  if (cancelled.length === 0) return `${head} Nothing was queued or scheduled for it.`;
  return `${head} Cancelled with it: ${cancelled.join(", ")}. Listings already on Etsy are not touched.`;
}
