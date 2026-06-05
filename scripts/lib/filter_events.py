"""Filter events by target regions and power T&D relevance."""

from __future__ import annotations

import json
import logging
import re
from typing import Iterable

from openai import OpenAI

from .config import Config
from .models import Event

logger = logging.getLogger(__name__)

REGION_RULES: dict[str, list[str]] = {
    "南美": [
        "南美",
        "巴西",
        "阿根廷",
        "智利",
        "秘鲁",
        "哥伦比亚",
        "乌拉圭",
        "巴拉圭",
        "玻利维亚",
        "厄瓜多尔",
        "委内瑞拉",
        "苏里南",
        "圭亚那",
        "brazil",
        "argentina",
        "chile",
        "peru",
        "colombia",
        "uruguay",
        "paraguay",
        "bolivia",
        "ecuador",
        "venezuela",
        "são paulo",
        "sao paulo",
        "buenos aires",
        "santiago",
        "lima",
        "bogotá",
        "bogota",
    ],
    "澳大利亚": [
        "澳大利亚",
        "澳洲",
        "悉尼",
        "墨尔本",
        "布里斯班",
        "珀斯",
        "阿德莱德",
        "australia",
        "sydney",
        "melbourne",
        "brisbane",
        "perth",
        "adelaide",
        "canberra",
    ],
    "中东": [
        "中东",
        "迪拜",
        "阿联酋",
        "沙特",
        "卡塔尔",
        "科威特",
        "巴林",
        "阿曼",
        "以色列",
        "约旦",
        "黎巴嫩",
        "伊拉克",
        "伊朗",
        "土耳其",
        "dubai",
        "uae",
        "abu dhabi",
        "saudi",
        "qatar",
        "kuwait",
        "bahrain",
        "oman",
        "israel",
        "jordan",
        "riyadh",
        "jeddah",
        "doha",
        "muscat",
        "manama",
    ],
    "东南亚": [
        "东南亚",
        "新加坡",
        "马来西亚",
        "泰国",
        "印尼",
        "印度尼西亚",
        "菲律宾",
        "越南",
        "缅甸",
        "柬埔寨",
        "老挝",
        "文莱",
        "singapore",
        "malaysia",
        "thailand",
        "indonesia",
        "philippines",
        "vietnam",
        "myanmar",
        "cambodia",
        "laos",
        "brunei",
        "曼谷",
        "吉隆坡",
        "雅加达",
        "马尼拉",
        "河内",
        "胡志明",
        "宿务",
        "槟城",
    ],
}

TD_KEYWORDS = [
    "输配电",
    "输电",
    "配电",
    "电网",
    "变电站",
    "变电",
    "电缆",
    "电线",
    "智能电网",
    "特高压",
    "配网",
    "输变电",
    "电力传输",
    "配电网",
    "switchgear",
    "transformer",
    "substation",
    "transmission",
    "distribution",
    "powerline",
    "power line",
    "smart grid",
    "grid",
    "cable",
    "wire",
    "t&d",
    "electrical grid",
    "power grid",
]


def _haystack(event: Event) -> str:
    parts = [
        event.title,
        event.city,
        event.country,
        event.venue_text,
        event.description,
        " ".join(event.categories),
    ]
    return " ".join(parts).lower()


def rule_match(event: Event) -> list[str]:
    text = _haystack(event)
    reasons: list[str] = []
    for region, keywords in REGION_RULES.items():
        if any(kw.lower() in text for kw in keywords):
            reasons.append(region)
    if any(kw.lower() in text for kw in TD_KEYWORDS):
        reasons.append("输配电")
    return reasons


def llm_refine(
    config: Config, candidates: list[Event]
) -> list[Event]:
    if not candidates or not config.llm_api_key or not config.llm_model:
        return candidates

    client = OpenAI(
        api_key=config.llm_api_key,
        base_url=config.llm_base_url,
    )
    payload = [
        {
            "id": e.id,
            "title": e.title,
            "location": f"{e.city}, {e.country}",
            "description": e.description[:240],
            "categories": e.categories,
        }
        for e in candidates
    ]
    prompt = (
        "你是电力与能源行业展会筛选助手。以下活动已通过初步规则筛选。\n"
        "【重要排除】：请首先排除与电力、能源、电气、输配电等工业领域完全不相关的活动（例如：音乐会、演唱会、歌舞剧、艺术展、体育赛事、数码电子、消费品等娱乐或非能源电力类活动），即使它们举办在目标地区也必须予以排除。\n"
        "对于剩下的活动，请复核是否满足以下任一条件以决定保留：\n"
        "1. 举办地在南美/澳大利亚/中东/东南亚（必须是电力、能源、环保或工业相关展会，排除生活娱乐类）；\n"
        "2. 与输配电（输电、配电、电网、变电站、电缆电线、变压器等）直接相关。\n"
        "仅返回 JSON 数组，元素为应保留的展会 id 字符串。不要输出其它文字。\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        resp = client.chat.completions.create(
            model=config.llm_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=4096,
        )
        content = (resp.choices[0].message.content or "").strip()
        match = re.search(r"\[[\s\S]*\]", content)
        if not match:
            logger.warning("LLM returned non-json, keeping rule matches")
            return candidates
        keep_ids = set(json.loads(match.group()))
        return [e for e in candidates if e.id in keep_ids]
    except Exception:
        logger.exception("LLM refine failed, keeping rule matches")
        return candidates


def filter_events(
    config: Config,
    events: Iterable[Event],
    *,
    use_llm: bool = True,
    batch_size: int = 40,
) -> list[Event]:
    matched: list[Event] = []
    for event in events:
        reasons = rule_match(event)
        if reasons:
            event.match_reasons = reasons
            matched.append(event)

    if not use_llm:
        return matched

    refined: list[Event] = []
    for i in range(0, len(matched), batch_size):
        refined.extend(llm_refine(config, matched[i : i + batch_size]))
    return refined
