import { ImageResponse } from "next/og";
import { PAGES, cardSlug, pageForCard } from "@/lib/marketing/pages";

/**
 * Each public page's share card, drawn once at build time from the page's own heading. Our name and
 * colours only: no Etsy logo and no Etsy colour.
 */

export const dynamic = "force-static";
export const dynamicParams = false;
export const generateStaticParams = () => PAGES.map((page) => ({ slug: `${cardSlug(page.path)}.png` }));

export async function GET(_request: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const page = pageForCard(slug.replace(/\.png$/, ""));
  if (!page) return new Response(null, { status: 404 });
  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "space-between", padding: 72, background: "#fafaf9", color: "#1c1917" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <div style={{ width: 64, height: 64, borderRadius: 14, background: "#4c1d95", color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 40 }}>L</div>
          <div style={{ fontSize: 44 }}>Listyro</div>
        </div>
        <div style={{ display: "flex", fontSize: page.card.length > 70 ? 56 : 72, lineHeight: 1.1, maxWidth: 1040 }}>{page.card}</div>
        <div style={{ display: "flex", fontSize: 26, color: "#57534e" }}>listyro.com</div>
      </div>
    ),
    { width: 1200, height: 630 },
  );
}
