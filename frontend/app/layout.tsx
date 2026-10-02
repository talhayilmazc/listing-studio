import type { Metadata, Viewport } from "next";
import { Instrument_Serif } from "next/font/google";
import "./globals.css";

// Display face for page titles and large metric numbers (docs/ui-direction-v2.md §2).
// Self-hosted by next/font at build time - no runtime request, no new dependency.
const display = Instrument_Serif({
  weight: "400",
  style: ["normal", "italic"],
  subsets: ["latin"],
  display: "swap",
  variable: "--font-display",
});

const SITE = process.env.NEXT_PUBLIC_SITE_URL || "https://listyro.com";

// The product name never contains "Etsy" (Etsy API Terms of Use), here or in any page title.
export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: { default: "Listyro", template: "%s · Listyro" },
  description:
    "A listing workflow and compliance assistant for print-on-demand and apparel sellers: a folder of mockups becomes review-ready drafts, and nothing goes live until you approve it.",
  applicationName: "Listyro",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#fafaf9" };

/**
 * The document only. The signed-in app brings its own frame and session
 * (app/(app)/layout.tsx); the public site is static and loads neither
 * (app/(marketing)/layout.tsx).
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={display.variable}>
      <body>{children}</body>
    </html>
  );
}
