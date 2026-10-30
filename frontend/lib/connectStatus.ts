/**
 * What the Shops page says when Etsy sends the seller back (backend api/auth.py: the callback
 * redirects to /connect?status=…). Each reason in its own words; every failure offers "Try
 * again" (it starts a new connection). Never "not signed in": the callback runs on another host
 * (api.listyro.com) where the session cookie is not sent, so it cannot know, and a seller who is
 * signed in was told they were not (production, 2026-10-23 to 2026-10-29).
 */

export type Tone = "ok" | "warn" | "bad";

export interface ConnectBanner {
  tone: Tone;
  text: string;
  /** Shows "Try again" (start the connection again). */
  retry: boolean;
}

const AGAIN = "Press Connect a shop again.";

export const TOO_LONG = `The connection took too long or started in another browser. ${AGAIN}`;

const BANNERS: Record<string, { tone: Tone; text: string }> = {
  connected: { tone: "ok", text: "Shop connected. Its listings are being fetched now." },
  error: { tone: "bad", text: "Something went wrong during authorization. Please try again." },
  etsy_error: { tone: "bad", text: "Etsy refused the authorization." },
  denied: { tone: "warn", text: "Authorization was cancelled." },
  no_binding: { tone: "warn", text: TOO_LONG },
  expired: { tone: "warn", text: TOO_LONG },
  // Sent before 2026-10-30 for what is now no_binding; an old link still reads right.
  wrong_account: { tone: "warn", text: TOO_LONG },
  state_mismatch: { tone: "warn", text: `That approval belongs to another connection than the one started in this browser. ${AGAIN}` },
  reused: { tone: "warn", text: `That approval was already used. ${AGAIN}` },
  unconfigured: { tone: "bad", text: "Etsy API credentials are not configured on the server." },
  unavailable: { tone: "bad", text: "This account cannot connect shops right now. Please contact support." },
  taken: {
    tone: "bad",
    text: "That shop is already connected to another account here. A shop can belong to one account at a time.",
  },
  limit_account: {
    tone: "warn",
    text: "Your account has reached its limit of connected shops. Disconnect one, or ask us for a higher limit.",
  },
  limit_app: {
    tone: "warn",
    text: "The service has reached the number of shops it can serve right now, because every shop shares one daily Etsy request budget. Please ask us.",
  },
  limit_paused: { tone: "warn", text: "New shop connections are paused for now. Your existing shops keep working." },
};

/** The banner for a status (and, for other_account, the starting account's masked email). */
export function connectBanner(status: string | null, who: string | null = null): ConnectBanner | null {
  if (!status) return null;
  if (status === "other_account") {
    return {
      tone: "warn",
      text: `That connection was started by ${who || "another account"}, and this browser is now signed in to a different account. Sign in to that account, or ${AGAIN.toLowerCase()}`,
      retry: true,
    };
  }
  const found = BANNERS[status];
  if (!found) return null;
  return { ...found, retry: found.tone !== "ok" && status !== "unconfigured" && status !== "limit_paused" };
}
