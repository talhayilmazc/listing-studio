"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ItemOptions as Options, PropertyOptions, ShopSection } from "@/lib/types";
import { Sheet } from "./Sheet";
import { Txt } from "./Txt";

/**
 * Occasion, Holiday and Section for one listing, as in Etsy's listing editor
 * (Item Options, shop section). Occasion and Holiday offer only Etsy's own values
 * for the listing's category; Section is per shop, from that shop's own
 * sections. They go with the listing to every shop and are written on its drafts.
 */
export function ItemOptions({ contentId }: { contentId: string }) {
  const [options, setOptions] = useState<Options | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const polls = useRef(0);

  const load = useCallback(async () => {
    try {
      setOptions(await api.itemOptions(contentId));
    } catch (e: any) {
      setError(e.message ?? String(e));
    }
  }, [contentId]);

  useEffect(() => {
    load();
  }, [load]);

  // A shop's sections are read through the queue: look again until they are here.
  const waiting = options?.shops.some((s) => s.sections === null) ?? false;
  useEffect(() => {
    if (!waiting || polls.current > 20) return;
    const t = setTimeout(() => {
      polls.current += 1;
      load();
    }, 3000);
    return () => clearTimeout(t);
  }, [waiting, options, load]);

  async function save(body: Parameters<typeof api.setItemOptions>[1]) {
    setBusy(true);
    setError(null);
    try {
      setOptions(await api.setItemOptions(contentId, body));
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!options) return error ? <p className="text-xs text-rose-700">{error}</p> : null;
  return (
    <div className="space-y-3" data-testid="item-options">
      <p className="label">Item options and section</p>
      {options.note && (
        <p key="note" className="text-xs text-slate-500">
          {options.note}
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        {options.occasion && (
          <ValuePicker key="occasion" options={options.occasion} busy={busy}
            onSave={(values) => save({ occasion: values })} />
        )}
        {options.holiday && (
          <ValuePicker key="holiday" options={options.holiday} busy={busy}
            onSave={(values) => save({ holiday: values })} />
        )}
      </div>
      {options.shops.map((shop) => (
        <SectionRow
          key={shop.connection_id}
          shop={shop}
          contentId={contentId}
          busy={busy}
          many={options.shops.length > 1}
          onPick={(pick) => save({ sections: { [shop.connection_id]: pick } })}
          onCreated={() => {
            polls.current = 0;
            load();
          }}
        />
      ))}
      {error && (
        <p key="error" role="alert" className="text-xs text-rose-700">
          {error}
        </p>
      )}
    </div>
  );
}

const SOURCE: Record<PropertyOptions["source"], string> = {
  seller: "set by you",
  writer: "chosen by the writer",
  none: "",
};

/** A searchable list of Etsy's values; several only where Etsy takes several. */
function ValuePicker({
  options,
  busy,
  onSave,
}: {
  options: PropertyOptions;
  busy: boolean;
  onSave: (values: string[] | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string[]>(options.selected);
  useEffect(() => setPicked(options.selected), [options.selected]);
  const multi = options.max_values > 1;
  const shown = options.values.filter((v) => v.toLowerCase().includes(query.trim().toLowerCase()));
  const label = options.selected.length ? options.selected.join(", ") : "None";

  function choose(value: string) {
    if (!multi) {
      onSave([value]);
      setOpen(false);
      return;
    }
    setPicked((cur) => (cur.includes(value) ? cur.filter((v) => v !== value) : cur.length < options.max_values ? [...cur, value] : cur));
  }

  return (
    <div className="relative">
      <span className="block text-xs font-medium text-slate-600">{options.name}</span>
      <button
        type="button"
        className="field mt-1 flex w-full items-center justify-between gap-2 text-left text-sm"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        disabled={busy}
        data-testid={`pick-${options.name.toLowerCase()}`}
      >
        <span translate="no" className="truncate">{label}</span>
        <span aria-hidden className="text-slate-400">▾</span>
      </button>
      <span className="mt-0.5 block text-[11px] text-slate-500" translate="no">
        {[
          SOURCE[options.source],
          options.profile_default !== null ? `profile default: ${options.profile_default || "none"}` : "",
          multi ? `up to ${options.max_values}` : "",
        ]
          .filter(Boolean)
          .join(" · ")}
      </span>
      {open && (
        <Sheet key="sheet" title={options.name} onClose={() => setOpen(false)} width="w-72">
          <div className="space-y-2 p-3">
            <input
              className="field py-1.5 text-sm"
              placeholder={`Search ${options.name.toLowerCase()}`}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label={`Search ${options.name}`}
              autoFocus
            />
            <ul className="max-h-64 overflow-y-auto" role="listbox" aria-multiselectable={multi}>
              {shown.map((v) => {
                const on = (multi ? picked : options.selected).includes(v);
                const full = multi && !on && picked.length >= options.max_values;
                return (
                  <li key={v}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={on}
                      disabled={full}
                      onClick={() => choose(v)}
                      className={
                        "flex min-h-[44px] w-full items-center gap-2 rounded px-2 text-left text-sm sm:min-h-0 sm:py-1.5 " +
                        (on ? "bg-brand-50 text-brand-800" : "hover:bg-slate-50") +
                        (full ? " opacity-40" : "")
                      }
                    >
                      <span aria-hidden className="w-4">{on ? "✓" : ""}</span>
                      <span>{v}</span>
                    </button>
                  </li>
                );
              })}
              {shown.length === 0 && <li key="none" className="px-2 py-2 text-xs text-slate-500">No value matches.</li>}
            </ul>
            <div className="flex flex-wrap gap-2 border-t border-slate-100 pt-2">
              {multi && (
                <button key="save" type="button" className="btn-primary px-3 py-1.5 text-xs" onClick={() => { onSave(picked); setOpen(false); }}>
                  Save
                </button>
              )}
              <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => { onSave([]); setOpen(false); }}>
                None
              </button>
              {options.source === "seller" && (
                <button key="writer" type="button" className="tap text-xs text-brand-700 underline" onClick={() => { onSave(null); setOpen(false); }}>
                  Use the writer&apos;s choice
                </button>
              )}
            </div>
          </div>
        </Sheet>
      )}
    </div>
  );
}

const SECTION_SOURCE: Record<ShopSection["source"], string> = {
  seller: "your choice",
  carried: "",
  suggested: "suggested",
  none: "",
};

function SectionRow({
  shop,
  contentId,
  busy,
  many,
  onPick,
  onCreated,
}: {
  shop: ShopSection;
  contentId: string;
  busy: boolean;
  many: boolean;
  onPick: (pick: number | null | "default") => void;
  onCreated: () => void;
}) {
  const [asking, setAsking] = useState<{ title: string; message: string } | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function create(confirm: boolean) {
    if (!shop.missing_title) return;
    setFailure(null);
    try {
      const res = await api.createSection(shop.connection_id, shop.missing_title, [contentId], confirm);
      if (res.queued) {
        setAsking(null);
        setSent(res.message);
        onCreated();
      } else if (res.requests === 0) {
        setSent(res.message);
        onCreated();
      } else {
        setAsking({ title: res.title, message: res.message });
      }
    } catch (e: any) {
      setFailure(e.message ?? String(e));
    }
  }

  return (
    <div className="space-y-1" data-testid="section-row">
      <label className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-xs font-medium text-slate-600">
          <span>Section</span>
          {many && <span key="shop" translate="no">{` in ${shop.shop_name ?? "shop"}`}</span>}
        </span>
        {shop.sections === null ? (
          <span key="reading" className="text-xs text-slate-500" translate="no">
            Reading this shop&apos;s sections…
          </span>
        ) : (
          <select
            key="select"
            className="field w-auto max-w-full py-1.5 text-sm"
            value={shop.selected_id === null ? "none" : String(shop.selected_id)}
            disabled={busy}
            onChange={(e) => onPick(e.target.value === "none" ? null : Number(e.target.value))}
            aria-label={`Section in ${shop.shop_name ?? "shop"}`}
          >
            <option value="none">No section</option>
            {shop.sections.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}
              </option>
            ))}
          </select>
        )}
      </label>
      {shop.sections !== null && (
        <p key="why" className="text-[11px] text-slate-500" translate="no">
          <span>{[SECTION_SOURCE[shop.source], shop.reason].filter(Boolean).join(" · ")}</span>
          {shop.source === "seller" && (
            <button key="default" type="button" className="tap ml-2 text-brand-700 underline" onClick={() => onPick("default")} disabled={busy}>
              Use the suggestion
            </button>
          )}
        </p>
      )}
      {shop.missing_title && shop.sections !== null && !sent && (
        <div key="missing" className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
          <p>
            <span>No section “</span>
            <span translate="no">{shop.missing_title}</span>
            <span>” in </span>
            <span translate="no">{shop.shop_name ?? "this shop"}</span>
            <span>. Pick another above</span>
            <Txt>{shop.can_create ? ", or create it." : ", or create it in Shop Manager (this shop has not allowed the app to create sections)."}</Txt>
          </p>
          {shop.can_create && !asking && (
            <button key="ask" type="button" className="btn-secondary mt-2 px-3 py-1.5 text-xs" onClick={() => create(false)}>
              <span>Create section “</span>
              <span translate="no">{shop.missing_title}</span>
              <span>” in </span>
              <span translate="no">{shop.shop_name ?? "this shop"}</span>
            </button>
          )}
          {asking && (
            <div key="confirm" role="alertdialog" aria-label="Create section" className="mt-2 space-y-2">
              <p translate="no">{asking.message}</p>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn-primary px-3 py-1.5 text-xs" onClick={() => create(true)}>
                  Create it
                </button>
                <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setAsking(null)}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      {sent && (
        <p key="sent" role="status" translate="no" className="text-xs text-brand-800">
          {sent}
        </p>
      )}
      {failure && (
        <p key="failure" role="alert" className="text-xs text-rose-700">
          {failure}
        </p>
      )}
    </div>
  );
}
