import type { CSSProperties } from "react";
import { HOME } from "@/lib/homeCopy";
import type { Shot } from "@/lib/marketing/heroImages";

/**
 * The hero's picture, as the reference draws it (docs/design/home-reference.dc.html): a batch window of
 * mockup tiles, the AI card that read one design, and the shops it goes live in. The tiles are the mockup
 * engine's real output on real shirt photos (backend/scripts/hero_assets.py, lib/marketing/heroImages.ts).
 *
 * One entrance on load, about two and a half seconds, then still: the tiles fade in from left to right, the
 * AI card's tags appear one by one, the three shop rows come last. Each piece holds its final place from the
 * first frame (only opacity and transform animate, so nothing shifts), and with reduced motion the classes
 * do nothing at all: the final state is there at once (app/globals.css, .hd-in / .hd-pop).
 */

/** An animation delay, read by .hd-in / .hd-pop. */
const delay = (seconds: number) => ({ "--d": `${seconds.toFixed(2)}s` }) as CSSProperties;

/** When each piece starts, in seconds: tiles 0.15 to 0.85, the card 0.9, its tags to 1.8, the shops to 2.6. */
export const TIMING = { tile: (i: number) => 0.15 + i * 0.1, card: 0.9, tag: (i: number) => 1.05 + i * 0.06, shopCard: 1.7, row: (i: number) => 1.85 + i * 0.15 };

/** A picture at 1x and 2x, AVIF first, with its size so nothing moves while it loads. */
export function Photo({ shot, alt, eager = false, style }: { shot: Shot; alt: string; eager?: boolean; style?: CSSProperties }) {
  return (
    <picture>
      <source type="image/avif" srcSet={`${shot.avif} 1x, ${shot.avif2x} 2x`} />
      <source type="image/webp" srcSet={`${shot.webp} 1x, ${shot.webp2x} 2x`} />
      <img
        src={shot.webp}
        width={shot.width}
        height={shot.height}
        alt={alt}
        loading={eager ? "eager" : "lazy"}
        decoding="async"
        style={{ width: "100%", height: "auto", display: "block", ...style }}
      />
    </picture>
  );
}

export function Check({ size = 12, colour = "#1F8A4C", width = 2 }: { size?: number; colour?: string; width?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" aria-hidden="true">
      <path d="M2.2 6.2 L4.8 8.6 L9.8 3.4" stroke={colour} strokeWidth={width} fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Spark({ size = 16, colour }: { size?: number; colour: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 18 18" aria-hidden="true">
      <path d="M9 1 L10.6 6.6 L16 8.2 L10.6 9.8 L9 15.4 L7.4 9.8 L2 8.2 L7.4 6.6 Z" fill={colour} />
    </svg>
  );
}

export function HeroDemo() {
  const { window: w, shops, ai } = HOME.hero;
  return (
    <div className="lx-hv" data-testid="hero-demo">
      <div className="lx-hv-window">
        <div style={{ borderRadius: 22, background: "#FFFFFF", border: "1px solid #E3E6EE", boxShadow: "0 50px 100px rgba(20,22,43,0.20), 0 8px 24px rgba(20,22,43,0.08)", overflow: "hidden" }}>
          <div style={{ height: 48, display: "flex", alignItems: "center", gap: 8, padding: "0 18px", borderBottom: "1px solid #EEF0F5", background: "#FAFBFD" }}>
            <span style={{ width: 11, height: 11, borderRadius: 99, background: "#FF6B6B" }} />
            <span style={{ width: 11, height: 11, borderRadius: 99, background: "#FFD24A" }} />
            <span style={{ width: 11, height: 11, borderRadius: 99, background: "#4ADE80" }} />
            <span style={{ marginLeft: 14, fontSize: 13, fontWeight: 800 }}>{w.name}</span>
            <span style={{ fontSize: 13, color: "#6A6F8A" }}>{w.count}</span>
            <span style={{ marginLeft: "auto", display: "inline-flex", gap: 6, alignItems: "center", fontSize: 12, fontWeight: 800, color: "#1A7340", background: "#E9F6EE", borderRadius: 99, padding: "5px 10px" }}>
              <Check size={10} width={1.8} />
              <span>{w.ready}</span>
            </span>
          </div>
          <div style={{ padding: 16, display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 12, background: "#FFFFFF" }} data-testid="hero-mockups">
            {w.tiles.map((tile, i) => (
              <div key={tile.alt} className="hd-in" style={{ ...delay(TIMING.tile(i)), borderRadius: 14, background: "linear-gradient(180deg, #F3F4F8, #E4E6EE)", overflow: "hidden" }}>
                <Photo shot={tile.shot} alt={tile.alt} eager />
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="lx-hv-shops hd-in" style={{ ...delay(TIMING.shopCard), borderRadius: 18, padding: 16, background: "#FFFFFF", border: "1px solid #E3E6EE", boxShadow: "0 24px 60px rgba(20,22,43,0.16)", display: "flex", flexDirection: "column", gap: 10 }} data-testid="hero-shops">
        <span style={{ fontSize: 13, fontWeight: 800 }}>{shops.heading}</span>
        {shops.rows.map((row, i) => (
          <span key={row.name} className="hd-in" style={{ ...delay(TIMING.row(i)), display: "flex", gap: 10, alignItems: "center", fontSize: 13 }}>
            <span style={{ width: 9, height: 9, borderRadius: 99, background: row.dot }} />
            <span style={{ flexGrow: 1, fontWeight: 700 }}>{row.name}</span>
            <span style={{ color: "#6A6F8A" }}>{row.when}</span>
          </span>
        ))}
      </div>

      <div className="lx-hv-ai hd-in" style={{ ...delay(TIMING.card), borderRadius: 20, padding: 18, background: "#14162B", color: "#FFFFFF", boxShadow: "0 30px 70px rgba(20,22,43,0.35)", display: "flex", flexDirection: "column", gap: 11 }} data-testid="hero-listing">
        <span style={{ display: "inline-flex", gap: 8, alignItems: "center", fontSize: 13, fontWeight: 800, color: "#FFD24A" }}>
          <Spark colour="#FFD24A" />
          <span>{ai.read}</span>
        </span>
        <span style={{ fontSize: 16, fontWeight: 800, lineHeight: 1.3 }}>{ai.title}</span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {ai.tags.map((tag, i) => (
            <span key={tag} className="hd-pop" style={{ ...delay(TIMING.tag(i)), fontSize: 12, fontWeight: 700, background: "rgba(255,255,255,0.10)", borderRadius: 7, padding: "4px 8px" }}>{tag}</span>
          ))}
        </div>
        <span style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#A4A9C4" }}>
          <span>{ai.tagCount}</span>
          <span>{ai.characters}</span>
        </span>
      </div>
    </div>
  );
}
