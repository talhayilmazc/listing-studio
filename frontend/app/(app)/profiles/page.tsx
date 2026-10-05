"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, uploadAsset } from "@/lib/api";
import { matchesProfile } from "@/lib/profileSearch";
import type { LinkSuggestion, Profile, ShopListing } from "@/lib/types";
import { etsyListingLink } from "@/lib/format";
import { waitForJob } from "@/lib/jobs";
import { ProfileCard } from "@/components/ProfileCard";
import { ShopGroupsEditor } from "@/components/ShopGroupsEditor";
import { useShops } from "@/components/ShopProvider";
import { ReplaceChoice } from "@/components/ReplaceChoice";
import type { ReplaceMode } from "@/lib/replaceModes";

import { Txt } from "@/components/Txt";
const IMAGE_RE = /\.(png|jpe?g|webp|gif|tiff?)$/i;

export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [suggestions, setSuggestions] = useState<LinkSuggestion[]>([]);
  const [listings, setListings] = useState<ShopListing[]>([]);
  const [syncing, setSyncing] = useState(false);
  // Find a profile among many (v7 §D1).
  const [query, setQuery] = useState("");
  const [detecting, setDetecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [replace, setReplace] = useState<{ id: number; status: string } | null>(null);
  // Asked first: which listing, then what to replace on it. The folder comes after the answer.
  const [choosing, setChoosing] = useState<ShopListing | null>(null);
  const pendingListing = useRef<{ id: number; mode: ReplaceMode } | null>(null);
  const replaceInput = useRef<HTMLInputElement>(null);
  // Profiles are the account's (v8 §C): every one is shown, with the shops it is
  // used in. The listings below are the selected shop's (switch shops in the rail).
  const { selected, shops } = useShops();
  const shopCount = shops?.length;
  const shopId = selected?.id ?? null;

  const loadProfiles = useCallback(async () => {
    try {
      setProfiles(await api.listProfiles(null));
      setSuggestions(await api.linkSuggestions());
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, []);

  // Opening the page refreshes what it shows: profiles not used in two weeks are
  // not kept warm in the background. Look again once the refreshes have landed.
  const openProfiles = useCallback(async () => {
    try {
      const list = await api.listProfiles(null, true);
      setProfiles(list);
      api.linkSuggestions().then(setSuggestions).catch(() => {});
      if (list.some((p) => p.refreshing)) {
        setTimeout(loadProfiles, 5000);
        setTimeout(loadProfiles, 15000);
      }
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, [loadProfiles]);

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
    openProfiles();
    loadListings();
  }, [openProfiles, loadListings]);

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

  // Replace-images (B4): choose what to replace, upload a folder of new photos,
  // then update the listing in place.
  function pickReplacement(listing: ShopListing, mode: ReplaceMode) {
    pendingListing.current = { id: listing.listing_id, mode };
    setChoosing(null);
    replaceInput.current?.click();
  }

  async function onReplaceFiles(files: File[]) {
    const pending = pendingListing.current;
    if (pending == null) return;
    const { id: listingId, mode } = pending;
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
      const { job_id } = await api.replaceImages(listingId, batch.id, mode, shopId);
      const s = await waitForJob(job_id);
      if (s === null) {
        setReplace({ id: listingId, status: "Still working after 15 minutes; reload to check." });
      } else if (s.pause) {
        setReplace({ id: listingId, status: `Queued, not failed. ${s.pause.message}` });
      } else if (s.status === "succeeded") {
        setReplace({ id: listingId, status: mode === "photos" ? "Photos replaced ✓ (title and tags unchanged)" : "Photos, title and tags replaced ✓" });
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
  const shown = (profiles ?? []).filter((p) =>
    matchesProfile(p, query, p.reference_title ?? byListingId.get(p.reference_listing_id ?? -1)?.title),
  );
  const detected = shown.filter((p) => !p.confirmed);
  const confirmed = shown.filter((p) => p.confirmed);

  // "Sync shop listings" (v7 §D2): changes made on Etsy show here without waiting
  // for the six-hour cache. Look again once the sync has had time to land.
  async function syncNow() {
    try {
      await api.syncShopListings(shopId);
      setSyncing(true);
      setTimeout(loadListings, 5000);
      setTimeout(loadListings, 15000);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }
  const failing = (profiles ?? []).filter((p) => p.refresh_error && p.reference_listing_id != null);
  const needsReference = (profiles ?? []).filter((p) => p.reference_listing_id == null);

  async function linkThese(sg: LinkSuggestion) {
    const [keep, ...rest] = sg.profiles;
    try {
      for (const other of rest) await api.linkProfiles(keep.id, other.id);
      await loadProfiles();
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  async function chooseReference(p: Profile, value: string) {
    if (!value) return;
    try {
      onChange(await api.setProfileReference(p.id, Number(value)));
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <p className="max-w-2xl text-sm text-slate-500">
          <span>Profiles copy category, price, variations and description from one of your own listings. New
          drafts reuse them instead of inventing metadata. A profile is used in as many of your shops as you
          set it up in: each shop keeps its own shipping profile, return policy and processing profile.</span>
          {selected && (
            <>
              {" "}
              Listings below are from <span className="font-medium text-slate-700">{selected.name}</span>; switch shops at
              the bottom of the menu.
            </>
          )}
        </p>
        <button className="btn-primary shrink-0" onClick={detect} disabled={detecting || !shopId}>
          {detecting ? "Detecting…" : `Detect from ${selected?.name ?? "my shop"}`}
        </button>
      </div>

      {error && <div key="div-184-6" className="card p-4 text-sm text-rose-700">{error}</div>}

      {profiles === null && <p key="p-186-6" className="text-sm text-slate-400">Loading…</p>}

      {profiles !== null && profiles.length > 0 && (
        <div key="div-188-6" className="flex flex-wrap items-center gap-3">
          <input
            type="search"
            className="field w-full max-w-md py-1.5 text-sm"
            placeholder="Search by name, template or reference listing title…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search profiles"
          />
          {query && (
            <span key="span-198-10" className="text-xs text-slate-500">
              <span><span>{shown.length}</span> of <span>{profiles.length}</span></span>
            </span>
          )}
        </div>
      )}

      {failing.length > 0 && (
        // Profiles refresh on their own (v6 §H); the seller hears when one can't.
        <div key="div-206-6" role="alert" className="card border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
          <p className="font-medium">
            {failing.length === 1 ? "A profile couldn't refresh" : `${failing.length} profiles couldn't refresh`}
          </p>
          <ul className="mt-1 space-y-0.5 text-xs">
            {failing.map((p) => (
              <li key={p.id}>
                <a href={"#profile-" + p.id} className="font-medium underline">
                  {p.name}
                </a>
                <span><span>: </span><Txt>{p.refresh_error}</Txt></span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(shopCount ?? 0) > 1 && (
        <details key="groups" className="card p-4 text-sm">
          <summary className="tap cursor-pointer font-medium text-slate-800">Shop groups</summary>
          <p className="mb-3 mt-1 text-xs text-slate-500">
            A profile can be set up in a whole group at once (&ldquo;Use in&rdquo; on each profile). The same groups are on
            the Shops page.
          </p>
          <ShopGroupsEditor />
        </details>
      )}

      {suggestions.length > 0 && (
        <section key="link-these" className="card space-y-2 border-brand-100 p-4 text-sm">
          <p className="font-medium text-slate-800">Same name in different shops</p>
          <p className="text-xs text-slate-500">
            One profile can serve all of them. Linking keeps the first profile&apos;s shared settings and adds the
            other&apos;s shop to it; batches and listings that used the other one use this one.
          </p>
          <ul className="space-y-2">
            {suggestions.map((sg) => (
              <li key={sg.name} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span translate="no" className="font-medium">{sg.name}</span>
                <span translate="no" className="text-xs text-slate-500">{sg.profiles.map((p) => p.shop_name).join(", ")}</span>
                <span className="text-xs text-slate-400">{sg.why}</span>
                <button type="button" className="btn-secondary px-2 py-1 text-xs" onClick={() => linkThese(sg)}>
                  Link these
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {needsReference.length > 0 && (
        <section key="needs-reference" className="card space-y-2 border-amber-300 p-4 text-sm">
          <p className="font-medium text-slate-800">Choose a reference listing</p>
          {needsReference.map((p) => (
            <div key={p.id} className="flex flex-wrap items-center gap-2 text-xs">
              <span><span translate="no" className="font-medium">{p.name}</span><span> lost its main shop. Its main shop is now </span><span translate="no">{p.shop_name}</span><span>.</span></span>
              {selected?.id === p.connection_id ? (
                <select key="pick" className="field py-1 text-xs" defaultValue="" onChange={(e) => chooseReference(p, e.target.value)}
                  aria-label={`Reference listing for ${p.name}`}>
                  <option value="">Choose one of this shop&apos;s listings…</option>
                  {listings.map((l) => <option key={l.listing_id} value={l.listing_id}>{l.title ?? `#${l.listing_id}`}</option>)}
                </select>
              ) : (
                <span key="switch" className="text-slate-500">Switch to that shop to choose one of its listings.</span>
              )}
            </div>
          ))}
        </section>
      )}

      {detected.length > 0 && (
        <section key="section-225-6" className="space-y-3">
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
                listing={byListingId.get(p.reference_listing_id ?? -1)}
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
                listing={byListingId.get(p.reference_listing_id ?? -1)}
              />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <SectionHead
            title="Your listings"
            count={listings.length}
            note={syncing ? "syncing…" : undefined}
            tone={syncing ? "amber" : undefined}
          />
          <button
            type="button"
            className="btn-secondary px-2.5 py-1 text-xs"
            onClick={syncNow}
            disabled={syncing || !shopId}
            title="Fetch this shop's listings from Etsy now, e.g. after publishing or reactivating one there"
          >
            {syncing ? "Syncing…" : "Sync shop listings"}
          </button>
        </div>
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
                  <div className="absolute inset-x-0 bottom-0 flex flex-col gap-1 bg-gradient-to-t from-black/60 to-transparent p-2 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100 max-sm:opacity-100">
                    <button
                      className="w-full rounded-md bg-white/95 py-1 text-xs font-medium text-slate-800 hover:bg-white max-sm:min-h-[2.75rem]"
                      onClick={() => useAsProfile(l.listing_id)}
                    >
                      Use as profile
                    </button>
                    <button
                      className="w-full rounded-md py-1 text-xs font-medium text-white/90 hover:text-white max-sm:min-h-[2.75rem] max-sm:bg-black/40"
                      onClick={() => setChoosing(l)}
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
                    <span translate="no" className="truncate tabular-nums" title={l.sku ?? ""}>
                      {l.sku ?? "—"}
                    </span>
                    {/* ToU: every listing card links back to Etsy. */}
                    <a
                      href={etsyListingLink(l.listing_id, l.state, l.url)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="tap shrink-0 font-medium text-brand-700 hover:underline"
                    >
                      {l.state === "draft" ? "edit ↗" : "view ↗"}
                    </a>
                  </div>
                  {replace?.id === l.listing_id && (
                    <p key="p-345-18" className="text-[11px] text-slate-500">{replace.status}</p>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {choosing && (
        <div key="replace-choice" className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/40 sm:items-center sm:p-4" onClick={() => setChoosing(null)}>
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Replace images"
            className="max-h-[90vh] w-full overflow-y-auto rounded-t-2xl bg-white px-4 pt-4 shadow-lg sm:max-w-lg sm:rounded-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="text-sm font-medium text-slate-900">Replace images</p>
            <p className="mb-3 truncate text-xs text-slate-500" translate="no">{choosing.title ?? `#${choosing.listing_id}`}</p>
            <ReplaceChoice
              intro="Choose what to replace on this listing. You pick the folder of new photos next; nothing changes on Etsy until they are uploaded."
              photos="the photos in the folder you choose, in file-name order"
              confirmLabel={(m) => (m === "photos" ? "Choose the folder: photos only" : "Choose the folder: photos, title and tags")}
              onConfirm={(m) => pickReplacement(choosing, m)}
              onCancel={() => setChoosing(null)}
              pinActions
            />
          </div>
        </div>
      )}

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
      <span translate="no" className="text-xs tabular-nums text-slate-400">{count}</span>
      {note && (
        <span key="span-389-6" className={"text-xs " + (tone === "amber" ? "text-amber-700" : "text-slate-400")}>
          {note}
        </span>
      )}
    </div>
  );
}
