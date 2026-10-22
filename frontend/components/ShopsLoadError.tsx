"use client";

import { useState } from "react";
import { useShops } from "./ShopProvider";

/** Said on every page when the seller's shops could not be read, with "Try again" (quality pass 2026-10). */
export function ShopsLoadError() {
  const { error, refresh } = useShops();
  const [trying, setTrying] = useState(false);
  if (!error) return null;
  return (
    <div role="alert" className="border-b border-rose-200 bg-rose-50 px-6 py-3 lg:px-8" data-testid="shops-load-error">
      <p className="mx-auto flex w-full max-w-[1800px] flex-wrap items-center gap-2.5 text-sm text-rose-800">
        <span>Your shops could not be loaded, so this page may be missing things.</span>
        <span translate="no" className="text-rose-700">{error}</span>
        <button type="button" className="btn-secondary px-3 py-1 text-xs" disabled={trying}
          onClick={async () => { setTrying(true); await refresh(); setTrying(false); }}>
          {trying ? "Trying…" : "Try again"}
        </button>
      </p>
    </div>
  );
}
