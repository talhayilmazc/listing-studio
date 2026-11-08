import type { Config } from "tailwindcss";

/* The brand tokens (docs/design/home-reference.dc.html, site v3), one place for the public site and the app.
   The colour names already used across the app are re-pointed onto them; utility names are unchanged.
     ink / paper / mist / line / body / muted / tag / go -> the brand tokens
     heather -> line, fabric -> mist (names the site used before v3)
     slate  -> cool neutrals ending in ink (text, borders, panels)
     brand  -> the ink ramp (primary action, focus, selected)
     emerald / amber / rose -> success / warning / danger */
const config: Config = {
  // lib/: class names some helpers return (lib/anotherShop.ts::shopColour).
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.ts"],
  theme: {
    extend: {
      colors: {
        ink: "#14162B", // text and primary buttons: 17.8:1 on paper
        paper: "#FFFFFF",
        mist: "#F5F6FA", // panels
        line: "#E6E8EF", // hairlines
        body: "#4B4F6B", // body text: 8.0:1 on paper, 7.4:1 on mist
        muted: "#6A6F8A", // secondary text: 4.9:1 on paper, 4.6:1 on mist
        tag: "#FFD24A", // the one accent; ink on tag is 12.4:1, tag is never text on paper
        go: { DEFAULT: "#1F8A4C", ink: "#1A7340" }, // status marks; small status text uses go-ink (5.3:1 on its tint)
        heather: "#E6E8EF",
        fabric: "#F5F6FA",
        stone: {
          50: "#F8F9FC", // the app's canvas
        },
        slate: {
          50: "#F8F9FC", // --bg
          100: "#F5F6FA", // mist: panels and sunken surfaces
          200: "#E6E8EF", // line
          300: "#CDD1DD", // --border-strong
          // Text the seller reads is never fainter than 4.5:1 (WCAG AA) on any light surface.
          400: "#6A6F8A", // muted: 4.9:1 on paper, 4.6:1 on mist
          500: "#5B6079", // 6.3:1 on paper
          600: "#4B4F6B", // body: 8.0:1
          700: "#3B3F5C",
          800: "#262942",
          900: "#14162B", // ink
        },
        brand: {
          50: "#EEF0F7", // --accent-subtle
          100: "#DFE2EE",
          500: "#14162B", // focus ring / focused border
          600: "#14162B", // --accent: ink
          700: "#3B3F66", // --accent-hover; as text 10:1 on paper
          800: "#0A0B18",
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
