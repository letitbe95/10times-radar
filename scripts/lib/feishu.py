"""Send notifications to Feishu custom bot webhook."""

from __future__ import annotations

import logging
from typing import Iterable

import httpx

from .models import Event

logger = logging.getLogger(__name__)

MAX_CARD_EVENTS = 30
MSG_CHUNK = 3500


def _event_line(event: Event, index: int) -> str:
    reasons = "、".join(event.match_reasons) or "匹配"
    loc = event.venue_text or f"{event.city}, {event.country}".strip(", ")
    return (
        f"{index}. **{event.title}**\n"
        f"   📅 {event.dates}\n"
        f"   📍 {loc}\n"
        f"   🏷 {reasons}\n"
        f"   🔗 {event.url}"
    )


def build_markdown(events: Iterable[Event], total_scraped: int) -> str:
    events = list(events)
    lines = [
        "## 10times 电力能源·贸易展览会雷达",
        f"共抓取 **{total_scraped}** 场，筛选命中 **{len(events)}** 场",
        "",
        "**筛选条件**：南美 / 澳大利亚 / 中东 / 东南亚 / 输配电相关",
        "",
    ]
    for i, event in enumerate(events[:MAX_CARD_EVENTS], 1):
        lines.append(_event_line(event, i))
        lines.append("")
    if len(events) > MAX_CARD_EVENTS:
        lines.append(f"_另有 {len(events) - MAX_CARD_EVENTS} 场未展示，见仓库 artifact_")
    return "\n".join(lines)


def send_text_chunks(webhook_url: str, text: str) -> None:
    if not webhook_url:
        raise ValueError("FEISHU_WEBHOOK_URL is not set")

    chunks: list[str] = []
    current = text
    while len(current) > MSG_CHUNK:
        split_at = current.rfind("\n", 0, MSG_CHUNK)
        if split_at <= 0:
            split_at = MSG_CHUNK
        chunks.append(current[:split_at])
        current = current[split_at:].lstrip()
    chunks.append(current)

    with httpx.Client(timeout=30) as client:
        for chunk in chunks:
            resp = client.post(
                webhook_url,
                json={"msg_type": "text", "content": {"text": chunk}},
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") not in (0, None):
                raise RuntimeError(f"Feishu error: {body}")
            logger.info("feishu chunk sent (%s chars)", len(chunk))


def notify(
    webhook_url: str,
    events: list[Event],
    total_scraped: int,
    *,
    dry_run: bool = False,
) -> None:
    markdown = build_markdown(events, total_scraped)
    if dry_run:
        print(markdown)
        return
    send_text_chunks(webhook_url, markdown)
