import { liveChecks } from "@/lib/liveChecks";

/** The checks under a listing's title and tags, as the seller types (lib/liveChecks.ts). */
export function LiveChecks({ title, tags }: { title: string; tags: string[] }) {
  const { checks, brands } = liveChecks(title, tags);
  return (
    <div className="space-y-2" data-testid="live-checks" translate="no">
      <ul className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
        {checks.map((c) => (
          <li key={c.key} className={"flex items-start gap-1.5 " + (c.ok ? "text-slate-700" : "font-medium text-amber-900")} data-ok={c.ok}>
            <span aria-hidden className={c.ok ? "text-emerald-700" : "text-amber-700"}>{c.ok ? "✓" : "!"}</span>
            <span>{c.line}</span>
          </li>
        ))}
      </ul>
      {brands.length ? (
        <ul key="brands" className="space-y-1 rounded-[var(--radius-sm)] border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900" data-testid="live-brands">
          {brands.map((b) => <li key={b}>{b}</li>)}
        </ul>
      ) : null}
    </div>
  );
}
