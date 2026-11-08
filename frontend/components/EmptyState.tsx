import Link from "next/link";

/**
 * An empty screen (app/onboarding-v1): one sentence saying what goes here, and one primary button
 * to the thing that fills it. Nothing else: no zeros, no filters for nothing.
 */
export function EmptyState({
  line,
  action,
  href,
  onAction,
  testId = "empty-state",
}: {
  line: string;
  action: string;
  href?: string;
  onAction?: () => void;
  testId?: string;
}) {
  return (
    <section className="card flex min-h-[11rem] flex-col items-center justify-center gap-4 px-6 py-10 text-center" data-testid={testId}>
      <p className="max-w-md text-base text-slate-700">{line}</p>
      {href ? (
        <Link key="go" href={href} className="btn-primary">
          {action}
        </Link>
      ) : (
        <button key="do" type="button" className="btn-primary" onClick={onAction}>
          {action}
        </button>
      )}
    </section>
  );
}

/** A list's place while it loads: rows of the size they will be, so nothing moves when they come. */
export function ListSkeleton({ rows = 3, height = "4.5rem" }: { rows?: number; height?: string }) {
  return (
    <div className="space-y-3" aria-hidden data-testid="list-loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="card animate-pulse bg-white" style={{ height }} />
      ))}
    </div>
  );
}
