import type { MetadataRoute } from "next";

// Web App Manifest — makes PuffPAI installable to a phone home screen.
// Next serves this at /manifest.webmanifest and auto-links it from <head>.
// start_url is /send: the phone's job is to send Puff a photo or file, so the
// installed icon opens straight into the send flow. Colors match the dark
// galaxy theme (deep plum) so the splash/standalone chrome is on-brand.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "PuffPAI: Send to Puff",
    short_name: "Puff",
    description: "Send photos and documents to Puff, your private, on-device AI.",
    start_url: "/send",
    scope: "/",
    display: "standalone",
    background_color: "#160B28",
    theme_color: "#160B28",
    orientation: "portrait",
    icons: [
      { src: "/puffpai-icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/puffpai-icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/puffpai-icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
