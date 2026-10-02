import { Instrument_Serif } from "next/font/google";
import { SiteFooter, SiteHeader } from "@/components/marketing/Site";

// The public site uses the display face with "optional": if it has not arrived
// in the first moments the fallback stays for that view, so a late font can
// never move the page (the app keeps "swap"). It is preloaded, so it is
// normally there in time.
const display = Instrument_Serif({
  weight: "400",
  style: ["normal", "italic"],
  subsets: ["latin"],
  display: "optional",
  variable: "--font-display",
});

/** The public site. Static pages: no session lookup, none of the app's code. */
export default function MarketingLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className={display.variable + " flex min-h-screen flex-col"}>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow"
      >
        Skip to content
      </a>
      <SiteHeader />
      <main id="main" className="flex-1">{children}</main>
      <SiteFooter />
    </div>
  );
}
