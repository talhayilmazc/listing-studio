"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { Asset } from "@/lib/types";
import { CoverCropper } from "./CoverCropper";

/**
 * "Crop" on a photo (feature/photo-crop): opens the existing cover cropper (CoverCropper, a square
 * with pan and zoom) for that photo. The square is saved on the photo and used whenever the photo is
 * a listing's cover, in any shop: a new draft and Replace images upload it as photo 1. Nothing is sent
 * to Etsy here; a listing already on Etsy changes only when the seller replaces its images.
 *
 * ``cover``: the photo is the cover where it is shown; its button is the big one.
 */
export function CropButton({ assetId, batchId, cover, onSaved, className }: {
  assetId: string;
  batchId: string;
  cover: boolean;
  onSaved?: () => void;
  className?: string;
}) {
  const [asset, setAsset] = useState<Asset | null>(null);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function open() {
    setOpening(true);
    setError(null);
    try {
      const found = (await api.getBatch(batchId)).assets.find((a) => a.id === assetId) ?? null;
      if (!found || found.status !== "processed" || !found.width || !found.height) {
        setError("This photo cannot be cropped: its file is not there.");
      } else {
        setAsset(found);
      }
    } catch (e: any) {
      setError(e.message ?? String(e));
    } finally {
      setOpening(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className={(cover ? "btn-secondary px-3 py-1.5 text-sm " : "tap rounded bg-white/90 px-1.5 py-0.5 text-xs font-medium text-slate-800 shadow-sm ") + (className ?? "")}
        onClick={(e) => { e.stopPropagation(); open(); }}
        disabled={opening}
        title={cover ? "Crop the cover: the square Etsy shows" : "Crop: the square used when this photo is the cover"}
        aria-label={cover ? "Crop the cover" : "Crop this photo"}
        data-testid={cover ? "crop-cover" : "crop-photo"}
      >
        {opening ? "Opening…" : cover ? "Crop cover" : "Crop"}
      </button>
      {error ? <span key="error" role="alert" className="text-xs text-rose-700">{error}</span> : null}
      {asset ? (
        <div key="dialog" role="dialog" aria-modal="true" aria-label="Crop" className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4"
          onClick={(e) => { if (e.target === e.currentTarget) setAsset(null); }} data-testid="crop-dialog">
          <div className="max-h-full w-full max-w-xl overflow-y-auto rounded-[var(--radius-md)] bg-white p-4 shadow-[var(--shadow-lg)]">
            {!cover ? (
              <p key="note" className="mb-2 text-xs text-slate-600">This square is used when this photo is the cover.</p>
            ) : null}
            <CoverCropper
              key={asset.id}
              asset={asset}
              onCancel={() => setAsset(null)}
              onDone={() => {
                setAsset(null);
                onSaved?.();
              }}
            />
          </div>
        </div>
      ) : null}
    </>
  );
}
