"use client";

import { groupState, inOrder, toggleGroup, toggleShop } from "@/lib/shopTargets";
import type { ShopGroup } from "@/lib/types";
import { Txt } from "./Txt";

export interface TargetShop {
  id: string;
  name: string;
  /** A short state beside the shop ("set up", "needs a choice"...). */
  note?: string | null;
  /** Cannot be chosen here (the reason is the note). */
  disabled?: boolean;
}

/**
 * Pick where something goes: a shop group (every shop in it) or single shops.
 * Groups are shortcuts for their shops, so a shop in two groups is chosen once.
 * Nothing is chosen until the seller chooses: never all shops by default.
 */
export function ShopTargetPicker({
  shops,
  groups,
  value,
  onChange,
  disabled,
  label = "Where",
}: {
  shops: TargetShop[];
  groups: ShopGroup[];
  value: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  label?: string;
}) {
  const available = shops.filter((s) => !s.disabled).map((s) => s.id);
  const order = shops.map((s) => s.id);
  const usable = groups.filter((g) => g.shops.some((s) => available.includes(s.id)));
  return (
    <div className="space-y-2" data-testid="shop-target-picker">
      {usable.length > 0 && (
        <fieldset key="groups" className="space-y-1">
          <legend className="text-xs font-medium text-slate-600">{`${label}: a group`}</legend>
          <div className="flex flex-wrap gap-2">
            {usable.map((g) => {
              const ids = g.shops.map((s) => s.id);
              const state = groupState(value, ids, available);
              const usableIds = ids.filter((id) => available.includes(id));
              return (
                <button key={g.id} type="button" aria-pressed={state === "all"} disabled={disabled}
                  data-testid="target-group"
                  onClick={() => onChange(inOrder(toggleGroup(value, ids, available, state !== "all"), order))}
                  className={
                    "inline-flex min-h-[2.75rem] items-center gap-1.5 rounded-full border px-3 text-sm transition-colors sm:min-h-[2rem] " +
                    (state === "all"
                      ? "border-brand-600 bg-brand-50 font-medium text-brand-800"
                      : state === "some"
                        ? "border-brand-300 bg-white text-slate-800"
                        : "border-slate-300 bg-white text-slate-700 hover:border-slate-400")
                  }>
                  <span translate="no">{g.name}</span>
                  <span translate="no" className="text-xs text-slate-500">
                    {state === "some"
                      ? `${usableIds.filter((id) => value.includes(id)).length} of ${usableIds.length}`
                      : `${usableIds.length} shop${usableIds.length === 1 ? "" : "s"}`}
                  </span>
                </button>
              );
            })}
          </div>
        </fieldset>
      )}
      <fieldset key="shops" className="space-y-0.5">
        <legend className="text-xs font-medium text-slate-600">{usable.length > 0 ? "…or single shops" : `${label}: shops`}</legend>
        <div className="grid gap-x-4">
          {shops.map((s) => (
            <label key={s.id} className={"flex min-h-[2.75rem] min-w-0 items-center gap-2 text-sm sm:min-h-[2rem] " + (s.disabled ? "text-slate-400" : "")}>
              <input type="checkbox" className="h-4 w-4 shrink-0" checked={value.includes(s.id)} disabled={disabled || s.disabled}
                onChange={(e) => onChange(inOrder(toggleShop(value, s.id, e.target.checked), order))} data-testid="target-shop" />
              <span translate="no" className="min-w-0 truncate" title={s.name}>{s.name}</span>
              {s.note && <span key="note" className="shrink-0 text-xs text-slate-500"><Txt>{s.note}</Txt></span>}
            </label>
          ))}
        </div>
      </fieldset>
    </div>
  );
}
