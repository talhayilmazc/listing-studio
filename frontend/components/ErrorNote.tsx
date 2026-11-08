import Link from "next/link";
import type { ErrorAdvice } from "@/lib/types";

/**
 * An error, in the product's voice (app/onboarding-v1): what happened, the one thing that fixes it,
 * and the raw answer (Etsy's status and its own reason) under "Details". No apology, no code in the
 * sentence. The action comes from the server (backend etsy/advice.py): a page to open, or ``onAction``
 * for what the card does itself (try again, focus the title or tags).
 */
export function ErrorNote({
  message,
  advice,
  onAction,
  testId = "error-note",
}: {
  message: string;
  advice?: ErrorAdvice | null;
  onAction?: (action: string) => void;
  testId?: string;
}) {
  return (
    <div role="alert" className="rounded-[var(--radius-sm)] border border-rose-300 bg-rose-50 p-3 text-sm text-rose-900" data-testid={testId}>
      <p>{message}</p>
      {advice ? (
        <div key="act" className="mt-2 flex flex-wrap items-center gap-3">
          {advice.href ? (
            <Link key="go" href={advice.href} className="btn-secondary" data-testid="error-action">
              {advice.label}
            </Link>
          ) : onAction && advice.action !== "wait" && advice.action !== "shop_manager" ? (
            <button key="do" type="button" className="btn-secondary" onClick={() => onAction(advice.action)} data-testid="error-action">
              {advice.label}
            </button>
          ) : (
            <span key="say" className="font-medium">{advice.label}</span>
          )}
          {advice.details ? (
            <details key="details" className="text-xs text-rose-900/90">
              <summary className="tap cursor-pointer select-none underline">Details</summary>
              <p className="mt-1 break-words font-mono" translate="no">{advice.details}</p>
            </details>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
