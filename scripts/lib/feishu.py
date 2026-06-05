"""Send notifications to Feishu custom bot webhook."""

from __future__ import annotations

import logging
import re
from typing import Iterable

import httpx

from .models import Event

logger = logging.getLogger(__name__)

MAX_CARD_EVENTS = 30


def _clean_text(text: str) -> str:
    if not text:
        return ""
    # Replace multiple whitespaces and newlines to keep layout clean
    cleaned = re.sub(r"\s+", " ", text)
    return cleaned.strip()


def _event_line(event: Event, index: int) -> str:
    reasons = "、".join(event.match_reasons) or "匹配"
    loc = event.venue_text or f"{event.city}, {event.country}".strip(", ")
    
    desc = _clean_text(event.description)
    if len(desc) > 180:
        desc = desc[:180] + "..."
    desc_str = f"   📝 **简介**：{desc}\n" if desc else ""
    
    return (
        f"{index}. **{event.title}**\n"
        f"   📅 {event.dates}\n"
        f"   📍 {loc}\n"
        f"   🏷 {reasons}\n"
        f"{desc_str}"
        f"   🔗 [查看详情]({event.url})"
    )


def build_markdown_content(events: list[Event], total_scraped: int) -> str:
    lines = [
        f"**共抓取 {total_scraped} 场，筛选命中 {len(events)} 场**",
        "**筛选条件**：南美 / 澳大利亚 / 中东 / 东南亚 / 输配电相关",
        "",
    ]
    for i, event in enumerate(events[:MAX_CARD_EVENTS], 1):
        lines.append(_event_line(event, i))
        lines.append("")
        
    if len(events) > MAX_CARD_EVENTS:
        lines.append(f"*另有 {len(events) - MAX_CARD_EVENTS} 场未展示，见仓库 artifact*")
        
    return "\n".join(lines)


def send_interactive_card(webhook_url: str, title: str, markdown_content: str) -> None:
    if not webhook_url:
        raise ValueError("FEISHU_WEBHOOK_URL is not set")

    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {
                "wide_screen_mode": True,
                "enable_forward": True
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": title
                },
                "template": "orange"  # Orange template matches radar logo theme
            },
            "elements": [
                {
                    "tag": "markdown",
                    "content": markdown_content
                }
            ]
        }
    }

    with httpx.Client(timeout=30) as client:
        resp = client.post(webhook_url, json=payload)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") not in (0, None):
            raise RuntimeError(f"Feishu error: {body}")
        logger.info("Feishu interactive card sent successfully")


def notify(
    webhook_url: str,
    events: list[Event],
    total_scraped: int,
    *,
    dry_run: bool = False,
) -> None:
    title = "10times 电力能源·贸易展览会雷达"
    markdown_content = build_markdown_content(events, total_scraped)
    if dry_run:
        print(f"=== {title} ===")
        print(markdown_content)
        return
    send_interactive_card(webhook_url, title, markdown_content)
