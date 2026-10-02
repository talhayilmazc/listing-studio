/**
 * A garment mockup drawn as inline SVG: crisp at any size, a few hundred bytes,
 * no image request. The artwork is simple original geometry, so the public site
 * shows sample designs without showing any seller's work.
 */

export type Art = "mountain" | "heart" | "paw" | "books" | "leaf" | "cup";

const GARMENT: Record<Art, { shirt: string; ink: string; accent: string; ground: string }> = {
  mountain: { shirt: "#e9e2d3", ink: "#3f3a33", accent: "#c2542d", ground: "#f3efe7" },
  heart: { shirt: "#2f3a4a", ink: "#f4efe6", accent: "#e8a7a1", ground: "#eef0f3" },
  paw: { shirt: "#c9d3c4", ink: "#2f3b2f", accent: "#7a8f6a", ground: "#f1f4ef" },
  books: { shirt: "#f4efe6", ink: "#33302b", accent: "#4c1d95", ground: "#f5f3f0" },
  leaf: { shirt: "#3d4a3d", ink: "#e9e2d3", accent: "#a9c29a", ground: "#eef1ee" },
  cup: { shirt: "#d9c7b8", ink: "#3b2f29", accent: "#8a4b2f", ground: "#f6f1ec" },
};

function Artwork({ art, ink, accent }: { art: Art; ink: string; accent: string }) {
  switch (art) {
    case "mountain":
      return (
        <g>
          <circle cx="100" cy="98" r="15" fill={accent} />
          <path d="M66 126 L90 94 L104 112 L116 100 L134 126 Z" fill={ink} />
          <rect x="70" y="131" width="60" height="3" rx="1.5" fill={ink} />
          <rect x="80" y="138" width="40" height="3" rx="1.5" fill={ink} opacity=".55" />
        </g>
      );
    case "heart":
      return (
        <g>
          <path d="M100 132 C76 114 74 96 88 92 C95 90 100 96 100 100 C100 96 105 90 112 92 C126 96 124 114 100 132 Z" fill={accent} />
          <rect x="97" y="101" width="6" height="18" rx="1" fill={ink} />
          <rect x="91" y="107" width="18" height="6" rx="1" fill={ink} />
        </g>
      );
    case "paw":
      return (
        <g fill={ink}>
          <ellipse cx="100" cy="120" rx="15" ry="12" />
          <circle cx="82" cy="104" r="6" />
          <circle cx="94" cy="96" r="6" />
          <circle cx="106" cy="96" r="6" />
          <circle cx="118" cy="104" r="6" />
          <rect x="78" y="138" width="44" height="3" rx="1.5" fill={accent} />
        </g>
      );
    case "books":
      return (
        <g>
          <rect x="74" y="122" width="52" height="10" rx="2" fill={ink} />
          <rect x="80" y="110" width="46" height="10" rx="2" fill={accent} />
          <rect x="72" y="98" width="48" height="10" rx="2" fill={ink} opacity=".7" />
          <rect x="84" y="86" width="34" height="10" rx="2" fill={accent} opacity=".6" />
        </g>
      );
    case "leaf":
      return (
        <g>
          <path d="M100 134 C100 112 84 104 80 90 C98 90 112 100 112 118" fill={accent} />
          <path d="M100 134 C100 116 112 108 122 98 C124 114 116 126 104 130" fill={ink} opacity=".85" />
          <rect x="99" y="118" width="2.5" height="20" fill={ink} />
        </g>
      );
    case "cup":
      return (
        <g>
          <path d="M80 104 H118 V122 A14 14 0 0 1 104 136 H94 A14 14 0 0 1 80 122 Z" fill={ink} />
          <path d="M118 108 H124 A7 7 0 0 1 124 122 H118" fill="none" stroke={ink} strokeWidth="4" />
          <path d="M90 96 C87 91 93 89 90 84 M100 96 C97 91 103 89 100 84 M110 96 C107 91 113 89 110 84" fill="none" stroke={accent} strokeWidth="2.5" strokeLinecap="round" />
        </g>
      );
  }
}

export function Mockup({ art, label, className = "" }: { art: Art; label: string; className?: string }) {
  const g = GARMENT[art];
  return (
    <svg
      viewBox="0 0 200 200"
      // A thumbnail beside its own label is decoration; a picture on its own is described.
      {...(label ? { role: "img", "aria-label": label } : { "aria-hidden": true })}
      className={className}
      preserveAspectRatio="xMidYMid slice"
    >
      <rect width="200" height="200" fill={g.ground} />
      <path
        d="M70 40 C80 50 120 50 130 40 L166 58 L152 88 L138 80 L138 168 Q138 172 134 172 L66 172 Q62 172 62 168 L62 80 L48 88 L34 58 Z"
        fill={g.shirt}
        stroke="rgba(0,0,0,.08)"
        strokeWidth="1"
      />
      <path d="M70 40 C80 50 120 50 130 40 C122 58 78 58 70 40 Z" fill="rgba(0,0,0,.07)" />
      <Artwork art={art} ink={g.ink} accent={g.accent} />
    </svg>
  );
}
