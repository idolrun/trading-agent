import type { NextConfig } from "next";

// Server-only (no NEXT_PUBLIC_). Rewrites are baked in at `next build`.
const backendOrigin = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendOrigin}/api/:path*` }];
  },
};

export default nextConfig;
