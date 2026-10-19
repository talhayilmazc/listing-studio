"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ShopGroups } from "@/lib/types";
import { useShops } from "./ShopProvider";

/**
 * Shop groups (v8 §B): named groups of your shops (Group A = 4 shops, Group B = 5).
 * A shop may be in several groups. Wherever listings or a profile go somewhere
 * (a profile's "Use in", the review page, distribution and scheduling), a group
 * picks every shop in it at once. The same groups are edited here, on the Shops
 * page and on the Profiles page.
 */
export function ShopGroupsEditor({ onChange }: { onChange?: (g: ShopGroups) => void }) {
  const { shops, refresh } = useShops();
  const [data, setData] = useState<ShopGroups | null>(null);
  const [name, setName] = useState("");
  const [members, setMembers] = useState<string[]>([]);
  const [renaming, setRenaming] = useState<Record<string, string>>({});
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const take = useCallback(
    (g: ShopGroups) => {
      setData(g);
      onChange?.(g);
    },
    [onChange],
  );

  useEffect(() => {
    api.shopGroups().then(take).catch((e) => setError(String(e.message ?? e)));
  }, [take]);

  async function run(fn: () => Promise<ShopGroups>) {
    setBusy(true);
    setError(null);
    try {
      take(await fn());
      await refresh();
      return true;
    } catch (e: any) {
      setError(String(e.message ?? e));
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (!data) return <p className="text-sm text-slate-400">{error ?? "Loading groups…"}</p>;
  const allShops = shops ?? [];

  function rename(id: string, current: string) {
    const next = (renaming[id] ?? current).trim();
    if (next && next !== current) run(() => api.updateShopGroup(id, { name: next }));
  }

  return (
    <div className="space-y-4">
      {data.groups.length === 0 && (
        <p key="none" className="text-sm text-slate-500">No groups yet. Name a group and tick its shops below.</p>
      )}
      <ul className="space-y-3">
        {data.groups.map((g) => {
          const ids = g.shops.map((s) => s.id);
          return (
            <li key={g.id} className="rounded-lg border border-slate-200 p-3 text-sm" data-testid="shop-group">
              <div className="flex flex-wrap items-center gap-2">
                <input
                  className="field min-w-0 max-w-[14rem] flex-1 py-1 text-sm font-medium"
                  value={renaming[g.id] ?? g.name}
                  onChange={(e) => setRenaming((r) => ({ ...r, [g.id]: e.target.value }))}
                  onBlur={() => rename(g.id, g.name)}
                  onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                  aria-label={`Name of group ${g.name}`}
                  maxLength={40}
                />
                <span translate="no" className="text-xs text-slate-500">{`${ids.length} shop${ids.length === 1 ? "" : "s"}`}</span>
                {deleting === g.id ? (
                  <span key="confirm" className="ml-auto flex flex-wrap items-center gap-2 text-xs">
                    <span className="text-slate-600">Delete this group? Its shops stay connected and in their other groups.</span>
                    <button type="button" className="btn-secondary px-2 py-1 text-xs" disabled={busy} onClick={() => setDeleting(null)}>Keep it</button>
                    <button type="button" className="btn-primary bg-rose-600 px-2 py-1 text-xs hover:bg-rose-700" disabled={busy}
                      data-testid="delete-group-confirm"
                      onClick={async () => { if (await run(() => api.deleteShopGroup(g.id))) setDeleting(null); }}>
                      Delete group
                    </button>
                  </span>
                ) : (
                  <button key="delete" type="button" className="tap ml-auto text-xs text-rose-600" disabled={busy} onClick={() => setDeleting(g.id)}>
                    Delete group
                  </button>
                )}
              </div>
              <div className="mt-2 grid gap-x-4 sm:grid-cols-2 lg:grid-cols-3">
                {allShops.map((s) => (
                  <label key={s.id} className="flex min-h-[2.75rem] min-w-0 items-center gap-2 sm:min-h-[2rem]">
                    <input type="checkbox" className="h-4 w-4 shrink-0" checked={ids.includes(s.id)} disabled={busy}
                      data-testid="group-member"
                      onChange={(e) => run(() => api.updateShopGroup(g.id, {
                        connection_ids: e.target.checked ? [...ids, s.id] : ids.filter((x) => x !== s.id),
                      }))}
                      aria-label={`${s.name} in ${g.name}`} />
                    <span translate="no" className="min-w-0 truncate" title={s.name}>{s.name}</span>
                  </label>
                ))}
              </div>
            </li>
          );
        })}
      </ul>

      <div className="space-y-2 rounded-lg border border-dashed border-slate-300 p-3 text-sm">
        <p className="text-xs font-medium text-slate-600">New group</p>
        <input className="field max-w-[14rem] py-1.5 text-sm" placeholder="Group name (e.g. Group A)" value={name} maxLength={40}
          onChange={(e) => setName(e.target.value)} aria-label="New group name" data-testid="new-group-name" />
        <div className="grid gap-x-4 sm:grid-cols-2 lg:grid-cols-3">
          {allShops.map((s) => (
            <label key={s.id} className="flex min-h-[2.75rem] min-w-0 items-center gap-2 sm:min-h-[2rem]">
              <input type="checkbox" className="h-4 w-4 shrink-0" checked={members.includes(s.id)}
                onChange={(e) => setMembers((m) => (e.target.checked ? [...m, s.id] : m.filter((x) => x !== s.id)))} />
              <span translate="no" className="min-w-0 truncate" title={s.name}>{s.name}</span>
            </label>
          ))}
        </div>
        <button type="button" className="btn-secondary" disabled={busy || !name.trim()} data-testid="add-group"
          onClick={async () => {
            if (await run(() => api.createShopGroup(name.trim(), members))) {
              setName("");
              setMembers([]);
            }
          }}>
          <span translate="no">{members.length ? `Add group with ${members.length} shop${members.length === 1 ? "" : "s"}` : "Add group"}</span>
        </button>
      </div>
      {error && <p key="error" className="text-sm text-rose-600">{error}</p>}
    </div>
  );
}
