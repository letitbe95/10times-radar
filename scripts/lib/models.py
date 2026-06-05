"""Shared data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Event:
    id: str
    title: str
    dates: str
    start: str
    end: str
    city: str
    country: str
    venue_text: str
    description: str
    categories: list[str]
    url: str
    page: int = 0
    match_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        return cls(
            id=data["id"],
            title=data["title"],
            dates=data.get("dates", ""),
            start=data.get("start", ""),
            end=data.get("end", ""),
            city=data.get("city", ""),
            country=data.get("country", ""),
            venue_text=data.get("venue_text", ""),
            description=data.get("description", ""),
            categories=list(data.get("categories", [])),
            url=data.get("url", ""),
            page=int(data.get("page", 0)),
            match_reasons=list(data.get("match_reasons", [])),
        )
