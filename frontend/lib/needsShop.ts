/**
 * The pages that show nothing without a shop, and the one sentence each says instead
 * (components/AppShell.tsx renders it with one button: "Connect your shop"). Before this, each
 * showed zeros, filters or its own wording (docs/app-audit.md, 10).
 */
export const NEEDS_SHOP: Record<string, string> = {
  "/profiles": "A profile copies category, price and shipping from one of your own listings. Connect your shop to pick one.",
  "/analytics": "Profit, costs and sales from your own shop appear here once it is connected.",
  "/listing-health": "Your shop's active listings are checked here once it is connected.",
  "/season-planner": "Plan when seasonal listings go live in each shop, once a shop is connected.",
};

export function needsShop(pathname: string, shops: number | null): string | null {
  if (shops === null || shops > 0) return null;
  return NEEDS_SHOP[pathname] ?? null;
}
