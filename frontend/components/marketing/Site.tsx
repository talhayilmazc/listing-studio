import Link from "next/link";

/** The public site's frame: static, no session, no app code. */

// Exact Etsy API Terms of Use notice — must not be altered or abbreviated.
export const TRADEMARK_NOTICE =
  "The term 'Etsy' is a trademark of Etsy, Inc. This application uses the Etsy API but is not endorsed or certified by Etsy, Inc.";

export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <Link href="/" className={"tap inline-flex items-center gap-2.5 " + className} aria-label="Listyro home">
      <span aria-hidden className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 font-display text-xl leading-none text-white">
        L
      </span>
      <span className="font-display text-[1.7rem] leading-none text-slate-900">Listyro</span>
    </Link>
  );
}

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200/70 bg-stone-50/85 backdrop-blur">
      <div className="mx-auto flex h-16 w-full max-w-[1240px] items-center justify-between gap-4 px-4 sm:px-6">
        <Wordmark />
        <nav aria-label="Site" className="hidden items-center gap-7 text-sm text-slate-600 md:flex">
          <Link href="/#how-it-works" className="hover:text-slate-900">How it works</Link>
          <Link href="/#your-data" className="hover:text-slate-900">Your data</Link>
          <Link href="/pricing" className="hover:text-slate-900">Pricing</Link>
        </nav>
        <div className="flex items-center gap-1.5 sm:gap-2">
          <Link href="/login" className="btn-ghost">Sign in</Link>
          <Link href="/contact" className="btn-primary">Request an invite</Link>
        </div>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="border-t border-slate-200 bg-white">
      <div className="mx-auto w-full max-w-[1240px] space-y-6 px-4 py-10 text-sm text-slate-500 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-x-8 gap-y-4">
          <Wordmark />
          <nav aria-label="Footer" className="tap-row flex flex-wrap items-center gap-x-6 gap-y-2">
            <Link href="/pricing" className="tap hover:text-slate-800">Pricing</Link>
            <Link href="/terms" className="tap hover:text-slate-800">Terms of Service</Link>
            <Link href="/privacy" className="tap hover:text-slate-800">Privacy Policy</Link>
            <Link href="/contact" className="tap hover:text-slate-800">Contact</Link>
          </nav>
        </div>
        <p className="max-w-3xl leading-relaxed">{TRADEMARK_NOTICE}</p>
      </div>
    </footer>
  );
}
