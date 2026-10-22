"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import { announceProfilesChanged, panelSettings } from "@/lib/shopSetup";
import {
  COLUMN_LABELS,
  SETTING_COLUMNS,
  applyToAll,
  copyFrom,
  planResult,
  type Action,
  type Column,
  type Plan,
  type TableRow,
} from "@/lib/profileShopTable";
import type { ProfileSetup, ProfileShopTable as TableData } from "@/lib/types";
import { Txt } from "./Txt";
import { Help } from "./Help";
import { HowMadeBulk } from "./HowMadeBulk";

const POLL_MS = 3000;
const MAX_TRIES = 10;

/**
 * The Shops table on a profile card (B7): one row per shop the profile is used in, with the
 * shipping, returns and processing it uses there (by name; the account's choice, for every
 * profile using the same main-shop setting), the profile's section and title prefix there and
 * the shop's listings a day for spread-out schedules. Each value is changed in place; after a
 * change, "apply to all shops" gives it to every other shop (by name), and each row can
 * "Copy from shop…". Nothing is created or deleted on Etsy here.
 */
export function ProfileShopTable({
  profileId,
  profileName,
  onChanged,
  onClose,
  howMade,
}: {
  profileId: string;
  profileName: string;
  onChanged: () => void;
  onClose: () => void;
  /** The profile's How it's made answers: the bulk section under the table. */
  howMade?: { production: string | null; tools: string[] | null };
}) {
  const [setup, setSetup] = useState<ProfileSetup | null>(null);
  const [table, setTable] = useState<TableData | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  // The cell changed last: "apply to all shops" is offered next to it.
  const [last, setLast] = useState<{ shopId: string; column: Column } | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, t] = await Promise.all([api.profileSetup(profileId), api.profileShopTable(profileId)]);
      setSetup(s);
      setTable(t);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  }, [profileId]);

  useEffect(() => {
    load();
  }, [load]);

  // A shop whose lists are not read yet is read once per opening; sections arrive by themselves.
  const asked = useRef(new Set<string>());
  useEffect(() => {
    for (const shop of setup?.shops ?? []) {
      if (shop.lists_missing && shop.link.status !== "checking" && !asked.current.has(shop.connection_id)) {
        asked.current.add(shop.connection_id);
        api.checkShopAgain(profileId, shop.connection_id).then(load).catch(() => undefined);
      }
    }
  }, [setup, profileId, load]);
  const reading = (setup?.shops.some((s) => s.link.status === "checking" || s.lists_missing) ?? false) ||
    (table?.rows.some((r) => r.sections === null) ?? false);
  // Asked again a few times (about half a minute); after that the cell says it is not read yet.
  const [tries, setTries] = useState(0);
  const waiting = reading && tries < MAX_TRIES;
  useEffect(() => {
    if (!waiting) return;
    const t = setTimeout(() => {
      setTries((n) => n + 1);
      load();
    }, POLL_MS);
    return () => clearTimeout(t);
  }, [waiting, load, setup, table]);
  const pending = waiting ? "reading…" : "not read yet";

  const rows: TableRow[] = (table?.rows ?? []).map((r) => {
    const shop = setup?.shops.find((s) => s.connection_id === r.connection_id);
    const settings: TableRow["settings"] = {};
    if (shop && !shop.lists_missing) {
      for (const p of panelSettings(shop)) {
        if ((SETTING_COLUMNS as readonly string[]).includes(p.resource)) {
          settings[p.resource as (typeof SETTING_COLUMNS)[number]] = { value: p.value, options: p.options };
        }
      }
    }
    return {
      shopId: r.connection_id, shopName: r.shop_name, main: r.main, settings,
      sectionTitle: r.section_title, sections: r.sections, prefix: r.title_prefix, daily: r.daily_limit,
    };
  });

  async function run(action: Action) {
    if (action.kind === "pick") await api.pickInShop(profileId, action.shopId, action.resource, [action.id]);
    else if (action.kind === "section") await api.setProfileShopValue(profileId, action.shopId, "section_title", action.title, action.title === null);
    else if (action.kind === "prefix") await api.setProfileShopValue(profileId, action.shopId, "title_prefix", action.prefix);
    else await api.setShopDailyLimit(action.shopId, action.daily);
  }

  async function act(actions: Action[], plan?: Plan) {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      for (const a of actions) await run(a);
      await load();
      onChanged();
      announceProfilesChanged();
      if (plan) setResult(planResult(plan));
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function change(row: TableRow, column: Column, action: Action) {
    await act([action]);
    setLast({ shopId: row.shopId, column });
  }

  function applyAll() {
    if (!last) return;
    const source = rows.find((r) => r.shopId === last.shopId);
    if (!source) return;
    const plan = applyToAll(last.column, source, rows);
    act(plan.actions, plan);
    setLast(null);
  }

  function copy(to: TableRow, fromId: string) {
    const from = rows.find((r) => r.shopId === fromId);
    if (!from) return;
    const plan = copyFrom(from, to);
    act(plan.actions, plan);
  }

  const applyLink = (row: TableRow, column: Column) =>
    last && last.shopId === row.shopId && last.column === column && rows.length > 1 ? (
      <button key="apply" type="button" className="tap mt-0.5 block text-[11px] text-brand-700 underline" disabled={busy}
        onClick={applyAll} data-testid="apply-to-all">
        apply to all shops
      </button>
    ) : null;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // A dialog as wide as the screen allows: six columns do not fit a profile card.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 sm:p-6" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={`Shops for ${profileName}`}
        className="flex h-full w-full max-w-6xl flex-col overflow-y-auto bg-white p-4 text-xs shadow-xl sm:h-auto sm:max-h-[90vh] sm:rounded-lg"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="min-w-0 truncate text-sm font-semibold text-slate-900" translate="no">{`Shops for ${profileName}`}</h2>
          <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={onClose}>Close</button>
        </div>
        {table === null ? <p key="loading" className="text-slate-600">Loading…</p> : body()}
      </div>
    </div>,
    document.body,
  );

  function body() {
    return (
    <div className="space-y-2" data-testid="profile-shop-table">
      <div className="overflow-x-auto rounded-md border border-slate-200">
        <table className="w-full min-w-[56rem] text-left text-xs">
          <thead className="bg-slate-50 text-slate-600">
            <tr>
              <th className="px-2 py-1.5 font-medium">Shop</th>
              {(["shipping_profile", "return_policy", "readiness_state", "section", "prefix", "daily"] as Column[]).map((c) => (
                <th key={c} className="px-2 py-1.5 font-medium">{COLUMN_LABELS[c]}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.shopId} className="border-t border-slate-100 align-top" data-testid="profile-shop-row">
                <td className="px-2 py-1.5">
                  <span translate="no" className="block max-w-[10rem] truncate font-medium text-slate-800" title={row.shopName}>{row.shopName}</span>
                  {row.main && <span key="main" className="text-[10px] text-slate-500">main · reference listing</span>}
                  {rows.length > 1 && (
                    <select key="copy" className="field mt-1 w-full py-1 text-xs" value="" disabled={busy}
                      onChange={(e) => e.target.value && copy(row, e.target.value)} aria-label={`Copy settings to ${row.shopName} from`}
                      data-testid="copy-from">
                      <option value="">Copy from shop…</option>
                      {rows.filter((r) => r.shopId !== row.shopId).map((r) => <option key={r.shopId} value={r.shopId}>{r.shopName}</option>)}
                    </select>
                  )}
                </td>
                {SETTING_COLUMNS.map((c) => {
                  const s = row.settings[c];
                  return (
                    <td key={c} className="px-2 py-1.5">
                      {row.main ? (
                        <span key="ref" className="text-slate-600">the reference listing&apos;s</span>
                      ) : !s ? (
                        <span key="reading" translate="no" className="text-slate-600">{pending}</span>
                      ) : (
                        <select key="pick" className="field w-full py-1 text-xs" value={s.value ?? ""} disabled={busy}
                          onChange={(e) => e.target.value && change(row, c, { kind: "pick", shopId: row.shopId, resource: c, id: Number(e.target.value) })}
                          aria-label={`${COLUMN_LABELS[c]} in ${row.shopName}`}>
                          {s.value === null && <option key="none" value="">Choose…</option>}
                          {s.options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                        </select>
                      )}
                      {applyLink(row, c)}
                    </td>
                  );
                })}
                <td className="px-2 py-1.5">
                  {row.sections === null ? (
                    <span key="reading" translate="no" className="text-slate-600">{pending}</span>
                  ) : (
                    <select key="section" className="field w-full py-1 text-xs" value={row.sectionTitle ?? ""} disabled={busy}
                      onChange={(e) => change(row, "section", { kind: "section", shopId: row.shopId, title: e.target.value || null })}
                      aria-label={`Section in ${row.shopName}`}>
                      <option value="">Best fit for each design</option>
                      {row.sectionTitle && !row.sections.includes(row.sectionTitle) && (
                        <option key="gone" value={row.sectionTitle}>{`${row.sectionTitle} (not in this shop)`}</option>
                      )}
                      {row.sections.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                  )}
                  {applyLink(row, "section")}
                </td>
                <td className="px-2 py-1.5">
                  <PrefixInput value={row.prefix ?? ""} disabled={busy} label={`Title prefix in ${row.shopName}`}
                    onSave={(v) => change(row, "prefix", { kind: "prefix", shopId: row.shopId, prefix: v || null })} />
                  {applyLink(row, "prefix")}
                </td>
                <td className="px-2 py-1.5">
                  <select className="field w-full py-1 text-xs" value={row.daily ?? ""} disabled={busy}
                    onChange={(e) => change(row, "daily", { kind: "daily", shopId: row.shopId, daily: e.target.value ? Number(e.target.value) : null })}
                    aria-label={`Listings a day in ${row.shopName}`}>
                    <option value="">The schedule&apos;s</option>
                    {[1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50].map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                  {applyLink(row, "daily")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-slate-600">
        <span>Each value is used in that shop only.</span>
        <Help label="the Shops table">
          Shipping, returns and processing are each shop&apos;s own; a choice here is used by every profile with the same
          main-shop setting. &ldquo;A day&rdquo; is the shop&apos;s limit when a schedule spreads listings out, for every
          profile. &ldquo;apply to all shops&rdquo; and &ldquo;Copy from shop…&rdquo; go by name; a shop without that
          name is skipped and said.
        </Help>
      </p>
      {result && <p key="result" role="status" translate="no" className="text-xs text-slate-700" data-testid="table-result"><span>{result}</span></p>}
      {error && <p key="error" className="text-xs text-rose-700"><Txt>{error}</Txt></p>}
      {howMade && (
        <HowMadeBulk key="how-made" profileId={profileId} production={howMade.production} tools={howMade.tools} onChanged={onChanged} />
      )}
    </div>
    );
  }
}

/** A text box that saves when it loses focus or Enter is pressed, and only when it changed. */
function PrefixInput({ value, disabled, label, onSave }: { value: string; disabled: boolean; label: string; onSave: (v: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const save = () => {
    if (text.trim() !== value.trim()) onSave(text.trim());
  };
  return (
    <input className="field w-full py-1 text-xs" value={text} disabled={disabled} maxLength={60} aria-label={label}
      onChange={(e) => setText(e.target.value)} onBlur={save} onKeyDown={(e) => e.key === "Enter" && save()} placeholder="None" />
  );
}
