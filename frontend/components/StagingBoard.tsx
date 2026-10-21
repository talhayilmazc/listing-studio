"use client";

import { useRef, useState } from "react";
import { groupLabel, isUnsorted, place, type GroupingMode } from "@/lib/grouping";
import { listingsOf, type StagedFile, type StagedGroup } from "@/lib/staging";

/** A photo dragged within the page (not a file from the computer). */
const PHOTO = "application/x-listyro-photo";

/**
 * The upload page before anything is sent (B1): one card per listing the photos will
 * become, in the chosen grouping. Drag a photo onto another card to move it, onto
 * "+ New listing" or the empty space beside the cards to start one; drop new files on a
 * card to add them to it. Each card has a "+" to add photos (phones use it too) and each
 * photo a "Move to…" menu, so nothing needs dragging.
 */
export function StagingBoard({
  staged,
  mode,
  previews,
  idOf,
  onMove,
  onAdd,
  onRemove,
  disabled = false,
}: {
  staged: StagedGroup<File>[];
  mode: GroupingMode;
  previews: Map<File, string | null>;
  /** A stable id per staged file (for drag and drop within the page). */
  idOf: (file: File) => string;
  /** Photos moved onto a listing (``to`` null: a new listing). */
  onMove: (files: File[], to: string | null) => void;
  /** New files from the computer onto a listing (``to`` null: a new listing). */
  onAdd: (files: StagedFile<File>[], to: string | null) => void;
  onRemove: (key: string) => void;
  disabled?: boolean;
}) {
  const listings = listingsOf(staged, mode, place);
  const byId = new Map(staged.flatMap((g) => g.files.map((sf) => [idOf(sf.file), sf.file] as const)));
  const [over, setOver] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [pickFor, setPickFor] = useState<string | null | undefined>(undefined);

  // What a drop carries: photos of this page, or files from the computer (folders read later by the page).
  function dropped(e: React.DragEvent, to: string | null) {
    e.preventDefault();
    e.stopPropagation();
    setOver(null);
    if (disabled) return;
    const ids = e.dataTransfer.getData(PHOTO);
    if (ids) {
      const files = ids.split(",").map((id) => byId.get(id)).filter(Boolean) as File[];
      if (files.length) onMove(files, to);
      return;
    }
    const files = Array.from(e.dataTransfer.files).filter((f) => /\.(png|jpe?g|webp|gif|tiff?)$/i.test(f.name));
    if (files.length) onAdd(files.map((f) => ({ file: f, relpath: f.name })), to);
  }
  const target = (key: string | null) => ({
    onDragOver: (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setOver(key ?? "+new");
    },
    onDragLeave: () => setOver(null),
    onDrop: (e: React.DragEvent) => dropped(e, key),
  });
  const choose = (to: string | null) => {
    setPickFor(to);
    picker.current?.click();
  };

  return (
    <div
      // The empty space beside the cards starts a new listing too.
      className="grid gap-4 rounded-xl sm:grid-cols-2 xl:grid-cols-3"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => e.target === e.currentTarget && dropped(e, null)}
      data-testid="staging-board"
    >
      {listings.map((l) => {
        const label = isUnsorted(l.key) ? "Unsorted: drag these onto a listing" : l.key === "" ? "Photos without a folder" : groupLabel(l.key);
        return (
          <section
            key={l.key || "(root)"}
            {...target(l.key)}
            className={"card flex flex-col gap-2 p-3 " + (over === l.key ? "ring-2 ring-brand-500" : "")}
            data-testid="staged-listing"
            data-key={l.key}
          >
            <header className="flex items-center gap-2">
              <h2 className="min-w-0 flex-1 truncate font-display text-lg leading-tight text-slate-900" title={label}>{label}</h2>
              <span translate="no" className="text-xs tabular-nums text-slate-500">{`${l.files.length} photo${l.files.length === 1 ? "" : "s"}`}</span>
              <button type="button" className="btn-secondary h-8 w-8 p-0 text-base" aria-label={`Add photos to ${label}`}
                onClick={() => choose(l.key)} disabled={disabled} data-testid="add-to-listing">+</button>
              <button type="button" className="tap text-xs text-slate-500 underline" onClick={() => onRemove(l.key)} disabled={disabled}
                aria-label={`Remove ${label}`}>remove</button>
            </header>
            <ul className="grid grid-cols-4 gap-1.5">
              {l.files.map((sf) => (
                <Photo key={idOf(sf.file)} sf={sf} id={idOf(sf.file)} preview={previews.get(sf.file)} disabled={disabled}
                  listings={listings.filter((x) => x.key !== l.key && !isUnsorted(x.key)).map((x) => ({ key: x.key, label: x.key === "" ? "Photos without a folder" : groupLabel(x.key) }))}
                  onMove={(to) => onMove([sf.file], to)} />
              ))}
            </ul>
          </section>
        );
      })}
      <button
        type="button"
        {...target(null)}
        onClick={() => choose(null)}
        disabled={disabled}
        className={"flex min-h-[8rem] flex-col items-center justify-center rounded-xl border-2 border-dashed text-sm font-medium text-slate-600 " +
          (over === "+new" ? "border-brand-600 bg-brand-50" : "border-slate-300 bg-white")}
        data-testid="new-listing"
      >
        <span className="text-2xl" aria-hidden>+</span>
        <span>New listing</span>
        <span className="mt-0.5 text-xs font-normal text-slate-500">Drop photos here, or click to add</span>
      </button>
      <input
        ref={picker}
        type="file"
        multiple
        accept="image/png,image/jpeg,image/webp,image/gif,image/tiff"
        className="hidden"
        data-testid="listing-input"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length && pickFor !== undefined) onAdd(files.map((f) => ({ file: f, relpath: f.name })), pickFor);
        }}
      />
    </div>
  );
}

function Photo({
  sf, id, preview, listings, onMove, disabled,
}: {
  sf: StagedFile<File>;
  id: string;
  preview: string | null | undefined;
  listings: { key: string; label: string }[];
  onMove: (to: string | null) => void;
  disabled: boolean;
}) {
  return (
    <li
      draggable={!disabled}
      onDragStart={(e) => {
        e.dataTransfer.setData(PHOTO, id);
        e.dataTransfer.effectAllowed = "move";
      }}
      className="group relative aspect-square overflow-hidden rounded-md bg-slate-100"
      title={sf.file.name}
      data-testid="staged-photo"
    >
      {preview ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={preview} alt={sf.file.name} className="h-full w-full object-cover" draggable={false} decoding="async" />
      ) : (
        <span className={"flex h-full w-full items-center justify-center text-[10px] uppercase text-slate-400 " + (preview === undefined ? "animate-pulse" : "")}>
          {sf.file.name.split(".").pop()}
        </span>
      )}
      {/* Move without dragging (phones): always there, small. */}
      <select
        aria-label={`Move ${sf.file.name} to another listing`}
        className="absolute inset-x-0 bottom-0 h-6 w-full cursor-pointer appearance-none bg-slate-900/55 px-1 text-[10px] text-white opacity-90"
        value=""
        disabled={disabled}
        onChange={(e) => {
          const to = e.target.value;
          if (to) onMove(to === "+new" ? null : to.slice(1));
        }}
        data-testid="move-to"
      >
        <option value="">Move to…</option>
        {listings.map((l) => <option key={l.key || "(root)"} value={`=${l.key}`}>{l.label}</option>)}
        <option value="+new">New listing</option>
      </select>
    </li>
  );
}
