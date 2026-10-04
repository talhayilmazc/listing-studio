"use client";

import type { MatrixCell, MatrixColumn, MatrixRow, PublishPreview } from "@/lib/types";
import { ShopBadge } from "./ShopPicker";

import { Txt } from "@/components/Txt";
export const cellKey = (contentId: string, shopId: string) => `${contentId}|${shopId}`;

function name(row: MatrixRow): string {
  return row.title || row.original_filename || "Listing";
}

/** What one cell says when it is not a checkbox. */
function CellState({ cell }: { cell: MatrixCell }) {
  if (cell.state === "draft") return <span className="text-emerald-700">✓ draft there</span>;
  if (cell.state === "live") return <span className="text-emerald-700">✓ live there</span>;
  return (
    <span className="text-amber-800">
      <span aria-hidden>✕ </span>
      <span>{cell.reason ?? "cannot go to this shop"}</span>
    </span>
  );
}

/**
 * Which listing becomes a draft in which shop (Priority 2). Listings down the
 * side, the account's shops across. A ticked cell will be created; a cell that
 * already has a draft or is live says so; one that cannot be created says why.
 * By default each listing goes only to the shop it was written for: another
 * shop gets a draft only where the seller ticks it. Each shop's drafts are
 * built from that shop's own profile, chosen in its column.
 */
