"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, uploadAsset } from "@/lib/api";
import type { Profile, ShopListing } from "@/lib/types";
import { etsyListingLink } from "@/lib/format";
import { waitForJob } from "@/lib/jobs";
import { ProfileCard } from "@/components/ProfileCard";
import { useShops } from "@/components/ShopProvider";

const IMAGE_RE = /\.(png|jpe?g|webp|gif|tiff?)$/i;

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [listings, setListings] = useState<ShopListing[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [replace, setReplace] = useState<{ id: number; status: string } | null>(null);
  const pendingListing = useRef<number | null>(null);
  const replaceInput = useRef<HTMLInputElement>(null);
  // Everything on this page is the selected shop's: each shop has its own
  // profiles and listings (v5 §E). Switch shops in the rail.
  const { selected } = useShops();
  const shopId = selected?.id ?? null;

  const loadProfiles = useCallback(async () => {
    try {
      setProfiles(await api.listProfiles(shopId));
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, [shopId]);

  const loadListings = useCallback(async () => {
    try {
      const res = await api.shopListings(shopId);
      setListings(res.listings);
      setSyncing(res.stale);
      // If a background sync was triggered, poll once more shortly for the results.
      if (res.stale) setTimeout(loadListings, 4000);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, [shopId]);

  useEffect(() => {
    setProfiles(null);
    setListings([]);
    loadProfiles();
    loadListings();
  }, [loadProfiles, loadListings]);

  async function detect() {
    setDetecting(true);
    setError(null);
    try {
      await api.detectProfiles(shopId);
      // Detection runs in the background; poll for the new profiles a few times.
      for (let i = 0; i < 6; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        await loadProfiles();
      }
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setDetecting(false);
    }
  }

  async function useAsProfile(listingId: number) {
    try {
      await api.useListingAsProfile(listingId, shopId);
      await loadProfiles();
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  // Replace-images (B4): upload a folder of new photos, then update the listing in place.
  function pickReplacement(listingId: number) {
    pendingListing.current = listingId;
    replaceInput.current?.click();
  }

  async function onReplaceFiles(files: File[]) {
    const listingId = pendingListing.current;
    if (listingId == null) return;
    const images = files.filter((f) => IMAGE_RE.test(f.name));
    if (images.length === 0) {
      setReplace({ id: listingId, status: "No image files in that folder." });
      return;
    }
    setReplace({ id: listingId, status: "Uploading new photos…" });
    try {
      const batch = await api.createBatch();
      for (const f of images) {
        const rel = (f as any).webkitRelativePath || f.name;
        const gk = rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : "";
        await uploadAsset(batch.id, f, () => {}, gk);
      }
      await api.finalizeBatch(batch.id);
      setReplace({ id: listingId, status: "Updating listing…" });
      const { job_id } = await api.replaceImages(listingId, batch.id, shopId);
      const s = await waitForJob(job_id);
      if (s === null) {
        setReplace({ id: listingId, status: "Still working after 15 minutes; reload to check." });
      } else if (s.pause) {
        setReplace({ id: listingId, status: `Queued, not failed. ${s.pause.message}` });
      } else if (s.status === "succeeded") {
        setReplace({ id: listingId, status: "Updated ✓" });
        loadListings();
      } else {
        setReplace({ id: listingId, status: `Failed: ${s.error ?? ""}` });
      }
    } catch (e: any) {
      setReplace({ id: listingId, status: e.message ?? String(e) });
    }
  }

  const onChange = (p: Profile) =>
    setProfiles((cur) => (cur ?? []).map((x) => (x.id === p.id ? p : x)));
  const onDelete = (id: string) =>
    setProfiles((cur) => (cur ?? []).filter((x) => x.id !== id));

  const byListingId = new Map(listings.map((l) => [l.listing_id, l]));
  const detected = (profiles ?? []).filter((p) => !p.confirmed);
  const confirmed = (profiles ?? []).filter((p) => p.confirmed);

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <p className="max-w-2xl text-sm text-slate-500">
          Profiles copy category, price, variations and description from your own listings. New
          drafts reuse them instead of inventing metadata.
          {selected && (
            <>
              {" "}
              Showing <span className="font-medium text-slate-700">{selected.name}</span>; each
              shop has its own profiles. Switch shops at the bottom of the menu.
            </>
          )}
        </p>
        <button className="btn-primary shrink-0" onClick={detect} disabled={detecting || !shopId}>
          {detecting ? "Detecting…" : `Detect from ${selected?.name ?? "my shop"}`}
        </button>
      </div>

      {error && <div className="card p-4 text-sm text-rose-700">{error}</div>}

      {profiles === null && <p className="text-sm text-slate-400">Loading…</p>}

      {detected.length > 0 && (
        <section className="space-y-3">
          <SectionHead
            title="Detected"
            note="confirm or rename before use"
            count={detected.length}
            tone="amber"
          />
          <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
            {detected.map((p) => (
              <ProfileCard
                key={p.id}
                profile={p}
                onChange={onChange}
                onDelete={onDelete}
                listing={byListingId.get(p.reference_listing_id)}
              />
            ))}
          </div>
        </section>
      )}

      <section className="space-y-3">
        <SectionHead title="Confirmed profiles" count={confirmed.length} />
        {confirmed.length === 0 ? (
          <p className="text-sm text-slate-400">
            None yet. Detect them from your shop, or pick a listing below to use as a profile.
          </p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
            {confirmed.map((p) => (
              <ProfileCard
                key={p.id}
                profile={p}
                onChange={onChange}
                onDelete={onDelete}
                listing={byListingId.get(p.reference_listing_id)}
              />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <SectionHead
          title="Your listings"
          count={listings.length}
          note={syncing ? "syncing…" : undefined}
          tone={syncing ? "amber" : undefined}
        />
        {listings.length === 0 ? (
          <p className="text-sm text-slate-400">No listings cached yet.</p>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6">
            {listings.map((l) => (
              <div key={l.listing_id} className="card group overflow-hidden">
                <div className="relative aspect-square bg-slate-100">
                  {l.thumbnail_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={l.thumbnail_url}
                      alt=""
                      className="h-full w-full object-cover"
                      loading="lazy"
                      decoding="async"
                    />
                  ) : (
                    <div className="flex h-full items-center justify-center text-xs text-slate-400">
                      no image
                    </div>
                  )}
                  <span className="absolute left-2 top-2 rounded-md border border-slate-200 bg-white/90 px-1.5 py-0.5 text-[11px] font-medium text-slate-600 backdrop-blur">
                    {l.state}
                  </span>
                  {/* Actions surface on hover; the Etsy back-link is always present below. */}
                  <div className="absolute inset-x-0 bottom-0 flex flex-col gap-1 bg-gradient-to-t from-black/60 to-transparent p-2 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                    <button
                      className="w-full rounded-md bg-white/95 py-1 text-xs font-medium text-slate-800 hover:bg-white"
                      onClick={() => useAsProfile(l.listing_id)}
                    >
                      Use as profile
                    </button>
                    <button
                      className="w-full rounded-md py-1 text-xs font-medium text-white/90 hover:text-white"
                      onClick={() => pickReplacement(l.listing_id)}
                      title="Upload a folder of new photos to update this listing in place"
                    >
                      Replace images…
                    </button>
                  </div>
                </div>
                <div className="space-y-1 p-2.5">
                  <p className="truncate text-xs font-medium text-slate-700" title={l.title ?? ""}>
                    {l.title ?? `#${l.listing_id}`}
                  </p>
                  <div className="flex items-center justify-between gap-2 text-[11px] text-slate-400">
                    <span className="truncate tabular-nums" title={l.sku ?? ""}>
                      {l.sku ?? "—"}
                    </span>
                    {/* ToU: every listing card links back to Etsy. */}
                    <a
                      href={etsyListingLink(l.listing_id, l.state, l.url)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="shrink-0 font-medium text-brand-700 hover:underline"
                    >
                      {l.state === "draft" ? "edit ↗" : "view ↗"}
                    </a>
                  </div>
                  {replace?.id === l.listing_id && (
                    <p className="text-[11px] text-slate-500">{replace.status}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Hidden folder input for the replace-images flow. */}
      <input
        ref={replaceInput}
        type="file"
        multiple
        // @ts-expect-error non-standard folder-select attributes
        webkitdirectory=""
        directory=""
        className="hidden"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) onReplaceFiles(files);
        }}
      />
    </div>
  );
}

function SectionHead({
  title,
  count,
  note,
  tone,
}: {
  title: string;
  count: number;
  note?: string;
  tone?: "amber";
}) {
  return (
    <div className="flex items-baseline gap-2 border-b border-slate-200 pb-2">
      <h2 className="font-display text-lg text-slate-900">{title}</h2>
      <span className="text-xs tabular-nums text-slate-400">{count}</span>
      {note && (
        <span className={"text-xs " + (tone === "amber" ? "text-amber-700" : "text-slate-400")}>
          {note}
        </span>
      )}
    </div>
  );
}
