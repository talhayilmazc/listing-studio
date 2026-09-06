"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Connection, Quota } from "@/lib/types";

/**
 * Persistent dark left rail (docs/ui-direction-v2.md §1).
 *
 * Absorbs everything the old horizontal Nav carried: wordmark, navigation, the
 * Etsy connection indicator and the ToU-required API quota indicator. Nothing is
 * dropped — the quota figure and the connect path both live in the bottom block.
 */

const NAV = [
  { href: "/dashboard", label: "Overview" },
  { href: "/", label: "Batches" },
  { href: "/profiles", label: "Profiles" },
  { href: "/upload", label: "Uploads" },
];

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/" || pathname.startsWith("/batches");
  return pathname === href || pathname.startsWith(href + "/");
}

export function Rail({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname() ?? "/";

  return (
    <>
      {/* Mobile backdrop */}
      <div
        aria-hidden={!open}
        onClick={onClose}
        className={
          "fixed inset-0 z-30 bg-black/40 transition-opacity lg:hidden " +
          (open ? "opacity-100" : "pointer-events-none opacity-0")
        }
      />
      <aside
        className={
          "fixed inset-y-0 left-0 z-40 flex w-60 flex-col bg-[var(--rail)] transition-transform " +
          "lg:translate-x-0 " +
          (open ? "translate-x-0" : "-translate-x-full")
        }
      >
        <div className="flex items-center justify-between px-5 py-5">
          <Link href="/dashboard" onClick={onClose} className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
              LS
            </span>
            <span className="text-[15px] font-semibold text-[var(--rail-active)]">
              Listing Studio
            </span>
          </Link>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close navigation"
            className="rounded-md p-1 text-[var(--rail-text)] hover:text-[var(--rail-active)] lg:hidden"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <nav className="mt-2 flex-1 px-3">
          {NAV.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={onClose}
                aria-current={active ? "page" : undefined}
                className={
                  "relative block rounded-lg px-3 py-2 text-sm transition-colors " +
                  (active
                    ? "bg-white/[0.06] font-medium text-[var(--rail-active)]"
                    : "text-[var(--rail-text)] hover:bg-white/[0.04] hover:text-[var(--rail-active)]")
                }
              >
                {active && (
                  <span
                    aria-hidden
                    className="absolute inset-y-1.5 left-0 w-[2px] rounded-full bg-brand-500"
                  />
                )}
                {item.label}
              </Link>
            );
          })}
        </nav>

        <RailFooter />
      </aside>
    </>
  );
}

/** Shop identity + the ToU-required daily quota indicator. */
function RailFooter() {
  const [conn, setConn] = useState<Connection | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .connection()
      .then((c) => !cancelled && setConn(c))
      .catch(() => {});
    api
      .quota()
      .then((q) => !cancelled && setQuota(q))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const name = conn?.shop_name ?? null;
  const initials = (name ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");

  const used = quota ? quota.tenant_used / Math.max(1, quota.tenant_limit) : 0;
  const low = quota ? quota.tenant_remaining < quota.tenant_limit * 0.1 : false;

  return (
    <div className="border-t border-white/[0.08] p-3">
      {conn && !conn.connected ? (
        <Link
          href="/connect"
          className="block rounded-lg border border-white/[0.12] px-3 py-2 text-center text-sm text-[var(--rail-active)] transition-colors hover:bg-white/[0.06]"
        >
          Connect shop
        </Link>
      ) : (
        <Link
          href="/connect"
          className="flex items-center gap-2.5 rounded-lg px-2 py-2 transition-colors hover:bg-white/[0.04]"
          title={name ? `Connected to ${name}` : "Etsy connection"}
        >
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-white/[0.08] text-[11px] font-semibold text-[var(--rail-active)]">
            {initials || "—"}
          </span>
          <span className="min-w-0 flex-1">
            {conn === null ? (
              <span className="block h-3.5 w-24 animate-pulse rounded bg-white/[0.08]" />
            ) : (
              <span className="block truncate text-[13px] font-medium text-[var(--rail-active)]">
                {name ?? "Your shop"}
              </span>
            )}
          </span>
          {conn?.connected && (
            <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
          )}
        </Link>
      )}

      <div className="mt-2 px-2 pb-1">
        <div className="flex items-baseline justify-between text-[11px]">
          <span className="text-[var(--rail-text)]">API quota</span>
          <span className="tabular-nums text-[var(--rail-text)]">
            {quota ? (
              <>
                <span className={low ? "font-medium text-amber-400" : "font-medium text-[var(--rail-active)]"}>
                  {quota.tenant_remaining.toLocaleString()}
                </span>{" "}
                / {quota.tenant_limit.toLocaleString()}
              </>
            ) : (
              "—"
            )}
          </span>
        </div>
        <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-white/[0.08]">
          <div
            className={"h-full rounded-full transition-all " + (low ? "bg-amber-500" : "bg-brand-500")}
            style={{ width: Math.min(100, used * 100) + "%" }}
          />
        </div>
      </div>
    </div>
  );
}
