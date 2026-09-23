"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Profile, ReferenceImage, ShopListing } from "@/lib/types";
import { etsyListingLink } from "@/lib/format";

const TEMPLATES = ["apparel", "digital_products"];

export function ProfileCard({
  profile,
  onChange,
  onDelete,
  listing,
}: {
  profile: Profile;
  onChange: (p: Profile) => void;
  onDelete: (id: string) => void;
  /** The cached shop listing behind this profile, when known — picks the right back-link. */
  listing?: ShopListing;
}) {
  const [name, setName] = useState(profile.name);
  const [prefix, setPrefix] = useState(profile.title_prefix ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fetching, setFetching] = useState(false);

  // A freshly created/detected profile has its reference fetched in the background
  // (refresh_profile is enqueued on create). Poll until the cached payload lands so
  // the card shows "fetching reference…" and then flips to cached on its own.
  // Never fetched (just created) vs. fetched once but past its 24-hour limit and
  // cleared by retention. Only the first is in flight; the second needs a refresh.
  const expired = !profile.is_fresh && profile.updated_at !== null;

  useEffect(() => {
    if (profile.is_fresh || expired) {
      setFetching(false);
      return;
    }
    let cancelled = false;
    setFetching(true);
    (async () => {
      for (let i = 0; i < 16; i++) {
        await new Promise((r) => setTimeout(r, 2500));
        if (cancelled) return;
        try {
          const fresh = await api.getProfile(profile.id);
          if (cancelled) return;
          if (fresh.is_fresh) {
            onChange(fresh);
            return; // effect re-runs with is_fresh=true and clears `fetching`
          }
        } catch {
          /* keep polling; a transient error shouldn't stop the fetch */
        }
      }
      if (!cancelled) setFetching(false); // give up quietly after ~40s
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile.is_fresh, profile.id, expired]);

  async function run(label: string, fn: () => Promise<Profile | void>) {
    setBusy(label);
    setError(null);
    try {
      const updated = await fn();
      if (updated) onChange(updated);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(null);
    }
  }

  const saveName = () =>
    name !== profile.name && run("name", () => api.updateProfile(profile.id, { name }));
  const savePrefix = () =>
    prefix !== (profile.title_prefix ?? "") &&
    run("prefix", () => api.updateProfile(profile.id, { title_prefix: prefix }));
  const setTemplate = (t: string) =>
    run("template", () => api.updateProfile(profile.id, { content_template: t }));
  const confirm = () => run("confirm", () => api.confirmProfile(profile.id));
  const refresh = () => run("refresh", () => api.refreshProfile(profile.id));
  const remove = () =>
    run("delete", async () => {
      await api.deleteProfile(profile.id);
      onDelete(profile.id);
    });

  // Toggle one reference image's membership in the fixed-image set (size charts).
  const toggleFixed = (imageId: number, on: boolean) => {
    const next = on
      ? [...new Set([...profile.fixed_image_ids, imageId])]
      : profile.fixed_image_ids.filter((x) => x !== imageId);
    return run("images", () => api.updateProfile(profile.id, { fixed_image_ids: next }));
  };

  // The listing's own hero image identifies the profile at a glance; the rest is
  // split so size charts read as their own labelled group.
  const images = profile.reference_images;
  // Two limits: the image links are displayed, so they go after 6 hours; the rest
  // of the reference is only used to build drafts and stays usable for 24.
  const imagesHidden = profile.reference_images_expired;
  const hero =
    images.find((i) => i.kind !== "size_chart" && (i.url || imagesHidden)) ?? images[0];
  const rest = images.filter((i, idx) => i.listing_image_id != null && i !== hero && idx > 0);
  const charts = rest.filter((i) => i.kind === "size_chart");
  const others = rest.filter((i) => i.kind !== "size_chart");

  const backLink = etsyListingLink(
    profile.reference_listing_id,
    listing?.state ?? "active",
    listing?.url,
  );

  return (
    <div
      // Anchor target for the rail's profile children.
      id={"profile-" + profile.id}
      className={
        "card scroll-mt-6 flex flex-col overflow-hidden " +
        (profile.confirmed ? "" : "border-amber-300")
      }
    >
      {/* Reference listing's own hero image as the card header. */}
      <div className="relative aspect-[16/10] w-full bg-slate-100">
        {hero?.url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={hero.url}
            alt={`Reference listing ${profile.reference_listing_id}`}
            className="h-full w-full object-cover"
            loading="lazy"
            decoding="async"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-slate-400">
            {fetching
              ? "fetching reference…"
              : expired
                ? "Etsy data is cleared after 24 hours — refresh the reference"
                : imagesHidden
                  ? "Reference images are shown for 6 hours after each refresh"
                  : "no reference image"}
          </div>
        )}
        <div className="absolute left-3 top-3 flex flex-wrap gap-1.5">
          {profile.confirmed ? (
            <Chip tone="emerald">confirmed</Chip>
          ) : (
            <Chip tone="amber">needs confirmation</Chip>
          )}
          {profile.source === "detected" && <Chip tone="slate">detected</Chip>}
        </div>
        {/* ToU: product imagery always links back to the listing on Etsy. */}
        <a
          href={backLink}
          target="_blank"
          rel="noopener noreferrer"
          className="absolute bottom-3 right-3 rounded-md bg-white/90 px-2 py-1 text-xs font-medium text-slate-700 backdrop-blur transition-colors hover:bg-white hover:text-brand-700"
          title={
            listing?.state === "draft"
              ? "Edit draft in Shop Manager"
              : "View this listing on Etsy"
          }
        >
          {listing?.state === "draft" ? "Edit on Etsy ↗" : "View on Etsy ↗"}
        </a>
      </div>

      <div className="flex flex-1 flex-col gap-3 p-5">
        <div>
          <input
            className="field py-1.5 font-display text-lg text-slate-900"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={saveName}
            aria-label="Profile name"
          />
          <p className="mt-1.5 flex flex-wrap items-center gap-x-2 text-xs text-slate-400">
            <a
              href={backLink}
              target="_blank"
              rel="noopener noreferrer"
              className="tabular-nums hover:text-brand-700 hover:underline"
            >
              Listing #{profile.reference_listing_id} ↗
            </a>
            <span>·</span>
            <span
              className={
                profile.is_fresh
                  ? "text-emerald-700"
                  : fetching
                    ? "text-brand-700"
                    : "text-amber-700"
              }
            >
              {profile.is_fresh
                ? imagesHidden
                  ? "usable · images hidden after 6 hours"
                  : "reference cached"
                : fetching
                  ? "fetching reference…"
                  : expired
                    ? "Etsy data expired — refresh to use"
                    : "not fetched"}
            </span>
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor={`tpl-${profile.id}`}>
              Template
            </label>
            <select
              id={`tpl-${profile.id}`}
              className="field py-1.5 text-sm"
              value={profile.content_template}
              onChange={(e) => setTemplate(e.target.value)}
              disabled={busy !== null}
            >
              {TEMPLATES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label" htmlFor={`prefix-${profile.id}`}>
              Title prefix
            </label>
            <input
              id={`prefix-${profile.id}`}
              className="field py-1.5 text-sm"
              value={prefix}
              placeholder="none"
              onChange={(e) => setPrefix(e.target.value)}
              onBlur={savePrefix}
              disabled={busy !== null}
            />
          </div>
        </div>

        {charts.length > 0 && (
          <ImageRow
            title="Size charts"
            note={
              imagesHidden
                ? "images hidden after 6 hours; your selection still applies"
                : "appended to every draft using this profile"
            }
            images={charts}
            fixed={profile.fixed_image_ids}
            onToggle={toggleFixed}
            disabled={busy !== null}
            emphasise
          />
        )}

        {others.length > 0 && (
          <ImageRow
            title="Other reference images"
            note={
              imagesHidden
                ? "images hidden after 6 hours; refresh to see them"
                : "select any to append them too"
            }
            images={others}
            fixed={profile.fixed_image_ids}
            onToggle={toggleFixed}
            disabled={busy !== null}
          />
        )}

        {error && <p className="text-xs text-rose-600">{error}</p>}

        <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
          {!profile.confirmed && (
            <button className="btn-primary" onClick={confirm} disabled={busy !== null}>
              {busy === "confirm" ? "Confirming…" : "Confirm"}
            </button>
          )}
          <button className="btn-secondary" onClick={refresh} disabled={busy !== null}>
            {busy === "refresh" ? "Refreshing…" : "Refresh reference"}
          </button>
          <button
            className="ml-auto text-xs text-rose-600 hover:text-rose-700"
            onClick={remove}
            disabled={busy !== null}
          >
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}

function Chip({ tone, children }: { tone: "emerald" | "amber" | "slate"; children: React.ReactNode }) {
  const tones = {
    emerald: "border-emerald-200 bg-emerald-50 text-emerald-700",
    amber: "border-amber-200 bg-amber-50 text-amber-700",
    slate: "border-slate-200 bg-white text-slate-600",
  };
  return (
    <span className={"rounded-md border px-1.5 py-0.5 text-xs font-medium " + tones[tone]}>
      {children}
    </span>
  );
}

/** A labelled, visually separated row of reference images (§ size charts). */
function ImageRow({
  title,
  note,
  images,
  fixed,
  onToggle,
  disabled,
  emphasise,
}: {
  title: string;
  note: string;
  images: ReferenceImage[];
  fixed: number[];
  onToggle: (id: number, on: boolean) => void;
  disabled: boolean;
  emphasise?: boolean;
}) {
  return (
    <div className={"rounded-lg p-3 " + (emphasise ? "bg-brand-50/60" : "bg-slate-50")}>
      <p className="label mb-0">{title}</p>
      <p className="mb-2 text-xs text-slate-400">{note}</p>
      <div className="flex flex-wrap gap-2">
        {images.map((img) => {
          const id = img.listing_image_id as number;
          const on = fixed.includes(id);
          return (
            <button
              key={id}
              type="button"
              onClick={() => onToggle(id, !on)}
              disabled={disabled}
              aria-pressed={on}
              title={on ? "Included in every draft" : "Not included"}
              className={
                "relative h-16 w-16 overflow-hidden rounded-lg border-2 transition-all " +
                (on
                  ? "border-brand-600 opacity-100"
                  : "border-transparent opacity-55 hover:opacity-90")
              }
            >
              {img.url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={img.url} alt="" className="h-full w-full object-cover" loading="lazy" />
              ) : (
                // Link withheld past the 6-hour display limit: keep the slot and
                // its position so the selection still reads.
                <span className="flex h-full w-full items-center justify-center bg-slate-100 text-[11px] tabular-nums text-slate-400">
                  #{img.rank ?? "?"}
                </span>
              )}
              {on && (
                <span className="absolute right-0.5 top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-brand-600 text-[10px] font-bold text-white">
                  ✓
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
