"""A stand-in for Etsy's OAuth pages in the shop-connection browser test (docker-compose.oauth-e2e.yml).

* GET  /oauth/connect?redirect_uri=…&state=…  an "authorize" page with one "Allow access" link back to
  the redirect URI with a code and the state (the seller approving on Etsy).
* POST /v3/public/oauth/token                   tokens for that code (synthetic: user 4242; the
                                                "#allow-own-shop" link stands for another seller, with a
                                                user of their own, so each first-run sign-up connects its
                                                own shop: frontend/e2e/firstRun.spec.ts).
* GET  /health                                  for the compose healthcheck.

Standard library only; nothing is stored; it never talks to Etsy.
"""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse


class Stub(BaseHTTPRequestHandler):
    def _send(self, status: int, body: str, kind: str = "text/html; charset=utf-8") -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if url.path == "/health":
            return self._send(200, "ok", "text/plain")
        if url.path != "/oauth/connect":
            return self._send(404, "not found", "text/plain")
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if q.get("response_type") != "code" or not q.get("state") or not q.get("redirect_uri") or not q.get("code_challenge"):
            return self._send(400, "bad authorize request", "text/plain")
        back = q["redirect_uri"] + "?" + urlencode({"code": "stub-code-" + q["state"][:8], "state": q["state"]})
        own = q["redirect_uri"] + "?" + urlencode({"code": "stub-code-own-" + q["state"][:8], "state": q["state"]})
        self._send(200, f"""<!doctype html><title>Etsy authorize (stub)</title>
<h1>Allow Listyro to access your shop? (stub)</h1>
<a id="allow" href="{html.escape(back)}">Allow access</a>
<a id="allow-own-shop" href="{html.escape(own)}">Allow access (another seller)</a>""")

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/v3/public/oauth/token":
            return self._send(404, "not found", "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        if form.get("grant_type") != "authorization_code" or not form.get("code", "").startswith("stub-code-") \
                or not form.get("code_verifier"):
            return self._send(400, json.dumps({"error": "invalid_grant", "error_description": "stub: bad code"}),
                              "application/json")
        code = form["code"]
        # Another seller: a user id of their own, from the code (the same approval, the same user).
        user = 4242 if not code.startswith("stub-code-own-") else 5000 + sum(code.encode()) % 4000
        tokens = {"access_token": f"{user}.stub-access", "refresh_token": f"{user}.stub-refresh", "expires_in": 3600,
                  "token_type": "Bearer", "scope": "listings_r listings_w shops_r shops_w transactions_r"}
        self._send(200, json.dumps(tokens), "application/json")

    def log_message(self, *_: object) -> None:  # quiet
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 9911), Stub).serve_forever()
