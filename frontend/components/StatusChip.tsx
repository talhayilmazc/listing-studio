/**
 * One status word in one small box (B9): the same look on every page. ``tone`` says what the
 * seller should feel about it: work going on, done, needs a look, or failed.
 */
export type Tone = "neutral" | "working" | "done" | "attention" | "failed";

const TONES: Record<Tone, string> = {
  neutral: "border-slate-200 bg-slate-50 text-slate-700",
  working: "border-brand-100 bg-brand-50 text-brand-700",
  done: "border-emerald-200 bg-emerald-50 text-emerald-700",
  attention: "border-amber-200 bg-amber-50 text-amber-800",
  failed: "border-rose-200 bg-rose-50 text-rose-700",
};

export function StatusChip({ tone, children, title }: { tone: Tone; children: React.ReactNode; title?: string }) {
  return (
    <span translate="no" title={title} data-testid="status-chip"
      className={"inline-flex shrink-0 items-center rounded border px-1.5 text-[11px] leading-5 " + TONES[tone]}>
      {children}
    </span>
  );
}
