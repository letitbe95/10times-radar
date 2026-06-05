#!/usr/bin/env python3
"""Scrape 10times power-energy tradeshows and notify Feishu."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.lib.browser import BrowserUse
from scripts.lib.config import Config
from scripts.lib.feishu import notify
from scripts.lib.filter_events import filter_events
from scripts.lib.models import Event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("crawl")


def rows_to_events(rows: list[dict]) -> list[Event]:
    events: list[Event] = []
    for row in rows:
        if not row.get("id") or not row.get("title"):
            continue
        events.append(
            Event(
                id=str(row["id"]),
                title=row.get("title", ""),
                dates=row.get("dates", ""),
                start=row.get("start", ""),
                end=row.get("end", ""),
                city=row.get("city", ""),
                country=row.get("country", ""),
                venue_text=row.get("venue_text", ""),
                description=row.get("description", ""),
                categories=list(row.get("categories", [])),
                url=row.get("url", ""),
                page=int(row.get("page", 0)),
            )
        )
    return events


def save_artifact(path: Path, all_events: list[Event], matched: list[Event]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "total": len(all_events),
                "matched": len(matched),
                "all_events": [e.to_dict() for e in all_events],
                "filtered_events": [e.to_dict() for e in matched],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawl 10times and notify Feishu")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM refinement")
    parser.add_argument("--max-pages", type=int, default=None, help="Override MAX_PAGES")
    parser.add_argument(
        "--artifact",
        default="data/latest_scrape.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    config = Config.load()
    max_pages_override = args.max_pages

    browser = BrowserUse(config)
    all_events: list[Event] = []

    try:
        browser.bootstrap()
        total_pages = browser.estimate_total_pages(config.listing_url)
        page_limit = (
            max_pages_override
            if max_pages_override is not None
            else config.max_pages
        )
        max_pages = page_limit if page_limit > 0 else total_pages
        max_pages = min(max_pages, total_pages)
        logger.info("scraping %s pages (total available: %s)", max_pages, total_pages)

        for page in range(1, max_pages + 1):
            url = BrowserUse.page_url(config.listing_url, page)
            logger.info("page %s/%s: %s", page, max_pages, url)
            rows = browser.scrape_page(url, page)
            page_events = rows_to_events(rows)
            all_events.extend(page_events)
            logger.info("page %s -> %s events (running total %s)", page, len(page_events), len(all_events))

        matched = filter_events(config, all_events, use_llm=not args.no_llm)
        logger.info("matched %s / %s events", len(matched), len(all_events))

        artifact = ROOT / args.artifact
        save_artifact(artifact, all_events, matched)

        notify(
            config.feishu_webhook_url,
            matched,
            len(all_events),
            dry_run=config.dry_run,
        )
        return 0
    except Exception:
        logger.exception("crawl failed")
        return 1
    finally:
        browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