export function PublishMatrix({
  preview,
  profileFor,
  onProfile,
  onToggle,
  onColumn,
  disabled,
}: {
  preview: PublishPreview;
  /** The profile chosen for a shop's column; unset = the matching one. */
  profileFor: Record<string, string | null>;
  onProfile: (shopId: string, profileId: string | null) => void;
  onToggle: (row: MatrixRow, cell: MatrixCell, on: boolean) => void;
  /** Tick or untick every available cell of a shop. */
  onColumn: (column: MatrixColumn, on: boolean) => void;
  disabled?: boolean;
}) {
  const { columns, rows } = preview;
  const cellOf = (row: MatrixRow, shop: string) => row.cells.find((c) => c.connection_id === shop);
  const available = (shop: string) => rows.filter((r) => cellOf(r, shop)?.state === "available");
  const chosenIn = (shop: string) => available(shop).filter((r) => cellOf(r, shop)?.chosen).length;

  const head = (col: MatrixColumn) => {
    const can = available(col.connection_id).length;
    const on = chosenIn(col.connection_id);
    const needsChoice = rows.some((r) => cellOf(r, col.connection_id)?.reason?.includes("choose one"));
    return (
      <div className="space-y-1.5">
        <label className="flex min-h-[2rem] items-center gap-2 max-md:min-h-[2.75rem]">
          <input
            type="checkbox"
            className="h-4 w-4"
            disabled={disabled || can === 0}
            checked={can > 0 && on === can}
            ref={(el) => {
              if (el) el.indeterminate = on > 0 && on < can;
            }}
            onChange={(e) => onColumn(col, e.target.checked)}
            aria-label={`All listings to ${col.shop_name ?? "this shop"}`}
          />
          <ShopBadge name={col.shop_name} className="text-xs" />
        </label>
        {col.profiles.length > 0 ? (
          <select
            className="field w-full py-1 text-xs font-normal"
            aria-label={`Profile for ${col.shop_name ?? "this shop"}`}
            value={profileFor[col.connection_id] ?? ""}
            disabled={disabled}
            onChange={(e) => onProfile(col.connection_id, e.target.value || null)}
          >
            <option value="">{needsChoice ? "Choose a profile…" : "Matching profile"}</option>
            {col.profiles.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.is_fresh ? "" : " (needs refresh)"}
              </option>
            ))}
          </select>
        ) : (
          <p className="text-[11px] font-normal text-amber-800">No confirmed profile in this shop.</p>
        )}
        <p translate="no" className="text-[11px] font-normal tabular-nums text-slate-500">
          {`${col.drafts} draft${col.drafts === 1 ? "" : "s"} ≈ ${col.estimated_calls.toLocaleString()} requests`}
        </p>
      </div>
    );
  };

  const cellBody = (row: MatrixRow, col: MatrixColumn) => {
    const cell = cellOf(row, col.connection_id);
    if (!cell) return null;
    if (cell.state !== "available") return <CellState cell={cell} />;
    return (
      <label className="flex min-h-[2rem] items-center gap-2 max-md:min-h-[2.75rem]">
        <input
          type="checkbox"
          className="h-4 w-4"
          checked={cell.chosen}
          disabled={disabled}
          onChange={(e) => onToggle(row, cell, e.target.checked)}
          aria-label={`${name(row)} to ${col.shop_name ?? "this shop"}`}
        />
        <span className="min-w-0 truncate text-slate-600">
          <span>{cell.chosen ? "create draft" : "not sent"}</span>
          <Txt>{cell.profile_name ? ` · ${cell.profile_name}` : ""}</Txt>
        </span>
      </label>
    );
  };

  return (
    <section className="card space-y-3 p-4 sm:p-5" aria-labelledby="matrix-heading">
      <div>
        <h2 id="matrix-heading" className="text-sm font-medium text-slate-800">
          Where drafts will be created
        </h2>
        <p className="mt-0.5 text-xs text-slate-500">
          Each listing goes to the shop it was written for unless you tick another. A shop builds its draft from its own
          profile. Nothing is created in a shop you have not ticked.
        </p>
      </div>

      {/* Wide screens: the matrix. */}
      <div className="hidden max-h-[28rem] overflow-auto rounded-lg border border-slate-200 md:block">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 z-10 bg-slate-50 text-left align-top text-slate-700">
            <tr>
              <th className="px-3 py-2 font-medium">Listing</th>
              {columns.map((col) => (
                <th key={col.connection_id} className="w-56 px-3 py-2 font-medium">{head(col)}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row) => (
              <tr key={row.content_id} className="align-top">
                <td className="max-w-0 px-3 py-2">
                  <p className="truncate text-slate-800" title={name(row)}>{name(row)}</p>
                  <p className="truncate text-[11px] text-slate-400">
                    <Txt>{row.group_key ? `${row.group_key} · ` : ""}</Txt>
                    <span>written for </span>
                    <span translate="no">{columns.find((c) => c.connection_id === row.own_connection_id)?.shop_name ?? "no shop"}</span>
                  </p>
                </td>
                {columns.map((col) => (
                  <td key={col.connection_id} className="px-3 py-1.5">{cellBody(row, col)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Phones: the same cells, one listing at a time; the shop controls first. */}
      <div className="space-y-3 md:hidden">
        <ul className="space-y-2">
          {columns.map((col) => (
            <li key={col.connection_id} className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs">{head(col)}</li>
          ))}
        </ul>
        <ul className="max-h-[60vh] divide-y divide-slate-100 overflow-y-auto rounded-lg border border-slate-200">
          {rows.map((row) => (
            <li key={row.content_id} className="space-y-1.5 p-3 text-xs">
              <p className="line-clamp-2 text-sm text-slate-800">{name(row)}</p>
              {columns.map((col) => (
                <div key={col.connection_id} className="flex items-start gap-2">
                  <ShopBadge name={col.shop_name} className="mt-1.5 shrink-0" />
                  <div className="min-w-0 flex-1">{cellBody(row, col)}</div>
                </div>
              ))}
            </li>
          ))}
        </ul>
      </div>

      <p translate="no" className={"text-xs " + (preview.fits ? "text-slate-600" : "font-medium text-amber-800")}>
        {preview.fits
          ? `${preview.drafts} draft${preview.drafts === 1 ? "" : "s"} in all ≈ ${preview.estimated_calls.toLocaleString()} Etsy requests (about ${preview.calls_per_draft} each). ` +
            (preview.ceiling
              ? `Etsy requests today: ${preview.ceiling.remaining.toLocaleString()} of your ${preview.ceiling.limit.toLocaleString()} left, resets ${preview.ceiling.resets_label}.`
              : `${preview.budget_remaining.toLocaleString()} Etsy requests are left today.`)
          : // Does not fit: the server's sentence says which number is in the way (their own, or the app's shared budget).
            preview.message}
      </p>
    </section>
  );
}
