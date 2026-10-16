import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {
    root: __dirname,  // Lock workspace root to this dashboard dir (parent has unrelated package-lock.json)
  },
  // output: "standalone" is off: it is incompatible with `next start`
  // (which is what the PuffPAI-Dashboard scheduled task runs). It caused 500s
  // on /_next/static chunks after a rebuild. Restore only if the task is
  // changed to `node .next/standalone/server.js`.
  images: {
    unoptimized: true,   // No external image optimizer needed
  },
  // Production optimizations
  poweredByHeader: false,
  compress: true,
  // Environment-based API URL. Default is the same-origin proxy so the
  // API key stays server-side and other LAN devices can use the dashboard.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "/api/pai",
  },
  // Three.js + TalkingHead loaded client-side via dynamic(() => import(), { ssr: false })
  // TalkingHead's dynamic lipsync imports are patched to static import map via postinstall.
};

export default nextConfig;
