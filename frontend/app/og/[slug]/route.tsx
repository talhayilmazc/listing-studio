import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { ImageResponse } from "next/og";
import { TAG_BACK, TAG_FRONT, WORDMARK, WORDMARK_BASELINE, WORDMARK_SIZE, WORDMARK_WIDTH } from "@/lib/logoPaths";
import { PAGES, cardSlug, pageForCard } from "@/lib/marketing/pages";

/**
 * Each public page's share card, drawn once at build time from the page's own heading, in the site's dark
 * section style (docs/design/home-reference.dc.html): ink, the soft yellow and lilac glows, the logo on dark
 * and the heading in Archivo. Our name, mark and colours only: no Etsy logo and no Etsy colour. The type is a
 * static Latin subset of Archivo (SIL Open Font License; app/og/archivo-*.ttf), read at build time.
 */

export const dynamic = "force-static";
export const dynamicParams = false;
export const generateStaticParams = () => PAGES.map((page) => ({ slug: `${cardSlug(page.path)}.png` }));

const font = (file: string) => readFile(join(process.cwd(), "app", "og", file));

export async function GET(_request: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const page = pageForCard(slug.replace(/\.png$/, ""));
  if (!page) return new Response(null, { status: 404 });
  const [heavy, regular] = await Promise.all([font("archivo-900.ttf"), font("archivo-500.ttf")]);
  const type = 46;
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 72,
          background: "#14162B",
          backgroundImage:
            "radial-gradient(700px 420px at 92% 0%, rgba(255,210,74,0.28), rgba(255,210,74,0) 70%), radial-gradient(600px 420px at 0% 100%, rgba(150,120,255,0.24), rgba(150,120,255,0) 70%)",
          color: "#FFFFFF",
          fontFamily: "Archivo",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <svg viewBox="0 0 64 64" width={64} height={64}>
            <g transform="rotate(12 40 26)"><path d={TAG_BACK} fill="#FFD24A" /></g>
            <g transform="rotate(-8 30 36)"><path d={TAG_FRONT} fill="#FFFFFF" /><circle cx="33" cy="27.5" r="3.8" fill="#14162B" /></g>
          </svg>
          <svg viewBox={`0 0 ${WORDMARK_WIDTH} ${WORDMARK_SIZE}`} width={(type * WORDMARK_WIDTH) / WORDMARK_SIZE} height={type}>
            <path transform={`translate(0 ${WORDMARK_BASELINE})`} d={WORDMARK} fill="#FFFFFF" />
          </svg>
        </div>
        <div style={{ display: "flex", fontSize: page.card.length > 70 ? 60 : 76, fontWeight: 900, lineHeight: 1.0, letterSpacing: "-0.03em", maxWidth: 1040 }}>{page.card}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 14, fontSize: 26, fontWeight: 500, color: "#C3C7DD" }}>
          <div style={{ display: "flex", width: 14, height: 14, borderRadius: 99, background: "#FFD24A" }} />
          <div style={{ display: "flex" }}>listyro.com</div>
        </div>
      </div>
    ),
    {
      width: 1200,
      height: 630,
      fonts: [
        { name: "Archivo", data: heavy, weight: 900, style: "normal" },
        { name: "Archivo", data: regular, weight: 500, style: "normal" },
      ],
    },
  );
}
