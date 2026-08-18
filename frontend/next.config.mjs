/** @type {import('next').NextConfig} */
const target = process.env.API_PROXY_TARGET || "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  // Proxy API calls to the FastAPI backend so the browser stays same-origin
  // (no CORS) and image <img src="/api/..."> works directly.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
  },
};

export default nextConfig;
