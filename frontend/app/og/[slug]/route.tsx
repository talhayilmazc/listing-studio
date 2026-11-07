import { ImageResponse } from "next/og";
import { MARK_PATH } from "@/components/Logo";
import { PAGES, cardSlug, pageForCard } from "@/lib/marketing/pages";

/**
 * Each public page's share card, drawn once at build time from the page's own heading. Our name, mark and
 * colours only (docs/design-v2.md): no Etsy logo and no Etsy colour.
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
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", justifyContent: "space-between", padding: 72, background: "#ffffff", color: "#14213D", borderBottom: "24px solid #FFCF33" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <svg viewBox="0 0 32 32" width={64} height={64}>
            <path d={MARK_PATH} fill="#FFCF33" fillRule="evenodd" stroke="#14213D" strokeWidth="2.5" strokeLinejoin="round" />
          </svg>
          <div style={{ fontSize: 46, fontWeight: 800 }}>Listyro</div>
        </div>
        <div style={{ display: "flex", fontSize: page.card.length > 70 ? 56 : 72, fontWeight: 700, lineHeight: 1.1, maxWidth: 1040 }}>{page.card}</div>
        <div style={{ display: "flex", fontSize: 26, color: "#474D58" }}>listyro.com</div>
      </div>
    ),
    { width: 1200, height: 630 },
  );
}
