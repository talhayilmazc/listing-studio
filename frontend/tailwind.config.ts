import type { Config } from "tailwindcss";

/* The brand tokens (docs/design-v2.md), one place for the public site and the app. The colour names
   already used across the app are re-pointed onto them; utility names in components are unchanged.
     ink / paper / heather / fabric / tag / go -> the print-shop tokens
     slate  -> cool neutrals ending in ink (text, borders, panels)
     brand  -> the ink ramp (primary action, focus, selected)
     emerald / amber / rose -> success / warning / danger */
const config: Config = {
  // lib/: class names some helpers return (lib/anotherShop.ts::shopColour).
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.ts"],
  theme: {
    extend: {
      colors: {
        // The print-shop tokens.
        ink: "#14213D", // text and primary buttons: 16:1 on paper
        paper: "#FFFFFF",
        heather: "#D9DCE1", // lines
        fabric: "#F3F4F6", // panels
        tag: "#FFCF33", // the single accent; ink on tag is 10.8:1, tag is never text on paper
        go: { DEFAULT: "#1F8A4C", ink: "#1A7340" }, // status marks; small status text uses go-ink (5.9:1)
        stone: {
          50: "#F7F8FA", // the app's canvas
        },
        slate: {
          50: "#F7F8FA", // --bg
          100: "#F3F4F6", // fabric: panels and sunken surfaces
          200: "#D9DCE1", // heather: lines
          300: "#C4C8CF", // --border-strong
          // Text the seller reads is never fainter than 4.5:1 (WCAG AA) on any light surface.
          400: "#5F6673", // --text-muted: 5.8:1 on paper, 5.3:1 on fabric
          500: "#545B67", // 6.8:1 on paper
          600: "#474D58", // --text-secondary: 8.5:1
          700: "#363C48",
          800: "#232A3B",
          900: "#14213D", // ink
        },
        brand: {
          50: "#EEF1F6", // --accent-subtle
          100: "#DDE2EC",
          500: "#14213D", // focus ring / focused border
          600: "#14213D", // --accent: ink
          700: "#2B3E6B", // --accent-hover; as text 10.5:1 on paper
          800: "#0B1326",
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
        // The same Archivo, set wide (globals.css .font-display): page titles and large numbers only.
        display: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      // Scale: 32 / 24 / 18 / 15 / 13. Body 15px, meta 13px.
      // Headings 1.2 line-height with tight tracking; body 1.6.
      fontSize: {
        xs: ["0.8125rem", { lineHeight: "1.6" }], // 13px
        sm: ["0.9375rem", { lineHeight: "1.6" }], // 15px
        lg: ["1.125rem", { lineHeight: "1.3", letterSpacing: "-0.01em" }], // 18px
        "2xl": ["1.5rem", { lineHeight: "1.2", letterSpacing: "-0.02em" }], // 24px
        "3xl": ["2rem", { lineHeight: "1.2", letterSpacing: "-0.02em" }], // 32px
        "4xl": ["2.5rem", { lineHeight: "1.1", letterSpacing: "-0.01em" }], // 40px
      },
      boxShadow: {
        card: "0 1px 2px rgba(0,0,0,0.04)",
      },
    },
  },
  plugins: [],
};

export default config;
