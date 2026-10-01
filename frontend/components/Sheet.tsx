"use client";

import { useEffect } from "react";

/**
 * What a picker opens: a popover under its button on a wide screen, a sheet
 * from the bottom edge on a phone (where a popover would hang off the side and
 * its rows would be too small to tap). One list, two presentations.
 */
export function Sheet({
  title,
  onClose,
  children,
  width = "w-[22rem]",
  align = "left",
}: {
  /** Shown at the top of the sheet on a phone. */
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  width?: string;
  align?: "left" | "right";
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      {/* Phone: dim the page; a tap outside closes. */}
      <div className="fixed inset-0 z-40 bg-slate-900/40 sm:hidden" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-label={title}
        className={
          "z-50 border-slate-200 bg-white shadow-lg " +
          // phone: bottom sheet
          "fixed inset-x-0 bottom-0 max-h-[80vh] overflow-hidden rounded-t-2xl border-t pb-[env(safe-area-inset-bottom)] " +
          // wide: popover
          `sm:absolute sm:inset-x-auto sm:bottom-auto sm:top-full sm:mt-1 sm:max-h-none sm:max-w-[90vw] sm:rounded-lg sm:border sm:pb-0 ${width} ` +
          "max-sm:!w-full " +
          (align === "right" ? "sm:right-0" : "sm:left-0")
        }
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3 sm:hidden">
          <p className="text-sm font-medium text-slate-900">{title}</p>
          <button type="button" onClick={onClose} className="-mr-2 rounded p-2 text-slate-500" aria-label="Close">
            ✕
          </button>
        </div>
        {children}
      </div>
    </>
  );
}
