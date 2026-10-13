"use client";

import Link from "next/link";
import type { MatrixCell, MatrixColumn, MatrixRow, PublishPreview, ShopNeed } from "@/lib/types";
import { needAction, needsHeading } from "@/lib/shopNeeds";
import { ShopBadge } from "./ShopPicker";

import { Txt } from "@/components/Txt";
export const cellKey = (contentId: string, shopId: string) => `${contentId}|${shopId}`;

function name(row: MatrixRow): string {
  return row.title || row.original_filename || "Listing";
}

/** What a ticked or unticked cell says beside its checkbox. */
function cellText(cell: MatrixCell): { text: string; tone: string } {
  if (cell.state === "available") return cell.wanted ? { text: "create draft", tone: "text-slate-700" } : { text: "not sent", tone: "text-slate-400" };
  // Unavailable: its own text first (approve it), else what this shop lacks.
  return { text: cell.reason ?? "cannot go to this shop yet", tone: cell.wanted ? "text-amber-800" : "text-slate-400" };
}

/**
 * The approve step chooses shops, and only shops (customer feedback, item 3).
 * Listings down the side, the account's shops across: a ticked cell is a shop
 * the listing goes to. The ticks are kept on the listing, so they are the same
 * after a reload and before and after approval; by default a listing goes to
 * the shop it was written for.
 *
 * The profile is not chosen here. It is the one chosen at upload or on the
 * listing's group, shown read-only with a link back to change it there, and it
 * builds the draft in every ticked shop with that shop's own shipping, return
 * and processing settings. Ticking a shop the profile is not set up in sets it
 * up there as "Use in" does; what a shop still needs is listed under it before
 * anything is approved or created.
 */
