"use client";

import { useState } from "react";
import type { Profile } from "@/lib/types";

/**
 * How listings are written with this profile. The seller tries the search style
 * on one profile and compares; nothing changes for the others.
 *
 * The wording never promises a ranking: the style follows Etsy's published
 * guidance on what search matches, which is all anyone outside Etsy can know.
 */
export function ListingStyle({
  profile,
  busy,
  onSave,
}: {
  profile: Profile;
  busy: boolean;
  onSave: (body: {
    listing_style?: "classic" | "search";
    title_min_length?: number | null;
    title_max_length?: number | null;
  }) => void;
}) {
  const search = profile.listing_style === "search";
  const [low, setLow] = useState(String(profile.title_min_length));
  const [high, setHigh] = useState(String(profile.title_max_length));
  if (!profile.search_style_available) return null;

  const n = (v: string) => (v.trim() === "" ? null : Number(v));
  const ok = (v: number | null) => v === null || (Number.isInteger(v) && v >= 20 && v <= 140);
  const valid = ok(n(low)) && ok(n(high)) && (n(low) ?? 40) <= (n(high) ?? 100);
  const changed = low !== String(profile.title_min_length) || high !== String(profile.title_max_length);
  const saveBounds = () =>
    valid && changed && onSave({ title_min_length: n(low), title_max_length: n(high) });

  return (
    <fieldset className="space-y-2 rounded-lg border border-slate-200 p-3">
      <legend className="px-1 text-xs font-medium text-slate-600">Listing style</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {(
          [
            ["classic", "Current", "110–140 character title made of keyword phrases."],
            ["search", "Search matching", "Short, readable title; tags, description opening and attributes carry the other keywords."],
          ] as const
        ).map(([value, label, detail]) => (
          <label
            key={value}
            className={
              "tap-row flex cursor-pointer gap-2 rounded-md border p-2.5 text-sm " +
              (profile.listing_style === value ? "border-brand-600 bg-brand-50" : "border-slate-200")
            }
          >
            <input
              type="radio"
              name={`style-${profile.id}`}
              className="mt-0.5"
              checked={profile.listing_style === value}
              disabled={busy}
              onChange={() => onSave({ listing_style: value })}
            />
            <span>
              <span className="block font-medium text-slate-800">{label}</span>
              <span className="block text-xs text-slate-500">{detail}</span>
            </span>
          </label>
        ))}
      </div>
      {search && (
        <div key="search-settings" className="space-y-2">
          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="label" htmlFor={`tmin-${profile.id}`}>Shortest title</label>
              <input
                id={`tmin-${profile.id}`}
                inputMode="numeric"
                className="field w-20 py-1.5 text-sm tabular-nums"
                value={low}
                aria-invalid={!valid}
                onChange={(e) => setLow(e.target.value)}
                onBlur={saveBounds}
                onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                disabled={busy}
              />
            </div>
            <div>
              <label className="label" htmlFor={`tmax-${profile.id}`}>Longest title</label>
              <input
                id={`tmax-${profile.id}`}
                inputMode="numeric"
                className="field w-20 py-1.5 text-sm tabular-nums"
                value={high}
                aria-invalid={!valid}
                onChange={(e) => setHigh(e.target.value)}
                onBlur={saveBounds}
                onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
                disabled={busy}
              />
            </div>
            <p className="pb-2 text-xs text-slate-500">
              <span>characters, at most 14 words</span>
              {profile.title_length_custom && (
                <button
                  key="reset"
                  type="button"
                  className="tap ml-2 underline decoration-dotted underline-offset-2"
                  disabled={busy}
                  onClick={() => {
                    setLow("40");
                    setHigh("100");
                    onSave({ title_min_length: null, title_max_length: null });
                  }}
                >
                  reset to 40–100
                </button>
              )}
            </p>
          </div>
          {!valid && (
            <p key="invalid" className="text-xs text-rose-700">
              Between 20 and 140, and the shortest no longer than the longest.
            </p>
          )}
          <p className="text-xs text-slate-500">
            <span>
              Written to be optimised for search matching, following Etsy&apos;s current guidance for titles, tags,
              descriptions and attributes. It does not promise a ranking. Applies to listings written from now on.
            </span>
          </p>
          <p className="text-xs text-slate-500" translate="no">
            <span>
              {profile.attribute_lists === null
                ? "Attributes: this category's value lists arrive with the next profile refresh; until then none are filled."
                : profile.attribute_lists === 0
                  ? "Attributes: this category offers none a design can answer."
                  : `Attributes: chosen only from Etsy's own lists for this category (${profile.attribute_lists} available).`}
            </span>
          </p>
        </div>
      )}
    </fieldset>
  );
}
