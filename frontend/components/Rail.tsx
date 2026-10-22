"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { shopSections, supportsAll } from "@/lib/shopScope";
import { useSession } from "./SessionProvider";
import type { Attention, Profile, ShopGroups } from "@/lib/types";
import { useShops } from "./ShopProvider";
import { RailAllowance } from "./Allowance";
import { SEARCH_ABOVE, byMainShop } from "@/lib/profileShops";
import { PROFILES_CHANGED } from "@/lib/shopSetup";

/**
 * Persistent dark left rail (docs/ui-direction-v2.md §1).
 *
 * Absorbs everything the old horizontal Nav carried: wordmark, navigation and the
 * Etsy connection indicator; the connect path lives in the bottom block. The
 * remaining Etsy request quota is shown on Overview, and a seller is told on every
 * page when it makes their work wait (PauseNotice); it is no longer a figure here.
 *
 * The rail is inhabited rather than sparse: grouped sections, an icon per entry,
 * and the seller's own profiles as children of Profiles.
 */

type Item = { href: string; label: string; icon: Icon };

// The order follows the work (B9): upload, review in a batch, schedule, see how it went; the
// library the work uses; then the shops and the account.
const SECTIONS: { title: string; items: Item[] }[] = [
  {
    title: "Workspace",
    items: [
      { href: "/dashboard", label: "Overview", icon: IconGrid },
      { href: "/upload", label: "Upload", icon: IconUpload },
      { href: "/batches", label: "Batches", icon: IconLayers },
      { href: "/scheduled", label: "Scheduled", icon: IconClock },
      { href: "/analytics", label: "Analytics", icon: IconChart },
    ],
  },
  {
    title: "Library",
    items: [
      { href: "/profiles", label: "Profiles", icon: IconTag },
      { href: "/templates", label: "Mockup templates", icon: IconShirt },
    ],
  },
  {
    title: "Account",
    items: [
      { href: "/connect", label: "Shops & groups", icon: IconStore },
      { href: "/settings", label: "Settings", icon: IconGear },
    ],
  },
];

const ADMIN_SECTION = {
  title: "Operator",
  items: [{ href: "/admin", label: "Admin", icon: IconShield }],
};

function isActive(pathname: string, href: string) {
  if (href === "/batches") return pathname.startsWith("/batches");
  return pathname === href || pathname.startsWith(href + "/");
}

export function Rail({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname() ?? "/";
  const { account } = useSession();
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const { shops } = useShops();
  // What needs the seller, as a count on each entry (GET /api/attention; the same counts as
  // Overview's "Needs your attention"). Read again on every page and once a minute.
  const [attention, setAttention] = useState<Attention | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = () => api.attention().then((a) => !cancelled && setAttention(a)).catch(() => {});
    load();
    const timer = setInterval(load, 60_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [pathname]);

  // Every shop's profiles (B6): a profile is the account's; the rail groups them by main shop.
  // Read once, and again whenever a profile changes anywhere (lib/shopSetup.ts PROFILES_CHANGED).
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .listProfiles(null)
        .then((p) => !cancelled && setProfiles(p))
        .catch(() => setProfiles([]));
    load();
    window.addEventListener(PROFILES_CHANGED, load);
    return () => {
      cancelled = true;
      window.removeEventListener(PROFILES_CHANGED, load);
    };
  }, []);

  return (
    <>
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
              L
            </span>
            <span className="text-[15px] font-semibold text-[var(--rail-active)]">
              Listyro
            </span>
          </Link>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close navigation"
            className="-mr-2 flex h-11 w-11 items-center justify-center rounded-md text-[var(--rail-text)] hover:text-[var(--rail-active)] lg:hidden"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <nav className="mt-1 flex-1 overflow-y-auto pb-4">
          {/* The entry is a convenience only; the server re-checks the role on every admin call. */}
          {(account?.is_admin ? [...SECTIONS, ADMIN_SECTION] : SECTIONS).map((section) => (
            <div key={section.title} className="mb-5">
              <p className="px-5 pb-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--rail-heading)]">
                {section.title}
              </p>
              {section.items.map((item) => (
                <div key={item.href}>
                  <RailLink
                    href={item.href}
                    label={item.label}
                    icon={item.icon}
                    active={isActive(pathname, item.href)}
                    onClose={onClose}
                    badge={attention?.nav?.[item.href] ?? 0}
                  />
                  {item.href === "/profiles" && (
                    <ProfileChildren key="profilechildren-125-18" profiles={profiles} shops={shops ?? []} onClose={onClose} />
                  )}
                </div>
              ))}
            </div>
          ))}
        </nav>

        <RailFooter />
      </aside>
    </>
  );
}

