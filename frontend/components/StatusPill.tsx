/* Badge primitive. Quiet tinted surface + hairline border rather than a solid
   block of colour, so a row of pills reads as information, not decoration.
   The status -> tone mapping is unchanged. */
const STYLES: Record<string, string> = {
  uploaded: "border-slate-200 bg-slate-50 text-slate-600",
  processing: "border-amber-200 bg-amber-50 text-amber-700",
  processed: "border-emerald-200 bg-emerald-50 text-emerald-700",
  ready: "border-emerald-200 bg-emerald-50 text-emerald-700",
  applied: "border-brand-100 bg-brand-50 text-brand-700",
  failed: "border-rose-200 bg-rose-50 text-rose-700",
  uploading: "border-amber-200 bg-amber-50 text-amber-700",
};

export function StatusPill({ status }: { status: string }) {
  const cls = STYLES[status] ?? "border-slate-200 bg-slate-50 text-slate-600";
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium leading-5 ${cls}`}
    >
      {status}
    </span>
  );
}
