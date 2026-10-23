"""The Cloudflare tunnel (docs/deploy.md, "Tunnel protocol"; 2026-10-23).

HTTP/2, not QUIC: QUIC streams timed out on the server's 14 Mbps uplink, the tunnel dropped
and visitors saw "SSL handshake failed". Pinned to an exact cloudflared release, restarted
when it exits, and /api/* goes from the edge straight to the API (same host, so cookies,
the CSRF check and the client address are as before). deploy/preflight.sh warns if the
protocol is not http2.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.test_preflight_script import _run

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "docker-compose.prod.yml"
CONFIG = ROOT / "deploy" / "cloudflared" / "config.yml"
OK = "  ok    tunnel protocol http2"


def _files() -> tuple[dict, dict]:
    if not COMPOSE.exists() or not CONFIG.exists():
        pytest.skip("the deploy files are not here")
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8")), yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_the_tunnel_runs_http2_pinned_and_restarting() -> None:
    compose, _ = _files()
    tunnel = compose["services"]["cloudflared"]
    assert re.fullmatch(r"cloudflare/cloudflared:\d{4}\.\d+\.\d+", tunnel["image"]), tunnel["image"]
    command = tunnel["command"]
    assert command[command.index("--protocol") + 1] == "http2"
    assert tunnel["environment"]["TUNNEL_TRANSPORT_PROTOCOL"] == "http2"
    assert tunnel["restart"] in ("unless-stopped", "always")
    # The setup commands in docs/deploy.md run the same release.
    assert tunnel["image"] in (ROOT / "docs" / "deploy.md").read_text(encoding="utf-8")


def test_api_requests_go_straight_to_the_api_after_the_admin_refusal() -> None:
    _, config = _files()
    rules = config["ingress"]
    main = [r for r in rules if r.get("hostname") == "listyro.com"]
    assert [(r.get("path"), r["service"]) for r in main] == [
        ("^/api/account/admin/", "http_status:404"),
        ("^/api/", "http://api:8000"),
        (None, "http://frontend:3000"),
    ]
    assert not any(r.get("protocol") for r in rules) and "protocol" not in config
    # api.listyro.com still only answers Etsy's OAuth callback and the uptime check.
    api = [(r.get("path"), r["service"]) for r in rules if r.get("hostname") == "api.listyro.com"]
    assert api == [("^/api/auth/etsy/callback$", "http://api:8000"), ("^/health$", "http://api:8000"), (None, "http_status:404")]


def _tunnel_compose(command: str = "", environment: str = "") -> str:
    out = "services:\n  api:\n    environment:\n      STORAGE_DIR: /data/storage\n  cloudflared:\n    image: cloudflare/cloudflared:2026.10.3\n"
    if command:
        out += f"    command: {command}\n"
    if environment:
        out += f"    environment:\n      {environment}\n"
    return out + "    restart: unless-stopped\n"


@pytest.mark.parametrize(("command", "environment"), [
    ('["tunnel", "--no-autoupdate", "--protocol", "http2", "--config", "/etc/cloudflared/config.yml", "run"]', ""),
    ('["tunnel", "run"]', "TUNNEL_TRANSPORT_PROTOCOL: http2"),
])
def test_preflight_says_ok_for_http2(tmp_path, command: str, environment: str) -> None:
    out = _run(tmp_path, "APP_ENV=production\n", _tunnel_compose(command, environment))
    assert OK in out.stdout, out.stdout + out.stderr


@pytest.mark.parametrize(("command", "environment", "said"), [
    ('["tunnel", "run"]', "", "not set (cloudflared then uses QUIC)"),
    ('["tunnel", "--protocol", "quic", "run"]', "", "is quic"),
    ('["tunnel", "run"]', "TUNNEL_TRANSPORT_PROTOCOL: auto", "is auto"),
])
def test_preflight_warns_when_it_is_not_http2(tmp_path, command: str, environment: str, said: str) -> None:
    out = _run(tmp_path, "APP_ENV=production\n", _tunnel_compose(command, environment))
    assert "  warn  the tunnel protocol " in out.stdout and said in out.stdout, out.stdout + out.stderr
    assert OK not in out.stdout
    assert "FAIL  the tunnel protocol" not in out.stdout  # a warning: the site still works on QUIC


def test_preflight_on_the_real_compose_file(tmp_path) -> None:
    _files()
    out = _run(tmp_path, "APP_ENV=production\n", COMPOSE.read_text(encoding="utf-8"))
    assert OK in out.stdout, out.stdout + out.stderr