/**
 * One rail entry. The active state is an accent bar flush to the rail edge plus
 * a very light white wash — a filled panel reads as a hole punched in the dark.
 */
function RailLink({
  href,
  label,
  icon: Icon,
  active,
  onClose,
  badge = 0,
}: {
  href: string;
  label: string;
  icon: Icon;
  active: boolean;
  onClose: () => void;
  /** Things on this page that need the seller (a count; 0: none). */
  badge?: number;
}) {
  return (
    <Link
      href={href}
      onClick={onClose}
      aria-current={active ? "page" : undefined}
      aria-label={badge ? `${label}, ${badge} need${badge === 1 ? "s" : ""} your attention` : undefined}
      data-testid="rail-link"
      className={
        "relative flex items-center gap-2.5 px-5 py-2 text-sm transition-colors max-lg:min-h-[2.75rem] " +
        (active
          ? "bg-white/[0.12] font-semibold text-white"
          : "text-[var(--rail-text)] hover:bg-white/[0.06] hover:text-white")
      }
    >
      {active && <span key="bar" aria-hidden className="absolute inset-y-0 left-0 w-[3px] bg-brand-500" />}
      <Icon active={active} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {/* A fixed slot so a count arriving never moves the label. */}
      <span className="flex h-5 min-w-[1.5rem] shrink-0 items-center justify-end" aria-hidden={!badge}>
        {badge > 0 && (
          <span key="badge" translate="no" data-testid="rail-badge"
            className="rounded-full bg-amber-400 px-1.5 text-[11px] font-bold leading-5 text-stone-900 tabular-nums">
            {badge > 99 ? "99+" : badge}
          </span>
        )}
      </span>
    </Link>
  );
}

/** Whether the rail's "Profiles" section is open, remembered in this browser (a convenience). */
const OPEN_KEY = "listyro.rail.profiles";

/**
 * Every shop's profiles under the Profiles entry, grouped by main shop (B6), in a section the
 * seller opens and closes; a search once there are more than ten.
 */
