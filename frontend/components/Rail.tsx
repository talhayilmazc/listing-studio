"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useSession } from "./SessionProvider";
import type { Connection, Profile, Quota } from "@/lib/types";

/**
 * Persistent dark left rail (docs/ui-direction-v2.md §1).
 *
 * Absorbs everything the old horizontal Nav carried: wordmark, navigation, the
 * Etsy connection indicator and the ToU-required API quota indicator. Nothing is
 * dropped — the quota figure and the connect path both live in the bottom block.
 *
 * The rail is inhabited rather than sparse: grouped sections, an icon per entry,
 * and the seller's own profiles as children of Profiles.
 */

type Item = { href: string; label: string; icon: Icon };

const SECTIONS: { title: string; items: Item[] }[] = [
  {
    title: "Workspace",
    items: [
      { href: "/dashboard", label: "Overview", icon: IconGrid },
      { href: "/", label: "Batches", icon: IconLayers },
      { href: "/upload", label: "Uploads", icon: IconUpload },
    ],
  },
  {
    title: "Library",
    items: [{ href: "/profiles", label: "Profiles", icon: IconTag }],
  },
];

const ADMIN_SECTION = {
  title: "Operator",
  items: [{ href: "/admin", label: "Admin", icon: IconShield }],
};

function isActive(pathname: string, href: string) {
  if (href === "/") return pathname === "/" || pathname.startsWith("/batches");
  return pathname === href || pathname.startsWith(href + "/");
}

export function Rail({ open, onClose }: { open: boolean; onClose: () => void }) {
  const pathname = usePathname() ?? "/";
  const { account } = useSession();
  const [profiles, setProfiles] = useState<Profile[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listProfiles()
      .then((p) => !cancelled && setProfiles(p))
      .catch(() => setProfiles([]));
    return () => {
      cancelled = true;
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

        <nav className="mt-1 flex-1 overflow-y-auto pb-4">
          {/* The entry is a convenience only; the server re-checks the role on every admin call. */}
          {(account?.is_admin ? [...SECTIONS, ADMIN_SECTION] : SECTIONS).map((section) => (
            <div key={section.title} className="mb-5">
              <p className="px-5 pb-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--rail-text)]/70">
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
                  />
                  {item.href === "/profiles" && (
                    <ProfileChildren profiles={profiles} pathname={pathname} onClose={onClose} />
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
}: {
  href: string;
  label: string;
  icon: Icon;
  active: boolean;
  onClose: () => void;
}) {
  return (
    <Link
      href={href}
      onClick={onClose}
      aria-current={active ? "page" : undefined}
      className={
        "relative flex items-center gap-2.5 px-5 py-[7px] text-sm transition-colors " +
        (active
          ? "bg-white/[0.07] font-medium text-[var(--rail-active)]"
          : "text-[var(--rail-text)] hover:bg-white/[0.03] hover:text-[var(--rail-active)]")
      }
    >
      {active && <span aria-hidden className="absolute inset-y-0 left-0 w-[2px] bg-brand-500" />}
      <Icon active={active} />
      <span className="truncate">{label}</span>
    </Link>
  );
}

/** The seller's own profiles, so their data lives in the frame. */
function ProfileChildren({
  profiles,
  pathname,
  onClose,
}: {
  profiles: Profile[] | null;
  pathname: string;
  onClose: () => void;
}) {
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

  const onProfiles = pathname === "/profiles";
  return (
    <div className="relative">
      {/* Guide line tying the children to their parent */}
      <span aria-hidden className="absolute inset-y-1 left-[26px] w-px bg-white/[0.08]" />
      {profiles.slice(0, 6).map((p) => (
        <Link
          key={p.id}
          href={"/profiles#profile-" + p.id}
          onClick={onClose}
          title={p.name}
          className="flex items-center gap-2 py-[5px] pl-[46px] pr-5 text-[13px] text-[var(--rail-text)] transition-colors hover:bg-white/[0.03] hover:text-[var(--rail-active)]"
        >
          <span
            aria-hidden
            className={
              "h-1 w-1 shrink-0 rounded-full " +
              (p.confirmed ? "bg-emerald-500/70" : "bg-amber-500/70")
            }
          />
          <span className="truncate">{p.name}</span>
        </Link>
      ))}
      {profiles.length > 6 && (
        <Link
          href="/profiles"
          onClick={onClose}
          className={
            "block py-[5px] pl-[46px] pr-5 text-[13px] transition-colors hover:text-[var(--rail-active)] " +
            (onProfiles ? "text-[var(--rail-text)]" : "text-[var(--rail-text)]/70")
          }
        >
          +{profiles.length - 6} more
        </Link>
      )}
    </div>
  );
}

/** Account, shop identity, and the ToU-required daily quota indicator. */
function RailFooter() {
  const { account, signOut } = useSession();
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
      {/* Who is signed in, and the way out. */}
      <div className="mb-1 flex items-center gap-2 px-2 py-1.5">
        <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--rail-text)]" title={account?.email}>
          {account?.email ?? ""}
        </span>
        <button
          type="button"
          onClick={signOut}
          className="shrink-0 rounded px-1 text-[11px] font-medium text-[var(--rail-text)] transition-colors hover:text-[var(--rail-active)]"
          title="Sign out"
        >
          Sign out
        </button>
      </div>

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
      className={"shrink-0 transition-opacity " + (active ? "opacity-100" : "opacity-70")}
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
