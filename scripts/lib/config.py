"""Load runtime configuration from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    feishu_webhook_url: str
    listing_url: str
    max_pages: int
    state_path: Path
    page_timeout_ms: int
    dry_run: bool
    crawler_backend: str
    browser_headless: bool
    cf_wait_seconds: int
    llm_api_key: str | None
    llm_base_url: str | None
    llm_model: str | None
    browser_use_api_key: str | None
    browser_use_cloud_profile_id: str | None
    tentimes_cookies_b64: str | None
    tentimes_cookies_path: Path | None

    @classmethod
    def load(cls) -> "Config":
        load_dotenv(ROOT / ".env", override=False)

        cookies_path = os.getenv("TENTIMES_COOKIES_PATH")
        max_pages_raw = os.getenv("MAX_PAGES", "0")

        return cls(
            feishu_webhook_url=os.getenv("FEISHU_WEBHOOK_URL", ""),
            listing_url=os.getenv(
                "LISTING_URL", "https://10times.com/zh-CN/power-energy/tradeshows"
            ),
            max_pages=int(max_pages_raw) if max_pages_raw else 0,
            state_path=ROOT / os.getenv("STATE_PATH", "data/seen_events.json"),
            page_timeout_ms=int(os.getenv("PAGE_TIMEOUT_MS", "90000")),
            dry_run=_bool(os.getenv("DRY_RUN")),
            crawler_backend=os.getenv("CRAWLER_BACKEND", "auto").lower(),
            browser_headless=_bool(os.getenv("BROWSER_HEADLESS"), default=False),
            cf_wait_seconds=int(os.getenv("CF_WAIT_SECONDS", "18")),
            llm_api_key=os.getenv("LLM_API_KEY"),
            llm_base_url=os.getenv("LLM_BASE_URL"),
            llm_model=os.getenv("LLM_MODEL"),
            browser_use_api_key=os.getenv("BROWSER_USE_API_KEY"),
            browser_use_cloud_profile_id=os.getenv("BROWSER_USE_CLOUD_PROFILE_ID"),
            tentimes_cookies_b64=os.getenv("TENTIMES_COOKIES_B64"),
            tentimes_cookies_path=Path(cookies_path) if cookies_path else None,
        )
