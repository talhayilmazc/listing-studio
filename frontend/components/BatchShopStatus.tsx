"use client";

import { shopLine, shopsShown } from "@/lib/anotherShop";
import type { BatchShops } from "@/lib/types";

/**
 * Where the batch's listings stand in each shop ("OPA: 14 published · PCS: not sent"), and
 * which shop the page's actions apply to: the one picked in the menu, or every shop.
 */
export function BatchShopStatus({
  data,
  inView,
  inViewName,
  onAnotherShop,
}: {
  data: BatchShops | null;
  /** The shop picked in the menu; null: every shop is shown. */
  inView: string | null;
  inViewName: string | null;
  /** Offered when a listing is already on Etsy somewhere and another shop has none of them. */
  onAnotherShop?: () => void;
}) {
  const shown = shopsShown(data, inView);
  if (!data || shown.length === 0) return null;
  return (
    <div className="card space-y-2 p-3 text-sm" data-testid="batch-shop-status">
      <ul className="flex flex-wrap gap-x-5 gap-y-1">
        {shown.map((s) => (
          <li key={s.shop_id} className={"flex min-w-0 items-baseline gap-1.5 " + (s.shop_id === inView ? "font-medium" : "")}>
            <span className="truncate text-slate-900" translate="no" title={s.name}>{s.name}</span>
            <span className="text-slate-400" aria-hidden>·</span>
            <span className="text-slate-600" translate="no">{shopLine(s)}</span>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600">
        <p data-testid="batch-actions-shop">
          {inView ? (
            <span key="one">
              <span>Profile and SKU changes below apply to </span>
              <span className="font-medium text-slate-800" translate="no">{inViewName ?? "this shop"}</span>
              <span>, the shop picked in the menu. Listings already there are edited on Etsy.</span>
            </span>
          ) : (
            <span key="all">
              <span>All shops are shown: profile and SKU changes apply to listings not on Etsy yet. Pick a shop in the menu to change them for that shop.</span>
            </span>
          )}
        </p>
        {onAnotherShop && (
          <button key="another" type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={onAnotherShop} data-testid="another-shop-open">
            Publish in another shop
          </button>
        )}
      </div>
    </div>
  );
}
