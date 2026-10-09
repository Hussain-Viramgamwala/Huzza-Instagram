#!/usr/bin/env python3
"""
Keep Instagram tokens alive.

Long-lived Instagram tokens last 60 days. Refreshing one (it must be at least
24 hours old and not yet expired) gives a fresh 60 days. The workflow runs this
weekly and saves each new token back into the repo's secrets with the GitHub CLI.
"""
from __future__ import annotations

import os
import subprocess
import sys

import requests

sys.path.insert(0, os.path.dirname(__file__))
from publish import GRAPH, load_accounts, secret  # noqa: E402


def refresh(token: str) -> tuple[str, int]:
    r = requests.get(f"{GRAPH}/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": token}, timeout=30)
    body = r.json()
    if r.status_code >= 400 or "access_token" not in body:
        raise RuntimeError(body.get("error", {}).get("message", str(body)))
    return body["access_token"], int(body.get("expires_in", 0))


def main() -> int:
    failures = 0
    can_save = bool(os.environ.get("GH_TOKEN"))
    for name, cfg in load_accounts().items():
        secret_name = cfg.get("token_secret")
        token = secret(secret_name) if secret_name else None
        if not token:
            print(f"⚠️ {name}: no token in secret {secret_name}, skipping")
            continue
        try:
            new_token, expires_in = refresh(token)
        except Exception as e:
            print(f"❌ {name}: refresh failed: {e}")
            failures += 1
            continue
        days = expires_in // 86400
        if can_save:
            subprocess.run(["gh", "secret", "set", secret_name], input=new_token,
                           text=True, check=True)
            print(f"✅ {name}: refreshed, valid for {days} more days, saved to {secret_name}")
        else:
            print(f"✅ {name}: refreshed ({days} days) but GH_PAT isn't set, so it wasn't saved")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
