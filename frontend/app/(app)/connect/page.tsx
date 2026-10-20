"use client";

import { ShopGroupsEditor } from "@/components/ShopGroupsEditor";

import { useEffect, useState } from "react";
import { AUTH_START_URL, api } from "@/lib/api";
import type { Shop, ShopsOut } from "@/lib/types";
import { SCOPE_REASONS, TRADEMARK_NOTICE } from "@/lib/legal";
import { disconnectedNotice } from "@/lib/shops";
import { useShops } from "@/components/ShopProvider";
import { ShopProfilesSetup } from "@/components/ShopProfilesSetup";

import { Txt } from "@/components/Txt";
// Full literal class strings so Tailwind's JIT keeps them.
const OK = "border-emerald-200 bg-emerald-50 text-emerald-700";
const WARN = "border-amber-200 bg-amber-50 text-amber-700";
const BAD = "border-rose-200 bg-rose-50 text-rose-700";
const BANNERS: Record<string, { cls: string; text: string }> = {
  connected: { cls: OK, text: "Shop connected. Its listings are being fetched now." },
  error: { cls: BAD, text: "Something went wrong during authorization. Please try again." },
  etsy_error: { cls: BAD, text: "Etsy refused the authorization." },
  denied: { cls: WARN, text: "Authorization was cancelled." },
  expired: { cls: WARN, text: "That authorization link expired. Please start again." },
  unconfigured: { cls: BAD, text: "Etsy API credentials are not configured on the server." },
  taken: {
    cls: BAD,
    text: "That shop is already connected to another account here. A shop can belong to one account at a time.",
  },
  limit_account: {
    cls: WARN,
    text: "Your account has reached its limit of connected shops. Disconnect one, or ask us for a higher limit.",
  },
  limit_app: {
    cls: WARN,
    text: "The service has reached the number of shops it can serve right now, because every shop shares one daily Etsy request budget. Please ask us.",
  },
  limit_paused: {
    cls: WARN,
    text: "New shop connections are paused for now. Your existing shops keep working.",
  },
};

