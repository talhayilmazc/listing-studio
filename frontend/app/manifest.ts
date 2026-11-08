import type { MetadataRoute } from "next";
import { BRAND_FILES } from "@/lib/marketing/brand";

/** The web manifest: the app's name and icons (the 512 px one maskable), in the brand's colours. */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Listyro",
    short_name: "Listyro",
    start_url: "/dashboard",
    display: "standalone",
    background_color: "#FFFFFF",
    theme_color: "#14162B",
    icons: [
      { src: BRAND_FILES.icon, sizes: "any", type: "image/svg+xml" },
      { src: BRAND_FILES.icon192, sizes: "192x192", type: "image/png" },
      { src: BRAND_FILES.icon512Maskable, sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
