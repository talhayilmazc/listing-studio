import type { Metadata } from "next";
import { SiteFooter, SiteHeader } from "@/components/marketing/Site";

export const metadata: Metadata = {
  title: { absolute: "Page not found | Listyro" },
  description: "This page does not exist. Find what you were looking for from here.",
};

const PLACES = [
  { label: "Home", href: "/" },
  { label: "Features", href: "/features" },
  { label: "Guides", href: "/guides" },
  { label: "Help center", href: "/help" },
  { label: "Sign in", href: "/login" },
];

/** The answer to any address nothing lives at. Knows nothing of who is asking. */
export default function NotFound() {
  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader />
      <main id="main" className="mx-auto w-full max-w-[46rem] flex-1 px-4 py-16 sm:px-6">
        <h1 className="mk-h1">We can&apos;t find that page</h1>
        <p className="mt-4 text-[1.0625rem] leading-relaxed text-slate-700">
          It may have moved, or the address may be mistyped. One of these should help:
        </p>
        <ul className="mt-5 space-y-2 text-[1.0625rem]">
          {PLACES.map((place) => (
            <li key={place.href}>
              <a href={place.href} className="tap mk-link">{place.label}</a>
            </li>
          ))}
        </ul>
      </main>
      <SiteFooter />
    </div>
  );
}
