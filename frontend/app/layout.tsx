import type { Metadata, Viewport } from "next";
import { Archivo } from "next/font/google";
import "./globals.css";

// Archivo, the one typeface (docs/design-v2.md): body at its normal width, headlines set wide from the same
// variable file (the wdth axis). Self-hosted by next/font at build time: no runtime request. "optional": the
// file is preloaded and normally there in time; if it is not, the fallback stays for that view, so a late
// font never moves a page (a wide headline swapping in would).
const archivo = Archivo({
  subsets: ["latin"],
  axes: ["wdth"],
  display: "optional",
  variable: "--font-archivo",
});

const SITE = process.env.NEXT_PUBLIC_SITE_URL || "https://listyro.com";

// The product name never contains "Etsy" (Etsy API Terms of Use), here or in any page title.
export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: { default: "Listyro", template: "%s · Listyro" },
  description:
    "A listing workflow and compliance assistant for print-on-demand and apparel sellers: a folder of mockups becomes review-ready drafts, and nothing goes live until you approve it.",
  applicationName: "Listyro",
  openGraph: { type: "website", siteName: "Listyro", images: [{ url: "/og/home.png", width: 1200, height: 630, alt: "Listyro: upload your designs, get finished listings with mockups for every shop you run." }] },
  twitter: { card: "summary_large_image", images: ["/og/home.png"] },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#ffffff" };

/**
 * The document only. The signed-in app brings its own frame and session
 * (app/(app)/layout.tsx); the public site is static and loads neither
 * (app/(marketing)/layout.tsx).
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={archivo.variable}>
      <body>{children}</body>
    </html>
  );
}