export default function ConnectPage() {
  const { shops, slots, paused, refresh, select } = useShops();
  const [banner, setBanner] = useState<string | null>(null);
  // What disconnecting a shop cancelled: said, not left to be found out.
  const [disconnected, setDisconnected] = useState<string | null>(null);
  // Etsy's own reason when it refused the authorization (shown as Etsy wrote it).
  const [etsyReason, setEtsyReason] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const status = params.get("status");
    if (status) setBanner(status);
    setEtsyReason(params.get("reason"));
    const shop = params.get("shop");
    if (status === "connected" && shop) select(shop); // show the shop just connected
    refresh();
  }, [refresh, select]);

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e: any) {
      setError(e.message ?? String(e));
    }
  }

  const move = (index: number, by: number) => {
    if (!shops) return;
    const order = shops.map((s) => s.id);
    const [id] = order.splice(index, 1);
    order.splice(index + by, 0, id);
    run(() => api.orderShops(order));
  };

  const b = banner ? BANNERS[banner] : null;

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Your Etsy shops</h1>
        <p className="mt-1 text-sm text-slate-500">
          Authorize Listyro to create draft listings in your shops. You stay in control:
          nothing is ever published without your approval, and drafts are only created when you
          ask.
        </p>
      </div>

      {b && (
        <div key="div-77-6" className={`card p-3 text-sm ${b.cls}`}>
          <span>{b.text}</span>
          {banner === "etsy_error" && etsyReason && (
            <span key="reason" translate="no">{` Etsy's reason: ${etsyReason}`}</span>
          )}
        </div>
      )}
      {error && <div key="div-78-6" className={`card p-3 text-sm ${BAD}`}>{error}</div>}
      {disconnected && (
        <div key="disconnected" role="status" className={`card p-3 text-sm ${OK}`} data-testid="disconnected-note" translate="no">
          {disconnected}
        </div>
      )}
      {paused && (
        // Set by the operator in Admin; shops already connected are unaffected.
        <div key="paused" role="status" className={`card p-3 text-sm ${WARN}`} data-testid="connections-paused">
          {paused}
        </div>
      )}

      {shops === null ? (
        <div className="card h-40 animate-pulse bg-slate-50" />
      ) : (
        <div className="card divide-y divide-slate-100">
          {shops.length === 0 && (
            <p key="p-84-10" className="p-6 text-sm text-slate-600">
              No shop connected yet. You&apos;ll be sent to Etsy to sign in and approve access to
              your shop&apos;s listings.
            </p>
          )}
          {shops.map((shop, i) => (
            <ShopRow
              key={shop.id}
              shop={shop}
              first={i === 0}
              last={i === shops.length - 1}
              onUp={() => move(i, -1)}
              onDown={() => move(i, 1)}
              onRename={(name) => run(() => api.renameShop(shop.id, name))}
              onDisconnect={() =>
                run(async () => {
                  const res: ShopsOut = await api.disconnectShop(shop.id);
                  setDisconnected(res.disconnected ? disconnectedNotice(res.disconnected) : null);
                })
              }
            />
          ))}
          <div className="space-y-2 p-6">
            {slots?.can_add ? (
              <a href={AUTH_START_URL} className="btn-primary inline-flex">
                {shops.length ? "Connect another shop" : "Connect your shop"}
              </a>
            ) : (
              <button className="btn-primary" disabled>
                Connect another shop
              </button>
            )}
            {slots && (
              <p key="p-112-12" className="text-xs text-slate-500">
                <span><span>{slots.used}</span><span> of </span><span>{slots.limit}</span><span> shops connected</span>
                <Txt>{!slots.can_add &&
                  (paused
                    ? ". New connections are paused for now; a connected shop can still be reconnected."
                    : slots.used >= slots.limit
                      ? ". Your account is at its limit."
                      : ". The service is at its limit for now.")}</Txt></span>
              </p>
            )}
            {shops.length > 0 && (
              <p key="p-121-12" className="text-xs text-slate-400">
                On Etsy, each shop has its own account. To connect another shop, sign in to Etsy as
                that shop&apos;s owner when you are sent there.
              </p>
            )}
            <details className="text-xs text-slate-500">
              <summary className="tap cursor-pointer text-slate-600">What Listyro asks Etsy for, and why</summary>
              <ul className="mt-1.5 space-y-1.5">
                {SCOPE_REASONS.map((s) => (
                  <li key={s.scope}>
                    <code className="rounded bg-slate-100 px-1 py-0.5 text-[11px] text-slate-700" translate="no">{s.scope}</code>
                    <span>{` ${s.reason}`}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-1.5">
                Access and refresh tokens are encrypted on the server and never shown here. Disconnecting a shop
                deletes them at once.
              </p>
            </details>
            {/* Beside "Connect a shop", as Etsy's API terms ask. */}
            <p className="text-xs leading-relaxed text-slate-500" data-testid="connect-trademark-notice">{TRADEMARK_NOTICE}</p>
          </div>
        </div>
      )}

      {shops && shops.length > 1 && (
        <section key="groups" className="card space-y-3 p-6" aria-labelledby="groups-heading">
          <div>
            <h2 id="groups-heading" className="text-lg font-semibold text-slate-900">Shop groups</h2>
            <p className="mt-1 text-sm text-slate-500">
              A group is a set of your shops that you manage together. Wherever you choose where listings or a
              profile go, a group picks every shop in it at once; on the review page you can also divide your approved
              listings between groups so that each group gets different ones, and schedule them per shop. A shop can be
              in more than one group. Each shop keeps its own settings, and listings go only where you send them.
            </p>
          </div>
          <ShopGroupsEditor />
        </section>
      )}
    </div>
  );
}

function ShopRow({
  shop,
  first,
  last,
  onUp,
  onDown,
  onRename,
  onDisconnect,
}: {
  shop: Shop;
  first: boolean;
  last: boolean;
  onUp: () => void;
  onDown: () => void;
  onRename: (name: string) => void;
  onDisconnect: () => Promise<void> | void;
}) {
  const [name, setName] = useState(shop.display_name ?? "");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  return (
    <div className="space-y-3 p-6">
      <div className="flex items-start gap-3">
        <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-emerald-500" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium text-slate-900">{shop.name}</p>
          <p className="text-xs text-slate-500">
            <span><span>{shop.shop_name ? `On Etsy: ${shop.shop_name}` : "Fetching the shop's name…"}</span> ·
            connected <span>{new Date(shop.connected_at).toLocaleDateString()}</span></span>
          </p>
          {(shop.missing_scopes ?? []).includes("transactions_r") && (
            <p key="p-169-10" className="mt-1.5 rounded-md border border-amber-200 bg-amber-50 px-2 py-1.5 text-xs text-amber-900">
              Reconnect this shop once to let the Service read its sales (which listing sold,
              how many, the price and the date; never anything about your buyers).{" "}
              <a href={AUTH_START_URL} className="font-medium underline">
                Reconnect
              </a>
            </p>
          )}
          {/* Every reference listing set up in this shop in one click (a new shop has none yet). */}
          <div className="mt-2">
            <ShopProfilesSetup shopId={shop.id} shopName={shop.name} />
          </div>
        </div>
        <div className="flex shrink-0 gap-1">
          <button
            className="rounded-md px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 disabled:opacity-30 max-sm:h-11 max-sm:w-11 max-sm:border max-sm:border-slate-200 max-sm:text-base"
            onClick={onUp}
            disabled={first}
            aria-label={`Move ${shop.name} up`}
          >
            ↑
          </button>
          <button
            className="rounded-md px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 disabled:opacity-30 max-sm:h-11 max-sm:w-11 max-sm:border max-sm:border-slate-200 max-sm:text-base"
            onClick={onDown}
            disabled={last}
            aria-label={`Move ${shop.name} down`}
          >
            ↓
          </button>
        </div>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="block">
          <span className="text-xs font-medium text-slate-600">Name in the switcher</span>
          <input
            className="field mt-1 w-56 py-1.5 text-sm"
            value={name}
            placeholder={shop.shop_name ?? "Shop name"}
            maxLength={80}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => name !== (shop.display_name ?? "") && onRename(name)}
            onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
          />
        </label>
        <div className="ml-auto">
          {confirming ? (
            <span className="flex items-center gap-2 text-xs">
              <button
                className="rounded-md bg-rose-600 px-2.5 py-1.5 font-medium text-white hover:bg-rose-700 disabled:opacity-60"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  await onDisconnect();
                  setBusy(false);
                }}
              >
                {busy ? "Disconnecting…" : "Disconnect"}
              </button>
              <button className="text-slate-500 hover:text-slate-800" onClick={() => setConfirming(false)}>
                Cancel
              </button>
            </span>
          ) : (
            <button className="btn-secondary" onClick={() => setConfirming(true)}>
              Disconnect
            </button>
          )}
        </div>
      </div>
      {confirming && (
        <p key="p-236-6" className="text-xs text-slate-500">
          Disconnecting deletes this shop&apos;s tokens at once and everything we hold from it: its cached
          listings, its sales and fee figures, its profiles and the links to its drafts. Anything queued or
          scheduled for this shop is cancelled, and you are told what was. Your other shops, your uploads and
          your generated listings stay. Listings on Etsy are not touched.
        </p>
      )}
    </div>
  );
}