function ProfileChildren({
  profiles,
  shops,
  onClose,
}: {
  profiles: Profile[] | null;
  shops: { id: string; name: string }[];
  onClose: () => void;
}) {
  const [open, setOpen] = useState(true);
  const [query, setQuery] = useState("");
  useEffect(() => {
    try {
      const saved = globalThis.localStorage?.getItem(OPEN_KEY);
      if (saved !== null && saved !== undefined) setOpen(saved !== "0");
    } catch {
      /* storage blocked: open */
    }
  }, []);
  const toggle = () => {
    setOpen((o) => {
      try {
        globalThis.localStorage?.setItem(OPEN_KEY, o ? "0" : "1");
      } catch {
        /* not remembered */
      }
      return !o;
    });
  };

  if (profiles === null) {
    return (
      <div className="space-y-1.5 py-1.5 pl-[46px] pr-5">
        {[0, 1].map((i) => (
          <div key={i} className="h-2.5 w-20 animate-pulse rounded bg-white/[0.07]" />
        ))}
      </div>
    );
  }
  if (profiles.length === 0) return null;
  const groups = byMainShop(profiles, shops, query);
  const several = shops.length > 1;

  return (
    <div className="relative" data-testid="rail-profiles">
      <button type="button" onClick={toggle} aria-expanded={open} data-testid="rail-profiles-toggle"
        className="flex w-full items-center gap-1.5 py-[5px] pl-[46px] pr-5 text-left text-[12px] text-[var(--rail-text)] hover:text-[var(--rail-active)] max-lg:min-h-[2.75rem]">
        <span aria-hidden className={"inline-block transition-transform " + (open ? "rotate-90" : "")}>›</span>
        <span translate="no">{`${open ? "Hide" : "Show"} ${profiles.length} profile${profiles.length === 1 ? "" : "s"}`}</span>
      </button>
      {open && (
        <div key="list">
          {profiles.length > SEARCH_ABOVE && (
            <div key="search" className="py-1 pl-[46px] pr-5">
              <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Find a profile…"
                aria-label="Find a profile" data-testid="rail-profiles-search"
                className="w-full rounded border border-white/10 bg-white/[0.06] px-2 py-1 text-[13px] text-white placeholder:text-[var(--rail-text)] max-lg:min-h-[2.75rem] max-lg:text-base" />
            </div>
          )}
          {groups.map(([shop, items]) => (
            <div key={shop}>
              {several && (
                <p key="shop" translate="no" className="truncate pb-0.5 pl-[46px] pr-5 pt-1.5 text-[11px] font-semibold uppercase tracking-wide text-[var(--rail-heading)]">
                  {shop}
                </p>
              )}
              {items.map((p) => (
                <Link
                  key={p.id}
                  href={"/profiles#profile-" + p.id}
                  onClick={onClose}
                  title={p.name}
                  className="flex items-center gap-2 py-[5px] pl-[46px] pr-5 text-[13px] max-lg:min-h-[2.75rem] text-[var(--rail-text)] transition-colors hover:bg-white/[0.03] hover:text-[var(--rail-active)]"
                >
                  <span aria-hidden className={"h-1 w-1 shrink-0 rounded-full " + (p.confirmed ? "bg-emerald-500/70" : "bg-amber-500/70")} />
                  <span className="truncate">{p.name}</span>
                </Link>
              ))}
            </div>
          ))}
          {query && groups.length === 0 && (
            <p key="none" className="py-1 pl-[46px] pr-5 text-[12px] text-[var(--rail-text)]">No profile matches.</p>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Account, the shop switcher (v5 §E) and the listing allowance.
 *
 * The selected shop decides which profiles, listings and figures the app shows;
 * batches and uploads are the account's and do not change with it.
 */
function RailFooter() {
  const pathname = usePathname();
  const { account, signOut } = useSession();
  const { shops, slots, selected, select, allShops, selectAll } = useShops();
  // "All shops" where the page can show every shop (Overview, Analytics, Batches, Scheduled).
  const showAll = allShops && supportsAll(pathname) && (shops?.length ?? 0) > 1;
  const label = showAll ? "All shops" : selected?.name ?? null;
  const [open, setOpen] = useState(false);
  const [groups, setGroups] = useState<ShopGroups | null>(null);
  const switcherRef = useRef<HTMLDivElement>(null);
  // Shop groups (v8 §B) head the shops in the switcher; read when it opens.
  useEffect(() => {
    if (open) api.shopGroups().then(setGroups).catch(() => {});
  }, [open]);
  const shopId = selected?.id ?? null;

  // The list closes on Escape or a click anywhere else.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onDown = (e: MouseEvent) => {
      if (!switcherRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  return (
    <div className="border-t border-white/[0.08] p-3">
      {/* Who is signed in, and the way out. */}
      <div className="mb-1 flex items-center gap-2 px-2 py-1.5">
        <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--rail-text)]" title={account?.email}>
          {account?.email ?? ""}
        </span>

        <button
          type="button"
          onClick={signOut}
          className="tap shrink-0 rounded px-1 text-[11px] font-medium text-[var(--rail-text)] transition-colors hover:text-[var(--rail-active)] max-lg:px-2 max-lg:py-2 max-lg:text-xs"
          title="Sign out"
        >
          Sign out
        </button>
      </div>

      {shops !== null && shops.length === 0 ? (
        <Link
          href="/connect"
          className="block rounded-lg border border-white/[0.12] px-3 py-2 text-center text-sm text-[var(--rail-active)] transition-colors hover:bg-white/[0.06]"
        >
          Connect shop
        </Link>
      ) : (
        <div className="relative" ref={switcherRef}>
          <p className="mb-1 px-2 text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--rail-text)]">
            Shop
          </p>
          {/* The shop decides what most pages show, so the switcher reads as a
              control: bordered, the name at full weight, and a visible way in. */}
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-haspopup="listbox"
            className={
              "flex w-full items-center gap-3 rounded-xl border px-2.5 py-2.5 text-left transition-colors " +
              "focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 " +
              (open
                ? "border-white/25 bg-white/[0.1]"
                : "border-white/15 bg-white/[0.05] hover:border-white/25 hover:bg-white/[0.09]")
            }
            title={label ? `Showing ${label}` : "Your shops"}
          >
            <ShopBadge name={label} active />
            <span className="min-w-0 flex-1">
              {label === null ? (
                <span className="block h-4 w-28 animate-pulse rounded bg-white/[0.08]" />
              ) : (
                <>
                  <span translate="no" className="block truncate text-sm font-semibold text-[var(--rail-active)]">
                    {label}
                  </span>
                  <span className="block text-[11px] text-white/70">
                    {shops && shops.length > 1 ? (
                      <span key="many">
                        <span>Switch · </span>
                        <span translate="no">{shops.length}</span>
                        <span> shops</span>
                      </span>
                    ) : (
                      <span key="one">Manage shops</span>
                    )}
                  </span>
                </>
              )}
            </span>
            <span
              aria-hidden
              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/[0.1] text-[var(--rail-active)]"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path d="M7 10l5-5 5 5M7 14l5 5 5-5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
          </button>

          {open && shops && (
            <div key="shop-list"
              role="listbox"
              aria-label="Your shops"
              className="absolute inset-x-0 bottom-full z-10 mb-2 max-h-[70vh] overflow-y-auto rounded-xl border border-white/15 bg-[#26221f] shadow-2xl"
            >
              <p className="px-3 pb-1 pt-2.5 text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--rail-text)]">
                Your shops
              </p>
              {shops.length > 1 && supportsAll(pathname) && (
                <button
                  key="all"
                  type="button"
                  role="option"
                  aria-selected={showAll}
                  data-testid="switcher-all-shops"
                  onClick={() => {
                    selectAll();
                    setOpen(false);
                  }}
                  className={
                    "relative flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm transition-colors " +
                    (showAll
                      ? "bg-white/[0.08] font-semibold text-[var(--rail-active)]"
                      : "text-white/85 hover:bg-white/[0.06] hover:text-[var(--rail-active)]")
                  }
                >
                  {showAll && <span key="bar" aria-hidden className="absolute inset-y-1.5 left-0 w-[3px] rounded-r bg-brand-500" />}
                  <ShopBadge name="All shops" active={showAll} />
                  <span className="min-w-0 flex-1 truncate">
                    <span>All shops</span>
                    <span translate="no" className="text-white/60">{` · ${shops.length}`}</span>
                  </span>
                </button>
              )}
              {shopSections(shops, groups?.groups ?? null).map(([title, members]) => (
                <div key={title || "none"}>
                  {title && (
                    <p key="group" translate="no" className="px-3 pb-0.5 pt-1.5 text-[10px] uppercase tracking-[0.1em] text-stone-400/80">{title}</p>
                  )}
                  {members.map((shop) => {
                const current = !showAll && shop.id === selected?.id;
                return (
                  <button
                    key={shop.id}
                    type="button"
                    role="option"
                    aria-selected={current}
                    onClick={() => {
                      select(shop.id);
                      setOpen(false);
                    }}
                    className={
                      "relative flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm transition-colors " +
                      (current
                        ? "bg-white/[0.08] font-semibold text-[var(--rail-active)]"
                        : "text-white/85 hover:bg-white/[0.06] hover:text-[var(--rail-active)]")
                    }
                  >
                    {current && <span key="bar" aria-hidden className="absolute inset-y-1.5 left-0 w-[3px] rounded-r bg-brand-500" />}
                    <ShopBadge name={shop.name} active={current} />
                    <span className="min-w-0 flex-1 truncate">{shop.name}</span>
                    {current ? (
                      <span key="cur" className="flex shrink-0 items-center gap-1 text-[11px] font-medium text-emerald-400">
                        <svg aria-hidden width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                          <path d="M5 12l5 5L19 7" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                        <span>Current</span>
                      </span>
                    ) : null}
                  </button>
                );
                  })}
                </div>
              ))}
              <Link
                href="/connect"
                onClick={() => setOpen(false)}
                className="block border-t border-white/[0.08] px-3 py-2.5 text-[12px] text-[var(--rail-text)] transition-colors hover:bg-white/[0.06] hover:text-[var(--rail-active)]"
              >
                <span>{slots?.can_add ? "Connect another shop · manage" : "Manage shops"}</span>
                {slots && (
                  <span key="slots" translate="no" className="ml-1 text-stone-400/70">
                    <span>(<span>{slots.used}</span> of <span>{slots.limit}</span>)</span>
                  </span>
                )}
              </Link>
            </div>
          )}
        </div>
      )}

      <div className="mt-3">
        <RailAllowance />
      </div>
    </div>
  );
}

function ShopBadge({ name, active }: { name: string | null; active?: boolean }) {
  const initials = (name ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
  return (
    <span
      className={
        "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[12px] font-semibold " +
        (active ? "bg-brand-500 text-white" : "bg-white/[0.1] text-[var(--rail-active)]")
      }
    >
      {initials || "—"}
    </span>
  );
}

/* ---------------------------------------------------------------- icons ---- */
/* Inline 16px strokes — the existing icon vocabulary, no icon package. */

type Icon = ({ active }: { active: boolean }) => JSX.Element;

function svg(children: React.ReactNode, active: boolean) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0"
      aria-hidden
    >
      {children}
    </svg>
  );
}

function IconGrid({ active }: { active: boolean }) {
  return svg(
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </>,
    active,
  );
}

function IconLayers({ active }: { active: boolean }) {
  return svg(
    <>
      <path d="M12 3l9 5-9 5-9-5 9-5z" />
      <path d="M3 13l9 5 9-5" />
    </>,
    active,
  );
}

function IconUpload({ active }: { active: boolean }) {
  return svg(
    <>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="M12 3v13M7 8l5-5 5 5" />
    </>,
    active,
  );
}

function IconClock({ active }: { active: boolean }) {
  return svg(
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>,
    active,
  );
}

function IconChart({ active }: { active: boolean }) {
  return svg(
    <>
      <path d="M4 20h16" />
      <path d="M7 16v-4M12 16V8M17 16v-7" />
    </>,
    active,
  );
}

function IconShield({ active }: { active: boolean }) {
  return svg(
    <>
      <path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6l8-3z" />
      <path d="M9 12l2 2 4-4" />
    </>,
    active,
  );
}

function IconTag({ active }: { active: boolean }) {
  return svg(
    <>
      <path d="M20.6 13.4L12 4.8H4.8V12l8.6 8.6a2 2 0 0 0 2.8 0l4.4-4.4a2 2 0 0 0 0-2.8z" />
      <circle cx="8.4" cy="8.4" r="1.2" />
    </>,
    active,
  );
}

function IconStore({ active }: { active: boolean }) {
  return svg(
    <>
      <path d="M4 9l1.5-5h13L20 9" />
      <path d="M4 9h16v11H4z" />
      <path d="M9 20v-6h6v6" />
    </>,
    active,
  );
}

function IconGear({ active }: { active: boolean }) {
  return svg(
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
    </>,
    active,
  );
}

function IconShirt({ active }: { active: boolean }) {
  return svg(
    <>
      <path d="M8 3l-5 3 2 4 2-1v12h10V9l2 1 2-4-5-3c-.5 1.7-2 3-4 3s-3.5-1.3-4-3z" />
    </>,
    active,
  );
}


