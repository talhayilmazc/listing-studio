const STYLES: Record<string, string> = {
  uploaded: "bg-slate-100 text-slate-600",
  processing: "bg-amber-100 text-amber-700",
  processed: "bg-emerald-100 text-emerald-700",
  ready: "bg-emerald-100 text-emerald-700",
  applied: "bg-brand-100 text-brand-700",
  failed: "bg-rose-100 text-rose-700",
  uploading: "bg-amber-100 text-amber-700",
};

export function StatusPill({ status }: { status: string }) {
  const cls = STYLES[status] ?? "bg-slate-100 text-slate-600";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}
    >
      {status}
    </span>
  );
}
