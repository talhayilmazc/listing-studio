"use client";

import { useState } from "react";

/**
 * A secret shown exactly once — an invite code or a temporary password. The
 * server keeps only a hash, so this panel is the only chance to copy it.
 */
export function Reveal({
  title,
  value,
  detail,
  onDismiss,
}: {
  title: string;
  value: string;
  detail?: React.ReactNode;
  onDismiss: () => void;
}) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard needs a secure context; fall back to selecting the text.
      const el = document.getElementById("reveal-value") as HTMLInputElement | null;
      el?.select();
    }
  }

  return (
    <div role="status" className="rounded-xl border border-brand-100 bg-brand-50 p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-900">{title}</p>
          {detail && <p className="mt-0.5 text-xs text-slate-500">{detail}</p>}
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="shrink-0 text-xs text-slate-500 hover:text-slate-800"
        >
          Done
        </button>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <input
          id="reveal-value"
          readOnly
          value={value}
          onFocus={(e) => e.currentTarget.select()}
          className="field flex-1 bg-white font-mono text-sm"
          aria-label={title}
        />
        <button type="button" onClick={copy} className="btn-primary shrink-0">
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <p className="mt-2 text-xs text-amber-800">
        Shown once. Only a hash is stored, so it cannot be displayed again.
      </p>
    </div>
  );
}

/** Small inline confirmation, instead of a blocking browser dialog. */
export function Confirm({
  label,
  confirmLabel,
  tone = "neutral",
  disabled,
  disabledReason,
  onConfirm,
}: {
  label: string;
  confirmLabel: string;
  tone?: "neutral" | "danger";
  disabled?: boolean;
  disabledReason?: string;
  onConfirm: () => Promise<void> | void;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!asking) {
    return (
      <button
        type="button"
        onClick={() => setAsking(true)}
        disabled={disabled}
        title={disabled ? disabledReason : undefined}
        className="rounded-md px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-100 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
      >
        {label}
      </button>
    );
  }
  return (
    <span className="inline-flex items-center gap-1">
      <button
        type="button"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await onConfirm();
          } finally {
            setBusy(false);
            setAsking(false);
          }
        }}
        className={
          "rounded-md px-2 py-1 text-xs font-medium text-white disabled:opacity-60 " +
          (tone === "danger" ? "bg-rose-600 hover:bg-rose-700" : "bg-brand-600 hover:bg-brand-700")
        }
      >
        {busy ? "…" : confirmLabel}
      </button>
      <button
        type="button"
        onClick={() => setAsking(false)}
        className="rounded-md px-2 py-1 text-xs text-slate-500 hover:text-slate-800"
      >
        Cancel
      </button>
    </span>
  );
}
