import { IDEAS_PATH, MARKETING_PAGES, cacheRules } from "./lib/marketing/routes.mjs";

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
const buildCsp = (scripts = "", connections = "", frames = "") => [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}${scripts}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob: https://i.etsystatic.com",
  "font-src 'self'",
  // Dev only: Next's hot reload talks over a websocket.
  `connect-src 'self'${dev ? " ws: wss:" : ""}${connections}`,
  ...(frames ? [`frame-src${frames}`] : []),
  "object-src 'none'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

/**
 * The public site's visit counter (Cloudflare Web Analytics, app/(marketing)/layout.tsx): only when
 * its token is set at build time, and only on marketing pages. The app's pages keep the strict policy.
 */
const analytics = Boolean(process.env.NEXT_PUBLIC_CF_ANALYTICS_TOKEN);
const marketingCsp = buildCsp(" https://static.cloudflareinsights.com", " https://cloudflareinsights.com");

/**
 * The AI ideas tool's page alone may load Cloudflare Turnstile, which proves a visitor is a person
 * before the tool spends anything (docs/free-tools.md). No other page gets this.
 */
const TURNSTILE = " https://challenges.cloudflare.com";
const ideasCsp = analytics
  ? buildCsp(` https://static.cloudflareinsights.com${TURNSTILE}`, ` https://cloudflareinsights.com${TURNSTILE}`, TURNSTILE)
  : buildCsp(TURNSTILE, TURNSTILE, TURNSTILE);

const securityHeaders = [
  { key: "Content-Security-Policy", value: buildCsp() },
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
  // A self-contained server for the production image (docker/frontend.prod.Dockerfile):
  // only the files the app needs, no dev dependencies. No effect on `next dev`.
  output: "standalone",
  // A page rendered per request would otherwise get its title and description after the head, for
  // every browser but a known search bot. Ours are constants, so there is nothing to wait for.
  htmlLimitedBots: /.*/,
  experimental: {
    // How long the /api proxy waits on the backend (Next's default is 30 s): a
    // 30 MB photo on a slow connection must not be cut off mid-upload. The page
    // itself stops waiting after lib/api.ts UPLOAD_TIMEOUT_MS and says so.
    proxyTimeout: 300_000,
  },
  // Proxy API calls to the FastAPI backend so the browser stays same-origin
  // (no CORS) and image <img src="/api/..."> works directly. In production the
  // tunnel already sends /api/* to the API itself (deploy/cloudflared/config.yml),
  // so uploads and images do not pass through this server; this rewrite serves
  // development and any request that reaches the frontend anyway.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
  },
  async headers() {
    // Pages only: /api responses carry the backend's own, stricter headers.
    return [
      { source: "/((?!api/).*)", headers: securityHeaders },
      ...(analytics ? MARKETING_PAGES.map((source) => ({ source, headers: [{ key: "Content-Security-Policy", value: marketingCsp }] })) : []),
      { source: IDEAS_PATH, headers: [{ key: "Content-Security-Policy", value: ideasCsp }] },
      // Who may keep each response: private unless lib/marketing/routes.mjs lists it (docs/seo.md).
      ...cacheRules(),
    ];
  },
};

export default nextConfig;
