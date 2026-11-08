"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { EXAMPLES_LINE, SET_UP, doneLine, offersExamples, stateOf, viewOf } from "@/lib/onboarding";
import { WORDS } from "@/lib/words";
import type { Onboarding } from "@/lib/types";

/**
 * The first-run checklist at the top of Home (app/onboarding-v1): connect your shop, upload your
 * first design, review and approve, send or schedule it. Stored on the server and ticked by what
 * the seller did (GET /api/onboarding); each step's button goes straight to where it is done. Once
 * all four are done it is one "You're set up" note the seller can dismiss.
 */
export function SetupChecklist() {
  const [data, setData] = useState<Onboarding | null>(null);
  const [failed, setFailed] = useState(false);
  const [opening, setOpening] = useState(false);
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    api.onboarding().then((o) => !cancelled && setData(o)).catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, []);

  async function tryExamples() {
    setOpening(true);
    try {
      const { batch_id } = await api.tryExamples();
      router.push(`/batches/${batch_id}/review`);
    } catch {
      setOpening(false);
      setFailed(true);
    }
  }

  if (failed && data === null) return null; // the rest of Home still works; nothing guessed here
  if (data === null) {
    // The card's own size while it loads, so nothing under it moves.
    return <div className="card h-[21rem] animate-pulse bg-white sm:h-[15rem]" aria-hidden data-testid="checklist-loading" />;
  }
  const view = viewOf(data);
  if (view === "none") return null;
  if (view === "set_up") {
    return (
      <section className="card flex items-center gap-3 p-4" data-testid="set-up-note" aria-label="Setup">
        <span aria-hidden className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-emerald-800">✓</span>
        <p className="min-w-0 flex-1 text-sm text-slate-800">{SET_UP}</p>
        <button
          type="button"
          className="btn-quiet shrink-0"
          onClick={async () => {
            await api.dismissOnboarding().catch(() => undefined);
            setData({ ...data, dismissed: true });
          }}
        >
          Dismiss
        </button>
      </section>
    );
  }

  return (
    <section className="card p-5 sm:p-6" data-testid="setup-checklist" aria-labelledby="setup-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="setup-title" className="text-lg font-bold text-slate-900">Get your first listing ready</h2>
        <p className="text-sm text-slate-600" translate="no">{doneLine(data)}</p>
      </div>
      {/* Reserved: a usage line goes here later (trial or plan usage). Nothing is shown today. */}
      <UsageLine />
      <ol className="mt-4 grid gap-3 lg:grid-cols-4">
        {data.steps.map((step, n) => {
          const state = stateOf(step, data);
          return (
            <li
              key={step.key}
              data-testid={`step-${step.key}`}
              data-state={state}
              className={
                "flex flex-col rounded-[var(--radius-sm)] border p-4 " +
                (state === "now" ? "border-ink bg-white shadow-[var(--shadow-md)]" : state === "done" ? "border-emerald-200 bg-emerald-50/60" : "border-slate-200 bg-white")
              }
            >
              <p className="flex items-center gap-2 font-semibold text-slate-900">
                <span
                  aria-hidden
                  className={
                    "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold " +
                    (state === "done" ? "bg-emerald-700 text-white" : state === "now" ? "bg-[var(--tag)] text-ink" : "bg-slate-100 text-slate-700")
                  }
                >
                  {state === "done" ? "✓" : n + 1}
                </span>
                <span>{step.title}</span>
                {state === "done" ? <span key="done" className="sr-only"> (done)</span> : null}
              </p>
              <p className="mt-1.5 text-sm text-slate-600">{step.body}</p>
              {state === "now" ? (
                <div key="go" className="mt-auto pt-3">
                  <Link href={step.href} className="btn-primary w-full" data-testid="checklist-next">
                    {step.action}
                  </Link>
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>
      {offersExamples(data) ? (
        <p key="examples" className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-slate-700">
          <span>{EXAMPLES_LINE}</span>
          {data.example_batch_id ? (
            <Link key="open" href={`/batches/${data.example_batch_id}/review`} className="btn-secondary">
              Open the examples
            </Link>
          ) : (
            <button key="try" type="button" className="btn-secondary" disabled={opening} onClick={tryExamples} data-testid="try-examples">
              {opening ? "Opening…" : WORDS.examples}
            </button>
          )}
        </p>
      ) : null}
    </section>
  );
}

/**
 * The checklist's usage line: kept empty on purpose. When trial or plan usage is shown later
 * ("7 of 10 free listings left"), it is rendered here and nowhere else in the checklist.
 */
function UsageLine(): null {
  return null;
}
