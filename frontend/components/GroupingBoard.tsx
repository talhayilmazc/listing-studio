"use client";

import { useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import {
  EMPTY,
  boardGroups,
  boardOrder,
  clickSelect,
  coversChanged,
  dragged,
  moveProblem,
  type BoardGroup,
  type Selection,
} from "@/lib/board";
import type { Asset, BatchDetail, Content } from "@/lib/types";

const UNSORTED = "~unsorted";
/** Thumbnails a card shows before "Show all": a 300-photo upload stays smooth. */
const PAGE = 24;

/**
 * The grouping board: every photo of the batch, by listing group, plus the
 * Unsorted tray. Drag photos (or a selection) onto another group with a mouse;
 * on a phone tap photos to select them and use "Move to…". A selection can also
 * become a new group; groups merge; a group's SKU is edited here.
 *
 * A group with a listing written keeps its text: moving photos changes only its
 * images. When its cover changes the page says the title and tags came from the
 * old cover and offers "Regenerate" on the group below.
 */
export function GroupingBoard({
  batchId,
  assets,
  contents,
  onChanged,
  open,
  onToggle,
}: {
  batchId: string;
  assets: Asset[];
  contents: Content[];
  /** Something moved: the page reloads the batch, its groups and listings. */
  onChanged: (batch: BatchDetail) => Promise<void> | void;
  open: boolean;
  onToggle: () => void;
}) {
  const writtenOn = useMemo(() => new Set(contents.map((c) => c.asset_id)), [contents]);
  const { tray, groups } = useMemo(() => boardGroups(assets, writtenOn), [assets, writtenOn]);
  const order = useMemo(() => boardOrder(tray, groups), [tray, groups]);
  const byId = useMemo(() => new Map(assets.map((a) => [a.id, a])), [assets]);

  const [sel, setSel] = useState<Selection>(EMPTY);
  const [dragIds, setDragIds] = useState<string[] | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [deleting, setDeleting] = useState(false);
  const pointer = useRef<string>("mouse");

  // Photos that went away (moved by another tab, deleted) drop out of the selection.
  const selected = sel.ids.filter((id) => byId.has(id));
  const selectedSet = new Set(selected);

  async function run(action: () => Promise<BatchDetail>, done: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const batch = await action();
      setSel(EMPTY);
      await onChanged(batch);
      setNotice(done);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  function move(ids: string[], to: string) {
    if (!ids.length) return;
    if (ids.every((id) => (byId.get(id)?.group_key ?? "") === to)) return;
    const problem = moveProblem(ids, to, groups);
    if (problem) {
      setError(problem);
      return;
    }
    const changed = coversChanged(ids, to, groups);
    const where = to === UNSORTED ? "Unsorted" : groups.find((g) => g.key === to)?.label ?? to;
    run(
      () => api.boardMove(batchId, ids, to),
      `Moved ${ids.length} photo${ids.length === 1 ? "" : "s"} to ${where}.` +
        (changed.length
          ? ` ${changed.join(", ")} has a new cover: its title and tags were written from the old one. Regenerate it below if they no longer fit.`
          : ""),
    );
  }

  function newGroup() {
    const name = newName.trim();
    if (!name || !selected.length) return;
    const problem = moveProblem(selected, name, groups);
    if (problem) {
      setError(problem);
      return;
    }
    run(async () => {
      const b = await api.boardMove(batchId, selected, name, true);
      setNewName("");
      return b;
    }, `New group ${name.toUpperCase()} with ${selected.length} photo${selected.length === 1 ? "" : "s"}.`);
  }

  async function removeSelected() {
    setBusy(true);
    setError(null);
    let last: BatchDetail | null = null;
    try {
      for (const id of selected) last = (await api.deleteImage(id)).batch;
      setDeleting(false);
      setSel(EMPTY);
      setNotice(`Deleted ${selected.length} photo${selected.length === 1 ? "" : "s"}.`);
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      if (last) await onChanged(last);
      setBusy(false);
    }
  }

  function onTile(e: React.MouseEvent, id: string) {
    setSel((s) =>
      clickSelect(s, id, { shift: e.shiftKey, toggle: e.ctrlKey || e.metaKey, touch: pointer.current === "touch" }, order),
    );
  }

  // Deleting empties these written groups: their listing goes with them.
  const emptiedWritten = groups.filter((g) => g.written && g.assets.every((a) => selectedSet.has(a.id)));
  const targets = [{ key: UNSORTED, label: "Unsorted" }, ...groups.map((g) => ({ key: g.key, label: g.label }))];

  return (
    <section className="card p-4" aria-label="Arrange photos into listings">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="text-sm font-medium text-slate-900">Arrange photos into listings</h2>
          <p className="text-xs text-slate-500">
            <span translate="no">{groups.length}</span>
            <span>{groups.length === 1 ? " group" : " groups"}</span>
            <span> · </span>
            <span translate="no">{tray.assets.length}</span>
            <span> unsorted</span>
          </p>
        </div>
        <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={onToggle} aria-expanded={open}>
          {open ? "Done arranging" : "Arrange photos"}
        </button>
      </div>

      {open && (
        <div key="board" className="mt-3 space-y-3">
          <p className="text-xs text-slate-500">
            With a mouse: click a photo to select it, Shift-click for a run, Ctrl/⌘-click to add one, and drag onto a
            group. On a phone: tap photos to select them, then &ldquo;Move to…&rdquo;. A group&apos;s first photo is its
            cover.
          </p>

          {selected.length > 0 && (
            <div
              key="actions"
              className="sticky bottom-0 z-10 -mx-4 flex flex-wrap items-center gap-2 border-y border-slate-200 bg-white/95 px-4 py-2 text-sm shadow-sm backdrop-blur sm:top-0 sm:bottom-auto"
            >
              <span className="font-medium text-slate-800" translate="no">
                <span>{selected.length}</span> selected
              </span>
              <label className="flex items-center gap-1.5 text-slate-600">
                <span>Move to</span>
                <select
                  className="field w-auto max-w-[12rem] py-1 text-sm"
                  value=""
                  disabled={busy}
                  onChange={(e) => e.target.value && move(selected, e.target.value)}
                  aria-label="Move the selected photos to"
                  data-testid="move-to"
                >
                  <option value="">Choose…</option>
                  {targets.map((t) => (
                    <option key={t.key} value={t.key}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </label>
              <form
                className="flex items-center gap-1.5"
                onSubmit={(e) => {
                  e.preventDefault();
                  newGroup();
                }}
              >
                <input
                  className="field w-32 py-1 text-sm"
                  placeholder="New SKU"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  aria-label="SKU of the new group"
                  maxLength={120}
                  disabled={busy}
                />
                <button type="submit" className="btn-secondary px-3 py-1.5 text-xs" disabled={busy || !newName.trim()}>
                  New group from selection
                </button>
              </form>
              <button
                type="button"
                className="btn-secondary border-rose-200 px-3 py-1.5 text-xs text-rose-700"
                onClick={() => setDeleting(true)}
                disabled={busy}
              >
                Delete
              </button>
              <button type="button" className="tap text-xs text-slate-500 underline" onClick={() => setSel(EMPTY)}>
                Clear
              </button>
            </div>
          )}

          {deleting && selected.length > 0 && (
            <div key="confirm" role="alertdialog" aria-label="Delete photos" className="space-y-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2.5 text-xs text-rose-900">
              <p className="font-medium">
                <span>Delete </span>
                <span translate="no">{selected.length}</span>
                <span><span>{selected.length === 1 ? " photo" : " photos"}</span>? It cannot be undone.</span>
              </p>
              {emptiedWritten.length > 0 && (
                <p key="written">
                  <span translate="no">{emptiedWritten.map((g) => g.label).join(", ")}</span>
                  <span> would have no photos left, so the group goes with the title, tags and description written for it.</span>
                </p>
              )}
              <p>Listings already on Etsy keep their photos.</p>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="rounded-md bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-50 max-sm:min-h-[2.75rem] max-sm:px-4 max-sm:text-sm"
                  onClick={removeSelected}
                  disabled={busy}
                >
                  {busy ? "Deleting…" : "Delete"}
                </button>
                <button type="button" className="btn-secondary px-3 py-1.5 text-xs" onClick={() => setDeleting(false)} disabled={busy}>
                  Keep them
                </button>
              </div>
            </div>
          )}

          {notice && (
            <p key="notice" role="status" translate="no" className="rounded-md bg-brand-50 px-3 py-2 text-xs text-brand-800">
              {notice}
            </p>
          )}
          {error && (
            <p key="error" role="alert" className="rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-800">
              {error}
            </p>
          )}

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {[tray, ...groups].map((g) => (
              <BoardCard
                key={g.key}
                group={g}
                others={groups.filter((x) => x.key !== g.key)}
                selected={selectedSet}
                dropping={over === g.key && dragIds !== null}
                busy={busy}
                onTilePointer={(type) => (pointer.current = type)}
                onTile={onTile}
                onDragStart={(id) => setDragIds(dragged({ ids: selected, anchor: sel.anchor }, id))}
                onDragEnd={() => {
                  setDragIds(null);
                  setOver(null);
                }}
                onDragOver={() => over !== g.key && setOver(g.key)}
                onDrop={() => {
                  if (dragIds) move(dragIds, g.key);
                  setDragIds(null);
                  setOver(null);
                }}
                onMerge={(into) =>
                  run(
                    () => api.boardMerge(batchId, g.key, into),
                    `Merged ${g.label} into ${groups.find((x) => x.key === into)?.label ?? into}.` +
                      (g.written ? " Its listing moved with it; its title and tags were written from its old cover." : ""),
                  )
                }
                onSku={(sku) => run(() => api.boardSku(batchId, g.key, sku), `SKU of ${g.label} set to ${sku.trim().toUpperCase()}.`)}
              />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function BoardCard({
  group,
  others,
  selected,
  dropping,
  busy,
  onTilePointer,
  onTile,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
  onMerge,
  onSku,
}: {
  group: BoardGroup;
  others: BoardGroup[];
  selected: Set<string>;
  dropping: boolean;
  busy: boolean;
  onTilePointer: (type: string) => void;
  onTile: (e: React.MouseEvent, id: string) => void;
  onDragStart: (id: string) => void;
  onDragEnd: () => void;
  onDragOver: () => void;
  onDrop: () => void;
  onMerge: (into: string) => void;
  onSku: (sku: string) => void;
}) {
  const tray = group.key === UNSORTED;
  const [all, setAll] = useState(false);
  const [editing, setEditing] = useState(false);
  const [sku, setSku] = useState(group.sku ?? "");
  const [merging, setMerging] = useState(false);
  const shown = all ? group.assets : group.assets.slice(0, PAGE);
  const hidden = group.assets.length - shown.length;

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        onDragOver();
      }}
      onDrop={(e) => {
        e.preventDefault();
        onDrop();
      }}
      data-testid={`board-${group.key}`}
      className={
        "rounded-lg border p-3 transition-colors [content-visibility:auto] " +
        (dropping ? "border-brand-500 bg-brand-50 " : tray ? "border-amber-200 bg-amber-50/60 " : "border-slate-200 bg-white ")
      }
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="min-w-0 truncate text-sm font-medium text-slate-900" title={group.key} translate="no">
          {group.label}
        </span>
        <span className="text-xs text-slate-500">
          <span translate="no">{group.assets.length}</span>
          <span>{group.assets.length === 1 ? " photo" : " photos"}</span>
        </span>
        {group.written && (
          <span key="written" className="rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] text-emerald-700">
            listing written
          </span>
        )}
        {!tray && !editing && (
          <span key="sku" className="flex items-center gap-1 text-xs text-slate-500">
            <span className="font-mono" translate="no">
              {group.sku ? `SKU ${group.sku}` : "no SKU"}
            </span>
            <button
              type="button"
              className="tap text-brand-700 underline"
              onClick={() => {
                setSku(group.sku ?? "");
                setEditing(true);
              }}
              disabled={busy}
            >
              Edit SKU
            </button>
          </span>
        )}
        {!tray && others.length > 0 && !editing && (
          <button key="merge" type="button" className="tap text-xs text-brand-700 underline" onClick={() => setMerging((v) => !v)} disabled={busy}>
            Merge into…
          </button>
        )}
      </div>

      {editing && (
        <form
          key="sku-form"
          className="mt-2 flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (sku.trim()) onSku(sku);
            setEditing(false);
          }}
        >
          <input
            className="field w-36 py-1 font-mono text-sm"
            value={sku}
            onChange={(e) => setSku(e.target.value)}
            aria-label={`SKU of ${group.label}`}
            maxLength={120}
            autoFocus
          />
          <button type="submit" className="btn-secondary px-3 py-1.5 text-xs" disabled={!sku.trim() || busy}>
            Save SKU
          </button>
          <button type="button" className="tap text-xs text-slate-500 underline" onClick={() => setEditing(false)}>
            Cancel
          </button>
        </form>
      )}

      {merging && (
        <label key="merge-pick" className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-slate-600">
          <span>Merge</span>
          <span translate="no">{group.label}</span>
          <span>into</span>
          <select
            className="field w-auto max-w-[12rem] py-1 text-sm"
            value=""
            onChange={(e) => {
              if (e.target.value) onMerge(e.target.value);
              setMerging(false);
            }}
            aria-label={`Merge ${group.label} into`}
          >
            <option value="">Choose a group…</option>
            {others.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      )}

      {group.assets.length === 0 ? (
        <p className="mt-2 rounded border border-dashed border-amber-300 px-3 py-4 text-center text-xs text-amber-800">
          Nothing unsorted. Drop photos here to take them out of a group.
        </p>
      ) : (
        <ul className="mt-2 grid grid-cols-[repeat(auto-fill,minmax(4rem,1fr))] gap-1.5" aria-label={`Photos in ${group.label}`}>
          {shown.map((a, i) => (
            <Tile
              key={a.id}
              asset={a}
              cover={!tray && i === 0}
              selected={selected.has(a.id)}
              disabled={busy}
              onPointer={onTilePointer}
              onClick={(e) => onTile(e, a.id)}
              onDragStart={() => onDragStart(a.id)}
              onDragEnd={onDragEnd}
            />
          ))}
        </ul>
      )}
      {hidden > 0 && (
        <button key="more" type="button" className="tap mt-2 text-xs text-brand-700 underline" onClick={() => setAll(true)}>
          <span>Show all </span>
          <span translate="no">{group.assets.length}</span>
        </button>
      )}
      {all && group.assets.length > PAGE && (
        <button key="less" type="button" className="tap mt-2 text-xs text-slate-500 underline" onClick={() => setAll(false)}>
          Show fewer
        </button>
      )}
    </div>
  );
}

function Tile({
  asset,
  cover,
  selected,
  disabled,
  onPointer,
  onClick,
  onDragStart,
  onDragEnd,
}: {
  asset: Asset;
  cover: boolean;
  selected: boolean;
  disabled: boolean;
  onPointer: (type: string) => void;
  onClick: (e: React.MouseEvent) => void;
  onDragStart: () => void;
  onDragEnd: () => void;
}) {
  const image = asset.status === "processed" && !asset.files_removed;
  return (
    <li>
      <button
        type="button"
        draggable={!disabled}
        onPointerDown={(e) => onPointer(e.pointerType)}
        onClick={onClick}
        onDragStart={(e) => {
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", asset.id); // Firefox starts no drag without data
          onDragStart();
        }}
        onDragEnd={onDragEnd}
        aria-pressed={selected}
        aria-label={asset.original_filename + (cover ? " (cover)" : "")}
        title={asset.original_filename}
        className={
          "relative block aspect-square w-full overflow-hidden rounded border-2 bg-slate-100 " +
          (selected ? "border-slate-900 ring-2 ring-slate-900/30" : cover ? "border-brand-500" : "border-transparent")
        }
      >
        {image ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={api.assetImage(asset.id, 112)}
            alt=""
            loading="lazy"
            decoding="async"
            draggable={false}
            className="h-full w-full object-cover"
          />
        ) : (
          <span className="flex h-full items-center justify-center p-1 text-center text-[10px] text-slate-500">
            {asset.files_removed ? "removed" : asset.status === "failed" ? "failed" : "…"}
          </span>
        )}
        {cover && (
          <span key="cover" className="absolute left-0.5 top-0.5 rounded bg-brand-600 px-1 text-[9px] font-medium text-white">
            Cover
          </span>
        )}
        {selected && (
          <span key="tick" className="absolute right-0.5 top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-slate-900 text-[10px] text-white" aria-hidden>
            ✓
          </span>
        )}
      </button>
    </li>
  );
}
