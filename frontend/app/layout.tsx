import type { Metadata } from "next";
import { Instrument_Serif } from "next/font/google";
import "./globals.css";
import { AppShell } from "@/components/AppShell";

// Display face for page titles and large metric numbers (docs/ui-direction-v2.md §2).
// Self-hosted by next/font at build time - no runtime request, no new dependency.
const display = Instrument_Serif({
  weight: "400",
  subsets: ["latin"],
  display: "swap",
  variable: "--font-display",
});

export const metadata: Metadata = {
  title: "Listing Studio",
  description: "Prepare compliant draft listings from your own original designs.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={display.variable}>
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
