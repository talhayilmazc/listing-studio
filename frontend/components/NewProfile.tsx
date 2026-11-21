"use client";

import { useState } from "react";
import type { Profile } from "@/lib/types";
import { ProfileForm, type FormMode } from "./ProfileForm";

/**
 * "New profile" on the Profiles page (feature/manual-profiles): three ways to make one. From one of the seller's
 * own listings (as before: the listings are below); from scratch, for a chosen shop; or a copy of another
 * profile for a chosen shop (the fast path for a new, empty shop).
 */
export function NewProfile({
  shops, profiles, defaultShop, onCreated,
}: {
  shops: { id: string; name: string }[];
  profiles: Profile[];
  defaultShop: string | null;
  onCreated: (p: Profile) => void;
}) {
  const [open, setOpen] = useState(false);
  const [shop, setShop] = useState(defaultShop ?? shops[0]?.id ?? "");
  const [source, setSource] = useState("");
  const [target, setTarget] = useState(defaultShop ?? shops[0]?.id ?? "");
  const [form, setForm] = useState<FormMode | null>(null);
  const confirmed = profiles.filter((p) => p.confirmed);
  const shopValue = shops.some((s) => s.id === shop) ? shop : shops[0]?.id ?? "";
  const targetValue = shops.some((s) => s.id === target) ? target : shops[0]?.id ?? "";

  return (
    <div className="w-full" data-testid="new-profile">
      <button type="button" className="btn-secondary" onClick={() => setOpen((o) => !o)} aria-expanded={open} disabled={shops.length === 0}
        data-testid="new-profile-button">
        New profile
      </button>
      {open && (
        <div key="choices" className="card mt-3 grid gap-4 p-4 text-sm md:grid-cols-3" data-testid="new-profile-choices">
          <div className="space-y-2">
            <p className="font-medium text-slate-900">From one of my listings</p>
            <p className="text-xs text-slate-600">The profile copies the listing&apos;s settings and keeps following it.</p>
            <a href="#your-listings" className="btn-secondary inline-flex px-3 py-1.5 text-xs" onClick={() => setOpen(false)}>Pick a listing below</a>
          </div>
          <div className="space-y-2">
            <p className="font-medium text-slate-900">Start from scratch</p>
            <p className="text-xs text-slate-600">One form with every setting a draft needs, from the shop&apos;s own Etsy options. For a new shop with no listings yet.</p>
            <select className="field py-1.5 text-sm" value={shopValue} onChange={(e) => setShop(e.target.value)} aria-label="Shop for the new profile" data-testid="scratch-shop">
              {shops.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={!shopValue} data-testid="scratch-start"
              onClick={() => { setForm({ kind: "scratch", shopId: shopValue }); setOpen(false); }}>
              Start from scratch
            </button>
          </div>
          <div className="space-y-2">
            <p className="font-medium text-slate-900">Copy from another profile</p>
            <p className="text-xs text-slate-600">Any profile, into any of your shops. The shop&apos;s own shipping, returns and processing are matched for you.</p>
            <select className="field py-1.5 text-sm" value={source} onChange={(e) => setSource(e.target.value)} aria-label="Profile to copy" data-testid="copy-source">
              <option value="">Choose a profile…</option>
              {confirmed.map((p) => <option key={p.id} value={p.id}>{`${p.name}${p.shop_name ? ` (${p.shop_name})` : ""}`}</option>)}
            </select>
            <select className="field py-1.5 text-sm" value={targetValue} onChange={(e) => setTarget(e.target.value)} aria-label="Shop for the copy" data-testid="copy-target">
              {shops.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <button type="button" className="btn-secondary px-3 py-1.5 text-xs" disabled={!source || !targetValue} data-testid="copy-start"
              onClick={() => { setForm({ kind: "copy", profileId: source, shopId: targetValue }); setOpen(false); }}>
              Copy to this shop
            </button>
          </div>
        </div>
      )}
      {form && <ProfileForm key="form" mode={form} onSaved={onCreated} onClose={() => setForm(null)} />}
    </div>
  );
}
