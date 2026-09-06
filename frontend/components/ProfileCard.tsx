"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Profile } from "@/lib/types";

const TEMPLATES = ["apparel", "digital_products"];

export function ProfileCard({
  profile,
  onChange,
  onDelete,
}: {
  profile: Profile;
  onChange: (p: Profile) => void;
  onDelete: (id: string) => void;
}) {
  const [name, setName] = useState(profile.name);
  const [prefix, setPrefix] = useState(profile.title_prefix ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fetching, setFetching] = useState(false);

  // A freshly created/detected profile has its reference fetched in the background
  // (refresh_profile is enqueued on create). Poll until the cached payload lands so
  // the card shows "fetching reference…" and then flips to cached on its own.
  useEffect(() => {
    if (profile.is_fresh) {
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
  }, [profile.is_fresh, profile.id]);

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

  const nonPrimary = profile.reference_images.filter((img, i) => i > 0 && img.listing_image_id != null);

  return (
    <div
      // Anchor target for the rail's profile children.
      id={"profile-" + profile.id}
      className={`card scroll-mt-6 space-y-3 p-4 ${
        profile.confirmed ? "" : "ring-2 ring-amber-300"
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <input
            className="field max-w-[220px] py-1 text-sm font-medium"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={saveName}
          />
          {profile.source === "detected" && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
              detected
            </span>
          )}
          {profile.confirmed ? (
            <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-xs text-emerald-700">
              confirmed
            </span>
          ) : (
            <span className="rounded bg-amber-50 px-1.5 py-0.5 text-xs text-amber-700">
              needs confirmation
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 text-xs">
          <select
            className="field w-auto py-1 text-xs"
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
          <span
            className={
              profile.is_fresh
                ? "text-emerald-600"
                : fetching
                  ? "text-brand-600"
                  : "text-amber-600"
            }
          >
            {profile.is_fresh
              ? "● reference cached"
              : fetching
                ? "◌ fetching reference…"
                : "○ not fetched"}
          </span>
        </div>
      </div>

      <p className="text-xs text-slate-400">
        Reference listing #{profile.reference_listing_id}
      </p>

      <div className="flex items-center gap-2">
        <label className="text-xs text-slate-500">Title prefix</label>
        <input
          className="field max-w-[220px] py-1 text-xs"
          value={prefix}
          placeholder="none"
          onChange={(e) => setPrefix(e.target.value)}
          onBlur={savePrefix}
          disabled={busy !== null}
        />
        <span className="text-xs text-slate-400">prepended to every generated title</span>
      </div>

      {nonPrimary.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-slate-600">
            Fixed images (size charts added to every draft)
          </p>
          <div className="flex flex-wrap gap-2">
            {nonPrimary.map((img) => {
              const id = img.listing_image_id as number;
              const on = profile.fixed_image_ids.includes(id);
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => toggleFixed(id, !on)}
                  disabled={busy !== null}
                  title={img.kind ?? "image"}
                  className={`relative h-16 w-16 overflow-hidden rounded border-2 ${
                    on ? "border-brand-500" : "border-transparent opacity-60"
                  }`}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  {img.url && <img src={img.url} alt="" className="h-full w-full object-cover" />}
                  {img.kind === "size_chart" && (
                    <span className="absolute bottom-0 left-0 right-0 bg-black/50 text-[9px] text-white">
                      chart
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {error && <p className="text-xs text-rose-600">{error}</p>}

      <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
        {!profile.confirmed && (
          <button className="btn-primary" onClick={confirm} disabled={busy !== null}>
            {busy === "confirm" ? "Confirming…" : "Confirm"}
          </button>
        )}
        <button className="btn-secondary" onClick={refresh} disabled={busy !== null}>
          {busy === "refresh" ? "Refreshing…" : "Refresh reference"}
        </button>
        <button
          className="text-xs text-rose-500 hover:text-rose-700"
          onClick={remove}
          disabled={busy !== null}
        >
          Delete
        </button>
      </div>
    </div>
  );
}
