"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, uploadAsset } from "@/lib/api";
import type { Profile, ShopListing } from "@/lib/types";
import { ProfileCard } from "@/components/ProfileCard";

const IMAGE_RE = /\.(png|jpe?g|webp|gif|tiff?)$/i;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [listings, setListings] = useState<ShopListing[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [replace, setReplace] = useState<{ id: number; status: string } | null>(null);
  const pendingListing = useRef<number | null>(null);
  const replaceInput = useRef<HTMLInputElement>(null);

  const loadProfiles = useCallback(async () => {
    try {
      setProfiles(await api.listProfiles());
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, []);

  const loadListings = useCallback(async () => {
    try {
      const res = await api.shopListings();
      setListings(res.listings);
      setSyncing(res.stale);
      // If a background sync was triggered, poll once more shortly for the results.
      if (res.stale) setTimeout(loadListings, 4000);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, []);

  useEffect(() => {
    loadProfiles();
    loadListings();
  }, [loadProfiles, loadListings]);

  async function detect() {
    setDetecting(true);
    setError(null);
    try {
      await api.detectProfiles();
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
      await api.useListingAsProfile(listingId);
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
      const { job_id } = await api.replaceImages(listingId, batch.id);
      for (let i = 0; i < 80; i++) {
        await sleep(1500);
        const s = await api.jobStatus(job_id);
        if (s.status === "succeeded") {
          setReplace({ id: listingId, status: "Updated ✓" });
          loadListings();
          return;
        }
        if (s.status === "failed") {
          setReplace({ id: listingId, status: `Failed: ${s.error ?? ""}` });
          return;
        }
      }
      setReplace({ id: listingId, status: "Timed out." });
    } catch (e: any) {
      setReplace({ id: listingId, status: e.message ?? String(e) });
    }
  }

  const onChange = (p: Profile) =>
    setProfiles((cur) => (cur ?? []).map((x) => (x.id === p.id ? p : x)));
  const onDelete = (id: string) =>
    setProfiles((cur) => (cur ?? []).filter((x) => x.id !== id));

  const detected = (profiles ?? []).filter((p) => !p.confirmed);
  const confirmed = (profiles ?? []).filter((p) => p.confirmed);

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Product-type profiles</h1>
          <p className="mt-1 text-sm text-slate-500">
            Profiles copy category, price, variations and description from your own listings. New
            drafts reuse them instead of inventing metadata.
          </p>
        </div>
        <button className="btn-primary" onClick={detect} disabled={detecting}>
          {detecting ? "Detecting…" : "Detect from my shop"}
        </button>
      </div>

      {error && <div className="card p-4 text-sm text-rose-700">{error}</div>}

      {profiles === null && <p className="text-sm text-slate-400">Loading…</p>}

      {detected.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-amber-700">
            Detected — confirm or rename before use ({detected.length})
          </h2>
          <div className="grid gap-4 md:grid-cols-2">
            {detected.map((p) => (
              <ProfileCard key={p.id} profile={p} onChange={onChange} onDelete={onDelete} />
            ))}
          </div>
        </section>
      )}

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-700">
          Confirmed profiles ({confirmed.length})
        </h2>
        {confirmed.length === 0 ? (
          <p className="text-sm text-slate-400">
            None yet. Detect them from your shop, or pick a listing below to use as a profile.
          </p>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {confirmed.map((p) => (
              <ProfileCard key={p.id} profile={p} onChange={onChange} onDelete={onDelete} />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-700">
          Your listings {syncing && <span className="text-xs text-amber-600">· syncing…</span>}
        </h2>
        {listings.length === 0 ? (
          <p className="text-sm text-slate-400">No listings cached yet.</p>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {listings.map((l) => (
              <div key={l.listing_id} className="card overflow-hidden">
                <div className="aspect-square bg-slate-100">
                  {l.thumbnail_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={l.thumbnail_url} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <div className="flex h-full items-center justify-center text-xs text-slate-400">
                      no image
                    </div>
                  )}
                </div>
                <div className="space-y-1 p-2">
                  <p className="truncate text-xs font-medium text-slate-700" title={l.title ?? ""}>
                    {l.title ?? `#${l.listing_id}`}
                  </p>
                  <div className="flex items-center justify-between text-[11px] text-slate-400">
                    <span>{l.state}</span>
                    {l.url && (
                      <a href={l.url} target="_blank" rel="noreferrer" className="underline">
                        view ↗
                      </a>
                    )}
                  </div>
                  <button
                    className="btn-secondary w-full py-1 text-xs"
                    onClick={() => useAsProfile(l.listing_id)}
                  >
                    Use as profile
                  </button>
                  <button
                    className="w-full py-1 text-xs text-slate-500 hover:text-slate-800"
                    onClick={() => pickReplacement(l.listing_id)}
                    title="Upload a folder of new photos to update this listing in place"
                  >
                    Replace images…
                  </button>
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
