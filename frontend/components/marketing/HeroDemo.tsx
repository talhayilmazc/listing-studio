import { HOME } from "@/lib/homeCopy";

/**
 * The hero's one moment: a design file becomes a row of colour mockups, then a listing (title and 13 tags),
 * then three shops with the time each goes live. Real output: the synthetic demo account's design NW1042, its
 * text and shops (backend/scripts/demo_screens_seed.py), and mockups the mockup engine made from them
 * (backend/scripts/hero_assets.py). Static markup and CSS only: every piece is laid out in its final place
 * from the first frame, so nothing moves the page; with motion allowed each appears in turn once (about four
 * seconds, globals.css .hd-in/.hd-pop) and rests; with reduced motion the final state is all there is.
 * Every word comes from lib/homeCopy.ts.
 */

const delay = (seconds: number) => ({ "--d": `${seconds.toFixed(2)}s` }) as React.CSSProperties;
/** The demo's mockups, in the order of HOME.hero.demo.colours (paths written out: scripts/public-assets.cjs reads them). */
export const MOCKUPS = ["/hero/mockup-white.webp", "/hero/mockup-sand.webp", "/hero/mockup-sage.webp", "/hero/mockup-pink.webp", "/hero/mockup-mustard.webp"];

export function HeroDemo() {
  const d = HOME.hero.demo;
  return (
    <figure aria-label={d.label} className="min-w-0" data-testid="hero-demo">
      <div className="border border-heather bg-fabric p-3 sm:p-5">
        <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] items-end gap-3 sm:grid-cols-[7.5rem_minmax(0,1fr)] sm:gap-4">
          <div className="hd-in min-w-0" style={delay(0.1)} data-testid="hero-file">
            <div className="checkerboard border border-heather p-1.5">
              <img src="/hero/design.webp" alt={d.fileAlt} width={120} height={101} className="block h-auto w-full" />
            </div>
            <p className="mt-1.5 truncate text-xs font-semibold text-ink" title={d.file}>{d.file}</p>
          </div>
          <div className="min-w-0">
            <p className="hd-in mb-1.5 text-xs font-semibold text-slate-600" style={delay(0.5)}>{d.mockups}</p>
            <ul className="grid grid-cols-5 gap-1.5 sm:gap-2" data-testid="hero-mockups">
              {MOCKUPS.map((src, i) => (
                <li key={src} className="hd-pop min-w-0" style={delay(0.6 + i * 0.14)}>
                  <img src={src} alt={d.colours[i]} width={96} height={112} className="block h-auto w-full border border-heather" />
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="hd-in mt-3 border border-heather bg-white p-3 sm:mt-4 sm:p-4" style={delay(1.5)} data-testid="hero-listing">
          <p className="flex items-center justify-between gap-2 text-xs font-semibold text-slate-600">
            <span>{d.listing}</span>
            <span className="mk-chip !text-xs">{d.tagCount}</span>
          </p>
          <p className="mt-1.5 text-[0.9375rem] font-bold leading-snug text-ink">{d.title}</p>
          <ul className="mt-2 flex flex-wrap gap-1">
            {d.tags.map((tag, i) => (
              <li key={tag} className="hd-pop rounded-[3px] border border-heather bg-fabric px-1.5 py-px text-xs leading-5 text-ink" style={delay(1.75 + i * 0.06)}>{tag}</li>
            ))}
          </ul>
        </div>

        <ul className="mt-3 divide-y divide-heather border border-heather bg-white sm:mt-4" data-testid="hero-shops">
          {d.shops.map((shop, i) => (
            <li key={shop.name} className="hd-in flex items-center gap-2 px-3 py-2 text-[0.8125rem] sm:text-sm" style={delay(2.8 + i * 0.3)}>
              <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-go" />
              <span className="min-w-0 flex-1 truncate font-semibold text-ink">{shop.name}</span>
              <span className="hidden font-semibold text-go-ink sm:inline">{d.scheduled}</span>
              <span className="mk-chip !text-xs">{shop.time}</span>
            </li>
          ))}
        </ul>
      </div>
      <figcaption className="mt-2 text-[0.8125rem] text-slate-600">{d.caption}</figcaption>
    </figure>
  );
}
