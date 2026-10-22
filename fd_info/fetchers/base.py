"""信源 fetcher 基类与共用工具。

每个 fetcher 只做一件事：给定时间窗，返回 :class:`SourceResult`。
失败必须如实（unavailable/partial），不允许返回空列表冒充成功。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from ..models import InfoItem, SourceResult


class Source(Protocol):
    name: str

    def fetch(self, since: str, until: str) -> SourceResult: ...


def day_bounds(days: int = 1, *, now: datetime | None = None) -> tuple[str, str]:
    """返回 ``(since, until)``，UTC ISO 日期。缺省最近 N 天（含今天）。"""
    current = now or datetime.now(timezone.utc)
    start = (current - timedelta(days=max(1, days) - 1)).strftime("%Y-%m-%d")
    end = current.strftime("%Y-%m-%d")
    return start, end


def parse_list_date(raw: str) -> str:
    """把 ``2026-06-25`` / ``2026/06/25`` 规范为午夜 UTC。"""
    text = (raw or "").strip().replace("/", "-")
    if not text:
        raise ValueError("日期为空")
    if len(text) == 10:
        return f"{text}T00:00:00Z"
    return text


@dataclass(frozen=True)
class FetchWindow:
    since: str
    until: str

    def __post_init__(self) -> None:
        for name in ("since", "until"):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) != 10:
                raise ValueError(f"{name} 必须是 YYYY-MM-DD")


def item_from_row(
    *,
    source: str,
    source_id: str,
    source_type: str,
    published: str,
    title: str,
    url: str | None = None,
    summary: str | None = None,
    body: str | None = None,
    entities: tuple[str, ...] = (),
) -> InfoItem:
    return InfoItem(
        source=source,
        source_id=source_id,
        source_type=source_type,
        published_at=parse_list_date(published),
        title=title,
        summary=summary,
        body=body,
        url=url,
        entities=entities,
    )
