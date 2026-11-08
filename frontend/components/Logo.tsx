/**
 * The Listyro logo (docs/design/logo-reference.dc.html): two stacked hang tags, the back one tag yellow and
 * turned 12 degrees, the front one ink and turned -8 degrees with a punched hole, beside the wordmark
 * "listyro" in lower case (Archivo 900, width 108%, tight tracking). The letters are outlines
 * (lib/logoPaths.ts, written with the files in public/brand by scripts/logo-assets.py), so the logo looks
 * the same before and after the web font arrives. Server markup, no script: the site and the app use it.
 */
import { TAG_BACK, TAG_FRONT, WORDMARK, WORDMARK_BASELINE, WORDMARK_SIZE, WORDMARK_WIDTH } from "@/lib/logoPaths";

const INK = "#14162B";
const TAG = "#FFD24A";
const PAPER = "#FFFFFF";

/** The two tags. ``tone``: ink front tag on light grounds, white front tag on ink. */
export function LogoMark({ size = 36, tone = "ink", className = "" }: { size?: number; tone?: "ink" | "paper"; className?: string }) {
  const front = tone === "paper" ? PAPER : INK;
  const hole = tone === "paper" ? INK : PAPER;
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true" focusable="false" className={className}>
      <g transform="rotate(12 40 26)"><path d={TAG_BACK} fill={TAG} /></g>
      <g transform="rotate(-8 30 36)">
        <path d={TAG_FRONT} fill={front} />
        <circle cx="33" cy="27.5" r="3.8" fill={hole} />
      </g>
    </svg>
  );
}

/**
 * Mark and wordmark, as the reference sets them: the type is three quarters of the mark's height (36 / 27 in
 * the header, 32 / 24 in the footer) with a gap of 10/36 of it.
 */
export function Logo({ size = 36, tone = "ink", className = "" }: { size?: number; tone?: "ink" | "paper"; className?: string }) {
  const type = size * 0.75;
  return (
    <span className={"inline-flex items-center " + className} style={{ gap: (size * 10) / 36 }}>
      <LogoMark size={size} tone={tone} />
      <svg
        viewBox={`0 0 ${WORDMARK_WIDTH} ${WORDMARK_SIZE}`}
        width={(type * WORDMARK_WIDTH) / WORDMARK_SIZE}
        height={type}
        aria-hidden="true"
        focusable="false"
      >
        <path transform={`translate(0 ${WORDMARK_BASELINE})`} d={WORDMARK} fill={tone === "paper" ? PAPER : INK} />
      </svg>
    </span>
  );
}
