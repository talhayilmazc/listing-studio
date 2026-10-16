"use client";

import { useRef, useState } from "react";
import { filesFromDrop } from "@/lib/fileDrop";

/**
 * Many files at once: drop files or a whole folder here, or choose them ("Choose files",
 * "Choose a folder"). Every button is at least 44 px; nothing is sent from here (the page
 * sends each file with its own progress and error).
 */
export function FileDrop({
  onFiles,
  disabled,
  label,
  hint,
  zip = false,
  testId,
}: {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
  label: string;
  hint: string;
  /** Accept ZIPs too (designs). */
  zip?: boolean;
  testId?: string;
}) {
  const [over, setOver] = useState(false);
  const files = useRef<HTMLInputElement>(null);
  const folder = useRef<HTMLInputElement>(null);
  const accept = `image/png,image/jpeg,image/webp${zip ? ",.zip,application/zip" : ""}`;

  function take(list: FileList | null, input: HTMLInputElement | null) {
    const chosen = Array.from(list ?? []);
    if (input) input.value = "";
    if (chosen.length) onFiles(chosen);
  }

  return (
    <div
      className={`rounded-lg border-2 border-dashed p-4 text-center transition-colors ${over ? "border-brand-500 bg-brand-50" : "border-slate-300 bg-white"}`}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={async (e) => {
        e.preventDefault();
        setOver(false);
        if (disabled) return;
        const dropped = await filesFromDrop(e.dataTransfer);
        if (dropped.length) onFiles(dropped);
      }}
      data-testid={testId}
    >
      <p className="text-sm font-medium text-slate-700">{label}</p>
      <p className="mt-1 text-xs text-slate-500">{hint}</p>
      <div className="mt-3 flex flex-wrap justify-center gap-2">
        <input ref={files} type="file" multiple accept={accept} className="hidden" disabled={disabled}
          onChange={(e) => take(e.target.files, e.target)} data-testid={testId ? `${testId}-files` : undefined} />
        <input ref={folder} type="file" multiple className="hidden" disabled={disabled}
          // @ts-expect-error non-standard folder-select attributes
          webkitdirectory="" directory=""
          onChange={(e) => take(e.target.files, e.target)} />
        <button type="button" className="btn-secondary min-h-11" onClick={() => files.current?.click()} disabled={disabled}>
          {zip ? "Choose files or a ZIP" : "Choose files"}
        </button>
        <button type="button" className="btn-secondary min-h-11" onClick={() => folder.current?.click()} disabled={disabled}>
          Choose a folder
        </button>
      </div>
    </div>
  );
}