export function PublishMatrix({
  preview,
  batchId,
  onToggle,
  onColumn,
  onSetup,
  disabled,
  busyShop,
}: {
  preview: PublishPreview;
  batchId: string;
  onToggle: (row: MatrixRow, cell: MatrixCell, on: boolean) => void;
  /** Tick or untick a shop for every listing. */
  onColumn: (column: MatrixColumn, on: boolean) => void;
  /** Set the listing's profile up in a shop, from a need that offers it. */
  onSetup?: (profileId: string, shopId: string) => void;
  disabled?: boolean;
  /** The shop whose ticks are being saved. */
  busyShop?: string | null;
}) {
  const { columns, rows } = preview;
  const cellOf = (row: MatrixRow, shop: string) => row.cells.find((c) => c.connection_id === shop);
  /** Listings that can still be ticked for the shop (no draft or live listing there yet). */
  const open = (shop: string) => rows.filter((r) => { const c = cellOf(r, shop); return c && c.state !== "draft" && c.state !== "live"; });
  const tickedIn = (shop: string) => open(shop).filter((r) => cellOf(r, shop)?.wanted).length;

  const needRow = (col: MatrixColumn, n: ShopNeed, i: number) => {
    const action = needAction(n);
    return (
      <li key={`${n.kind}-${i}`} className={n.blocking ? "text-amber-900" : "text-slate-600"} data-testid="shop-need">
        <span aria-hidden>{n.blocking ? "✕ " : "• "}</span>
        {n.profile_name && <span key="profile" className="font-medium" translate="no">{`${n.profile_name}: `}</span>}
        <span>{n.message}</span>
        <span translate="no" className="text-slate-500">{` (${n.listings} listing${n.listings === 1 ? "" : "s"})`}</span>
        {action === "setup" && n.profile_id && onSetup && (
          <button key="setup" type="button" className="tap ml-1 text-brand-700 underline" disabled={disabled}
            onClick={() => onSetup(n.profile_id!, col.connection_id)}>
            <span>Set it up in </span><span translate="no">{col.shop_name ?? "this shop"}</span>
          </button>
        )}
        {action === "profiles" && (
          <Link key="profiles" href="/profiles" className="tap ml-1 text-brand-700 underline">Finish on the Profiles page</Link>
        )}
        {action === "wait" && <span key="wait" className="ml-1 text-slate-500">This updates by itself.</span>}
      </li>
    );
  };

  const head = (col: MatrixColumn) => {
    const can = open(col.connection_id).length;
    const on = tickedIn(col.connection_id);
    return (
      <div className="space-y-1.5">
        <label className="flex min-h-[2rem] items-center gap-2 max-md:min-h-[2.75rem]">
          <input
            type="checkbox"
            className="h-4 w-4"
            disabled={disabled || can === 0 || busyShop === col.connection_id}
            checked={can > 0 && on === can}
            ref={(el) => {
              if (el) el.indeterminate = on > 0 && on < can;
            }}
            onChange={(e) => onColumn(col, e.target.checked)}
            aria-label={`All listings to ${col.shop_name ?? "this shop"}`}
            data-testid="shop-tick"
          />
          <ShopBadge name={col.shop_name} className="text-xs" />
        </label>
        <p translate="no" className="text-[11px] font-normal tabular-nums text-slate-500">
          {`${col.ticked} of ${rows.length} listings · ${col.drafts} draft${col.drafts === 1 ? "" : "s"} to create ≈ ${col.estimated_calls.toLocaleString()} requests`}
        </p>
      </div>
    );
  };

  const cellBody = (row: MatrixRow, col: MatrixColumn) => {
    const cell = cellOf(row, col.connection_id);
    if (!cell) return null;
    if (cell.state === "draft") return <span className="text-emerald-700">✓ draft there</span>;
    if (cell.state === "live") return <span className="text-emerald-700">✓ live there</span>;
    const said = cellText(cell);
    return (
      <label className="flex min-h-[2rem] items-start gap-2 py-1 max-md:min-h-[2.75rem]">
        <input
          type="checkbox"
          className="mt-0.5 h-4 w-4 shrink-0"
          checked={cell.wanted}
          disabled={disabled || busyShop === col.connection_id}
          onChange={(e) => onToggle(row, cell, e.target.checked)}
          aria-label={`${name(row)} to ${col.shop_name ?? "this shop"}`}
        />
        <span className={"min-w-0 " + said.tone}>
          <Txt>{said.text}</Txt>
        </span>
      </label>
    );
  };

  /** Read-only: the profile is chosen on the listing's group. */
  const profileOf = (row: MatrixRow) => (
    <p className="text-[11px] text-slate-500" data-testid="row-profile">
      <span>Profile: </span>
      <span className="font-medium text-slate-700" translate="no">{row.profile_name ?? "none chosen"}</span>
      <span> · </span>
      <Link href={`/batches/${batchId}#group-${encodeURIComponent(row.group_key ?? "")}`} className="tap text-brand-700 underline">
        change on its group
      </Link>
    </p>
  );

  const waiting = columns.filter((c) => c.needs.length > 0);

  return (
    <section className="card space-y-3 p-4 sm:p-5" aria-labelledby="matrix-heading">
      <div>
        <h2 id="matrix-heading" className="text-sm font-medium text-slate-800">
          Shops for these drafts
        </h2>
        <p className="mt-0.5 text-xs text-slate-500">
          Tick the shops each listing goes to; your ticks are kept. A listing is built with its own profile in every
          shop, with that shop&apos;s own shipping, return and processing settings. Nothing is created in a shop you
          have not ticked, and nothing is created until you press Create draft.
        </p>
      </div>

      {waiting.length > 0 && (
        <div key="needs" role="status" className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs" data-testid="shop-needs">
          <p className="font-medium text-amber-900">Before these go to every ticked shop</p>
          {waiting.map((col) => (
            <div key={col.connection_id}>
              <p className="flex flex-wrap items-center gap-1.5 text-slate-700">
                <ShopBadge name={col.shop_name} className="text-xs" />
                <span>{needsHeading(col.needs)}</span>
              </p>
              <ul className="mt-1 space-y-1 pl-1">{col.needs.map((n, i) => needRow(col, n, i))}</ul>
            </div>
          ))}
        </div>
      )}

      {/* Wide screens: the matrix. */}
      <div className="hidden max-h-[28rem] overflow-auto rounded-lg border border-slate-200 md:block">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0 z-10 bg-slate-50 text-left align-top text-slate-700">
            <tr>
              <th className="px-3 py-2 font-medium">Listing</th>
              {columns.map((col) => (
                <th key={col.connection_id} className="w-60 px-3 py-2 font-medium">{head(col)}</th>
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
                  {profileOf(row)}
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
              {profileOf(row)}
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
