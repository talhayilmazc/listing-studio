"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { ShopGroups } from "@/lib/types";
import { useShops } from "./ShopProvider";

/**
 * Shop groups (v8 §B): named groups of your shops; every shop of a group gets
 * the same listings. A shop is in at most one group. The same groups are edited
 * here, on the Shops page and on the Profiles page.
 */
export function ShopGroupsEditor({ onChange }: { onChange?: (g: ShopGroups) => void }) {
  const { refresh } = useShops();
  const [data, setData] = useState<ShopGroups | null>(null);
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState<Record<string, string>>({});
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
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  if (!data) return <p className="text-sm text-slate-400">{error ?? "Loading groups…"}</p>;
  const allShops = [...data.groups.flatMap((g) => g.shops), ...data.ungrouped];
  const groupOf = (shopId: string) => data.groups.find((g) => g.shops.some((s) => s.id === shopId))?.id ?? "";

  function move(shopId: string, to: string) {
    const from = groupOf(shopId);
    if (from === to) return;
    run(async () => {
      let out: ShopGroups | null = null;
      if (from) {
        const g = data!.groups.find((x) => x.id === from)!;
        out = await api.updateShopGroup(from, { connection_ids: g.shops.map((s) => s.id).filter((id) => id !== shopId) });
      }
      if (to) {
        const current = (out ?? data!).groups.find((x) => x.id === to)!;
        out = await api.updateShopGroup(to, { connection_ids: [...current.shops.map((s) => s.id), shopId] });
      }
      return out ?? data!;
    });
  }

  return (
    <div className="space-y-4">
      <ul className="space-y-2">
        {data.groups.map((g) => (
          <li key={g.id} className="rounded-lg border border-slate-200 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <input
                className="field max-w-[14rem] py-1 text-sm font-medium"
                value={renaming[g.id] ?? g.name}
                onChange={(e) => setRenaming((r) => ({ ...r, [g.id]: e.target.value }))}
                onBlur={() => {
                  const next = (renaming[g.id] ?? g.name).trim();
                  if (next && next !== g.name) run(() => api.updateShopGroup(g.id, { name: next }));
                }}
                aria-label={`Name of group ${g.name}`}
              />
              <span translate="no" className="text-xs text-slate-500">{`${g.shops.length} shop${g.shops.length === 1 ? "" : "s"}`}</span>
              <button type="button" className="tap ml-auto text-xs text-rose-600" disabled={busy}
                onClick={() => run(() => api.deleteShopGroup(g.id))}>
                Delete group
              </button>
            </div>
            <p translate="no" className="mt-1 text-xs text-slate-500">{g.shops.map((s) => s.name).join(", ") || "No shops yet"}</p>
          </li>
        ))}
      </ul>

      <div className="flex flex-wrap items-center gap-2">
        <input className="field max-w-[14rem] py-1.5 text-sm" placeholder="New group name (e.g. Group A)" value={name}
          onChange={(e) => setName(e.target.value)} aria-label="New group name" />
        <button type="button" className="btn-secondary" disabled={busy || !name.trim()}
          onClick={() => run(async () => {
            const out = await api.createShopGroup(name.trim(), []);
            setName("");
            return out;
          })}>
          Add group
        </button>
      </div>

      {data.groups.length > 0 && (
        <table key="members" className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="py-1 font-medium">Shop</th>
              <th className="py-1 font-medium">Group</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {allShops.map((s) => (
              <tr key={s.id}>
                <td translate="no" className="py-1.5 pr-2">{s.name}</td>
                <td className="py-1.5">
                  <select className="field py-1 text-sm" value={groupOf(s.id)} disabled={busy}
                    onChange={(e) => move(s.id, e.target.value)} aria-label={`Group of ${s.name}`}>
                    <option value="">No group</option>
                    {data.groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {error && <p key="error" className="text-sm text-rose-600">{error}</p>}
    </div>
  );
}
