/**
 * The logo files (public/brand, from scripts/logo-assets.py and scripts/logo-icons.mjs), for anyone who needs
 * the logo outside the page: the web manifest, structured data, press. Written out so
 * scripts/public-assets.cjs checks each one exists.
 */
export const BRAND_FILES = {
  /** The two tags alone. */
  mark: "/brand/mark.svg",
  /** Mark and wordmark, ink on light. */
  logo: "/brand/logo.svg",
  /** Mark and wordmark, white on ink. */
  logoDark: "/brand/logo-dark.svg",
  /** The app icon: the mark on an ink square. */
  icon: "/brand/icon.svg",
  icon192: "/brand/icon-192.png",
  /** Full bleed, the mark inside the 80% safe circle. */
  icon512Maskable: "/brand/icon-512-maskable.png",
};
