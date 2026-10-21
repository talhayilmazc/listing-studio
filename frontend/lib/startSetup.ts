// Set reference listings up in shops: their main shop's shipping, return and processing
// settings are matched or copied there, with no question and no dialog (lib/shopSetup.ts).
// The cards say "preparing…" meanwhile and reload when it is done.

import { api } from "./api";
import { announceProfilesChanged } from "./shopSetup";

export interface SetupItem {
  profileId: string;
  profileName: string | null;
  shopIds: string[];
}

export async function startSetup(items: SetupItem[]): Promise<void> {
  for (const item of items) {
    if (item.shopIds.length) await api.useProfileIn(item.profileId, { scope: "shops", connection_ids: item.shopIds });
  }
  announceProfilesChanged();
}
