#!/usr/bin/env python3
"""Export 10times cookies via browser-use for CI secrets."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "10times_cookies.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export 10times cookies")
    parser.add_argument(
        "--profile",
        default="Default",
        help='Chrome profile name (see `browser-use profile list`)',
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON path")
    parser.add_argument(
        "--b64",
        action="store_true",
        help="Print base64 for GitHub secret TENTIMES_COOKIES_B64",
    )
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

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
        ["browser-use", "cookies", "export", str(out)],
        check=True,
    )
    subprocess.run(["browser-use", "close"], check=False)

    data = out.read_text(encoding="utf-8")
    json.loads(data)
    print(f"exported {out}")

    if args.b64:
        encoded = base64.b64encode(out.read_bytes()).decode()
        print("\n# Add to GitHub secret TENTIMES_COOKIES_B64:")
        print(encoded)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
