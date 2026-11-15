"""Read local Tailscale status. This process never enables Funnel or Serve."""

from __future__ import annotations

import json
import shutil
import subprocess


def _run(args: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(args, check=False, capture_output=True, text=True, timeout=4)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def tailscale_health() -> dict:
    binary = shutil.which("tailscale")
    if not binary:
        return {
            "installed": False,
            "connected": False,
            "dns_name": None,
            "tailscale_ips": [],
            "backend_state": None,
            "serve_enabled": False,
            "funnel_enabled": False,
            "detail": "Tailscale CLI is not installed on this machine. Use scripts/tailscale-serve.ps1 on a signed-in Windows device. Funnel is not used.",
        }
    code, raw = _run([binary, "status", "--json"])
    if code != 0:
        return {
            "installed": True,
            "connected": False,
            "dns_name": None,
            "tailscale_ips": [],
            "backend_state": None,
            "serve_enabled": False,
            "funnel_enabled": False,
            "detail": "tailscale status failed. Sign in to your tailnet. Funnel is not used.",
        }
    try:
        status = json.loads(raw)
    except json.JSONDecodeError:
        status = {}
    self_node = status.get("Self") or {}
    backend = status.get("BackendState")
    dns = (self_node.get("DNSName") or "").rstrip(".") or None
    ips = self_node.get("TailscaleIPs") or []
    _, serve_text = _run([binary, "serve", "status"])
    _, funnel_text = _run([binary, "funnel", "status"])
    lowered = serve_text.lower()
    serve_enabled = "127.0.0.1" in serve_text and "no serve config" not in lowered
    funnel_enabled = "Funnel on" in funnel_text
    return {
        "installed": True,
        "connected": backend == "Running" and bool(self_node.get("Online", True)),
        "dns_name": dns,
        "tailscale_ips": ips,
        "backend_state": backend,
        "serve_enabled": serve_enabled,
        "funnel_enabled": funnel_enabled,
        "detail": "Tailscale Serve is the only supported remote path. Funnel must stay off.",
    }
