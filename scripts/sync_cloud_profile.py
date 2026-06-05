#!/usr/bin/env python3
"""Sync local Chrome login to Browser Use Cloud profile for GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_config_profile_id() -> str | None:
    cfg = Path.home() / ".browser-use" / "config.json"
    if not cfg.exists():
        return None
    data = json.loads(cfg.read_text(encoding="utf-8"))
    return data.get("cloud_connect_profile_id")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync 10times login to cloud profile")
    parser.add_argument(
        "--profile",
        default="Default",
        help='Chrome profile with 10times login',
    )
    args = parser.parse_args()

    api_key = os.getenv("BROWSER_USE_API_KEY")
    if not api_key:
        print("Set BROWSER_USE_API_KEY first (browser-use cloud login <key>)", file=sys.stderr)
        return 1

    profile_id = read_config_profile_id()
    if not profile_id:
        print("No cloud_connect_profile_id in ~/.browser-use/config.json", file=sys.stderr)
        print("Run: browser-use cloud connect  (once locally)", file=sys.stderr)
        return 1

    env = os.environ.copy()
    env["BROWSER_USE_API_KEY"] = api_key
    env["BROWSER_USE_CLOUD_PROFILE_ID"] = profile_id

    subprocess.run(
        [
            "browser-use",
            "--profile",
            args.profile,
            "open",
            "https://10times.com/zh-CN/power-energy/tradeshows",
        ],
        check=True,
    )
    subprocess.run(
        ["browser-use", "profile", "sync", "--all"],
        check=True,
        env=env,
    )
    subprocess.run(["browser-use", "close"], check=False)

    print("Cloud profile synced.")
    print(f"BROWSER_USE_CLOUD_PROFILE_ID={profile_id}")
    print("Add both BROWSER_USE_API_KEY and BROWSER_USE_CLOUD_PROFILE_ID to GitHub secrets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
