"use client";

import type { MockupPhoto } from "@/lib/types";
import { COLOUR_LABEL, colourChip, type Colour } from "@/lib/variants";

/**
 * A template photo's colour group for light/dark design variants (backend pipeline/variants.py):
 * measured from the garment, changed with one click. A photo measured close to the line between
 * light and dark (or not measured yet) is marked for the seller to choose.
 */
export function ColourToggle({
  photo,
  onChoose,
  disabled,
  compact = false,
}: {
  photo: Pick<MockupPhoto, "name" | "colour" | "colour_source">;
  onChoose: (colour: Colour) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  const chip = colourChip(photo);
  return (
    <div className="space-y-1" data-testid="photo-colour" data-colour={photo.colour ?? "none"} data-ask={chip.ask ? "yes" : "no"}>
      {!compact && <span key="label" className="label">Colour group</span>}
      {chip.ask && (
        <p key="ask" className="text-xs font-medium text-amber-900" data-testid="photo-colour-ask">
          <span>{photo.colour ? "Close to the line between light and dark: choose one." : "Light or dark? Choose one."}</span>
        </p>
      )}
      <div className="grid grid-cols-2 gap-1" role="group" aria-label={`Colour group of ${photo.name}`}>
        {(["light", "dark"] as Colour[]).map((c) => {
          const on = photo.colour === c;
          return (
            <button key={c} type="button" aria-pressed={on} disabled={disabled} onClick={() => !on || chip.ask ? onChoose(c) : undefined}
              data-testid={`photo-colour-${c}`}
              className={
                "min-h-11 rounded-md border px-2 text-xs font-medium transition-colors " +
                (on && !chip.ask
                  ? c === "light" ? "border-slate-700 bg-white text-slate-900 ring-1 ring-slate-700" : "border-slate-900 bg-slate-900 text-white"
                  : on ? "border-amber-600 bg-amber-50 text-amber-900"
                  : "border-slate-300 bg-white text-slate-700 hover:border-slate-500")
              }>
              <span className={`mr-1.5 inline-block h-2.5 w-2.5 rounded-full border border-slate-400 align-middle ${c === "light" ? "bg-white" : "bg-slate-900"}`} aria-hidden />
              <span>{COLOUR_LABEL[c]}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
