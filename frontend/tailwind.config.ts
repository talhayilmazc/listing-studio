import type { Config } from "tailwindcss";

/* The palette below re-points the colour names already used across the app onto
   the design tokens in app/globals.css (docs/ui-redesign.md §1). Utility names in
   components are unchanged on purpose — only the values move.
     slate  -> warm neutral ramp (text, borders, sunken surfaces)
     brand  -> deep violet accent (primary action, focus, selected)
     emerald / amber / rose -> success / warning / danger */
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        stone: {
          50: "#fafaf9", // --bg
        },
        slate: {
          50: "#fafaf9", // --bg
          100: "#f5f5f4", // --surface-sunken
          200: "#e7e5e4", // --border
          300: "#d6d3d1", // --border-strong
          400: "#a8a29e", // --text-muted
          500: "#78716c",
          600: "#57534e", // --text-secondary
          700: "#44403c",
          800: "#292524",
          900: "#1c1917", // --text
        },
        brand: {
          50: "#f5f3ff", // --accent-subtle
          100: "#ede9fe",
          500: "#6d28d9", // focus ring / focused border
          600: "#4c1d95", // --accent
          700: "#5b21b6", // --accent-hover
          800: "#3b0764",
        },
        emerald: {
          50: "#f0fdf4",
          100: "#dcfce7",
          200: "#bbf7d0",
          300: "#86efac",
          500: "#16a34a",
          600: "#15803d",
          700: "#166534", // --success
        },
        amber: {
          50: "#fefce8",
          100: "#fef9c3",
          200: "#fde68a",
          300: "#fcd34d",
          600: "#ca8a04",
          700: "#a16207", // --warning
        },
        rose: {
          50: "#fef2f2",
          100: "#fee2e2",
          200: "#fecaca",
          300: "#fca5a5",
          400: "#f87171",
          500: "#ef4444",
          600: "#b91c1c",
          700: "#991b1b", // --danger
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      // Scale: 32 / 24 / 18 / 15 / 13. Body 15px, meta 13px.
      // Headings 1.2 line-height with tight tracking; body 1.6.
      fontSize: {
        xs: ["0.8125rem", { lineHeight: "1.6" }], // 13px
        sm: ["0.9375rem", { lineHeight: "1.6" }], // 15px
        lg: ["1.125rem", { lineHeight: "1.3", letterSpacing: "-0.01em" }], // 18px
        "2xl": ["1.5rem", { lineHeight: "1.2", letterSpacing: "-0.02em" }], // 24px
        "3xl": ["2rem", { lineHeight: "1.2", letterSpacing: "-0.02em" }], // 32px
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.04)",
      },
    },
  },
  plugins: [],
};

export default config;
