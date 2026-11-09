/**
 * When Home warns about Etsy requests (fix/onboarding-followups, Part F): under a fifth of the account's
 * daily requests left. The detailed card is on the Shops page (components/EtsyRequestsCard.tsx).
 */
import type { Quota } from "./types";

export const LOW_SHARE = 0.2;

export function requestsLow(quota: Pick<Quota, "ceiling"> | null): boolean {
  if (!quota) return false;
  const { remaining, limit } = quota.ceiling;
  return limit > 0 && remaining < limit * LOW_SHARE;
}
