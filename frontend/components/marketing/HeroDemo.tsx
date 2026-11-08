import type { CSSProperties } from "react";
import { HOME } from "@/lib/homeCopy";
import type { Shot } from "@/lib/marketing/heroImages";

/**
 * The hero's picture, as the reference draws it (docs/design/home-reference.dc.html): a batch window of
 * mockup tiles, the AI card that read one design, and the shops it goes live in. The tiles are the mockup
 * engine's real output on real shirt photos (backend/scripts/hero_assets.py, lib/marketing/heroImages.ts).
 * The reference's values are the lx- classes in app/globals.css.
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

/** A picture at 1x and 2x, AVIF first, with its size so nothing moves while it loads. Lazy: the page's
 *  largest paint is the h1, and a lazy picture in view still loads at once. */
export function Photo({ shot, alt }: { shot: Shot; alt: string }) {
  return (
    <picture>
      <source type="image/avif" srcSet={`${shot.avif} 1x, ${shot.avif2x} 2x`} />
      <source type="image/webp" srcSet={`${shot.webp} 1x, ${shot.webp2x} 2x`} />
      <img
        src={shot.webp}
        width={shot.width}
        height={shot.height}
        alt={alt}
        loading="lazy"
        decoding="async"
        className="lx-img"
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
        <div className="lx-win">
          <div className="lx-win-bar">
            <span className="lx-light" style={{ background: "#FF6B6B" }} />
            <span className="lx-light" style={{ background: "#FFD24A" }} />
            <span className="lx-light" style={{ background: "#4ADE80" }} />
            <span className="lx-win-name">{w.name}</span>
            <span className="lx-win-count">{w.count}</span>
            <span className="lx-ready">
              <Check size={10} width={1.8} />
              <span>{w.ready}</span>
            </span>
          </div>
          <div className="lx-win-grid" data-testid="hero-mockups">
            {w.tiles.map((tile, i) => (
              <div key={tile.alt} className="lx-tile hd-in" style={delay(TIMING.tile(i))}>
                <Photo shot={tile.shot} alt={tile.alt} />
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="lx-hv-shops hd-in" style={delay(TIMING.shopCard)} data-testid="hero-shops">
        <span className="lx-shops-h">{shops.heading}</span>
        {shops.rows.map((row, i) => (
          <span key={row.name} className="lx-shop-row hd-in" style={delay(TIMING.row(i))}>
            <span className="lx-dot" style={{ background: row.dot }} />
            <span className="lx-shop-name">{row.name}</span>
            <span className="lx-muted">{row.when}</span>
          </span>
        ))}
      </div>

      <div className="lx-hv-ai hd-in" style={delay(TIMING.card)} data-testid="hero-listing">
        <span className="lx-ai-read">
          <Spark colour="#FFD24A" />
          <span>{ai.read}</span>
        </span>
        <span className="lx-ai-title">{ai.title}</span>
        <div className="lx-chips">
          {ai.tags.map((tag, i) => (
            <span key={tag} className="lx-chip-dark hd-pop" style={delay(TIMING.tag(i))}>{tag}</span>
          ))}
        </div>
        <span className="lx-ai-foot">
          <span>{ai.tagCount}</span>
          <span>{ai.characters}</span>
        </span>
      </div>
    </div>
  );
}
