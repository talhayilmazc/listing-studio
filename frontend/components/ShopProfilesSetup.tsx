"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { announceProfilesChanged, shopProfilesLine } from "@/lib/shopSetup";
import type { ShopProfiles } from "@/lib/types";
import { ShopSetupDialog, type SetupItem } from "./ShopSetupDialog";
import { Txt } from "./Txt";

const POLL_MS = 3000;
/** Setting a shop up takes seconds per reference listing; past this, stop looking. */
const POLL_TRIES = 60;

/**
 * The Shops page's one button for a shop: "Use all reference listings here" sets every
 * reference listing (confirmed profile) of the account up in this shop in one click. That
 * click is the seller's confirmation: each shop setting is matched (the account's map, the
 * same name or terms) or copied from the main shop, once per setting. No dialog, unless
 * something needs the seller (a copy Etsy refused, two settings that match): then the setup
 * dialog opens for those reference listings, with the reason.
 */
export function ShopProfilesSetup({ shopId, shopName }: { shopId: string; shopName: string }) {
  const [status, setStatus] = useState<ShopProfiles | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<SetupItem[] | null>(null);
  const tries = useRef(0);
  const clicked = useRef(false);

  const load = useCallback(async () => {
    try {
      setStatus(await api.shopProfiles(shopId));
    } catch {
      setStatus(null);
    }
  }, [shopId]);

  useEffect(() => {
    load();
  }, [load]);

  // While reference listings are being set up here, look again; when it is done and some
  // need the seller (after their click), the dialog opens for those.
  useEffect(() => {
    if (!status) return;
    if (status.checking > 0 && tries.current < POLL_TRIES) {
      const t = setTimeout(() => {
        tries.current += 1;
        load();
      }, POLL_MS);
      return () => clearTimeout(t);
    }
    if (status.checking === 0 && clicked.current) {
      clicked.current = false;
      announceProfilesChanged();
      if (status.waiting.length) {
        setDialog(status.waiting.map((w) => ({ profileId: w.id, profileName: w.name, shopIds: [shopId] })));
      }
    }
  }, [status, load, shopId]);

  async function useAll() {
    setBusy(true);
    setError(null);
    try {
      clicked.current = true;
      tries.current = 0;
      setStatus(await api.useAllProfilesHere(shopId));
    } catch (e: any) {
      clicked.current = false;
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  if (!status || status.total === 0) return null;
  const line = shopProfilesLine(status);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs" data-testid="shop-profiles-setup">
      <span translate="no" className="text-slate-600" data-testid="shop-profiles-line">{line.text}</span>
      {line.offer && (
        <button key="use-all" type="button" className="btn-secondary px-3 py-1.5 text-xs font-semibold" disabled={busy}
          onClick={useAll} data-testid="use-all-here"
          title={`Set every reference listing up in ${shopName}: matching settings are used, missing ones are copied from each listing's main shop`}>
          <span>Use all reference listings here</span>
        </button>
      )}
      {status.waiting.length > 0 && status.checking === 0 && !line.offer && (
        <button key="waiting" type="button" className="btn-secondary px-3 py-1.5 text-xs"
          onClick={() => setDialog(status.waiting.map((w) => ({ profileId: w.id, profileName: w.name, shopIds: [shopId] })))}>
          <span>See what needs you</span>
        </button>
      )}
      {error && <p key="error" className="basis-full text-rose-700"><Txt>{error}</Txt></p>}
      {dialog && (
        <ShopSetupDialog key="dialog" items={dialog} onClose={() => {
          setDialog(null);
          load();
        }} />
      )}
    </div>
  );
}
