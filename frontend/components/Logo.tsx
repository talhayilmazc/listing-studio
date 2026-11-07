/**
 * The Listyro mark: a hang tag (chamfered top corners, a punched hole) in an ink outline with a tag-yellow
 * fill (docs/design-v2.md). The hole is cut out, so it shows whatever is behind it; on a dark ground the
 * yellow body carries the shape. Drawn on a 32 grid with a 2.5 stroke so it holds at 16 px (app/icon.svg is
 * the same drawing). Server markup, no script: the public site and the app both use it.
 */

/** The tag and its hole, one path; the hole is cut by the even-odd rule. */
export const MARK_PATH =
  "M10.5 3.5H21.5L28.5 10.5V27A1.5 1.5 0 0 1 27 28.5H5A1.5 1.5 0 0 1 3.5 27V10.5Z" +
  "M16 7.25A2.75 2.75 0 1 0 16 12.75A2.75 2.75 0 1 0 16 7.25Z";

export function LogoMark({ size = 32, className = "" }: { size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true" focusable="false" className={className}>
      <path d={MARK_PATH} fill="#FFCF33" fillRule="evenodd" stroke="#14213D" strokeWidth="2.5" strokeLinejoin="round" />
    </svg>
  );
}

/** Mark and wordmark. ``tone``: ink on paper, or paper on the ink rail. */
export function Logo({ size = 28, tone = "ink", className = "" }: { size?: number; tone?: "ink" | "paper"; className?: string }) {
  return (
    <span className={"inline-flex items-center gap-2 " + className}>
      <LogoMark size={size} />
      <span className={"wordmark leading-none " + (tone === "paper" ? "text-white" : "text-ink")} style={{ fontSize: size * 0.82 }}>
        Listyro
      </span>
    </span>
  );
}
