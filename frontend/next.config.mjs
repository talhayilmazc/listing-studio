/** @type {import('next').NextConfig} */
const target = process.env.API_PROXY_TARGET || "http://localhost:8000";
const dev = process.env.NODE_ENV !== "production";

/**
 * Content-Security-Policy for the app's pages (production-spec D).
 *
 * script-src keeps 'unsafe-inline' because the App Router inlines its hydration
 * payload as <script> tags; the strict alternative is a per-request nonce, which
 * would force every page to render dynamically. What this policy still buys:
 * no third-party script origins at all, no plugins, no framing (clickjacking),
 * no <base> hijacking, and forms that can only post back to us.
 *
 * Images: our own /api (proxied, same-origin), blob: for local upload previews,
 * and the Etsy CDN for the seller's own listing and reference images.
 */
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https://i.etsystatic.com",
  "font-src 'self'",
  // Dev only: Next's hot reload talks over a websocket.
  `connect-src 'self'${dev ? " ws: wss:" : ""}`,
  "object-src 'none'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  // Ignored by browsers over plain http, so harmless in development.
  { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
];

const nextConfig = {
  reactStrictMode: true,
  // No "X-Powered-By: Next.js" advertising the stack.
  poweredByHeader: false,
  // Proxy API calls to the FastAPI backend so the browser stays same-origin
  // (no CORS) and image <img src="/api/..."> works directly.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
  },
  async headers() {
    // Pages only: /api responses carry the backend's own, stricter headers.
    return [{ source: "/((?!api/).*)", headers: securityHeaders }];
  },
};

export default nextConfig;
