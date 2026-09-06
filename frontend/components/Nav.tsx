import Link from "next/link";
import { QuotaBadge } from "./QuotaBadge";
import { ConnectionBadge } from "./ConnectionBadge";

export function Nav() {
  return (
    <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
        <Link href="/" className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
            LS
          </span>
          <span className="text-base font-semibold text-slate-900">Listing Studio</span>
        </Link>
        <nav className="flex items-center gap-3">
          <Link
            href="/dashboard"
            className="hidden text-sm text-slate-600 hover:text-slate-900 sm:block"
          >
            Dashboard
          </Link>
          <Link href="/" className="hidden text-sm text-slate-600 hover:text-slate-900 sm:block">
            Batches
          </Link>
          <Link
            href="/profiles"
            className="hidden text-sm text-slate-600 hover:text-slate-900 sm:block"
          >
            Profiles
          </Link>
          <ConnectionBadge />
          <QuotaBadge />
          <Link href="/upload" className="btn-primary">
            New upload
          </Link>
        </nav>
      </div>
    </header>
  );
}
