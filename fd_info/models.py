"""InfoItem 与来源三态契约（spec: info-stream）。

两个不变量放在这里而不是存储层：

1. ``item_id`` 只由 ``(source, source_id)`` 派生——标题或正文修订不得改变身份。
2. 来源返回三态，且"空结果"不能被默认当成成功：``ok`` 必须显式声明已检查过窗口。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SOURCE_TYPES = ("announcement", "ministry", "interaction")
BODY_STATUSES = ("pending", "stored", "failed")
SOURCE_STATUSES = ("ok", "unavailable", "partial")

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class InfoError(ValueError):
    """条目或来源结果不合法。调用方据此拒绝写入，不静默修正。"""


def item_id_for(source: str, source_id: str) -> str:
    """稳定身份：与正文无关，同源同标识永远同 id。"""
    if not source or not source_id:
        raise InfoError("source 与 source_id 都不能为空")
    return hashlib.sha1(f"{source}:{source_id}".encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_time(value: Any, field_name: str) -> str:
    """接受 ISO 日期/时间与带偏移的时间，统一为 UTC 的 ``...Z``。"""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not isinstance(value, str) or not value.strip():
        raise InfoError(f"{field_name} 必须是 ISO 时间，收到 {value!r}")
    text = value.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InfoError(f"{field_name} 不是合法 ISO 时间：{value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def content_hash(title: str, summary: str | None, body: str | None) -> str:
    payload = "\n".join((title or "", summary or "", body or ""))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InfoItem:
    source: str
    source_id: str
    source_type: str
    published_at: str
    title: str
    summary: str | None = None
    body: str | None = None          # 不落索引表；交给正文存储
    url: str | None = None
    lang: str = "zh"
    entities: tuple[str, ...] = ()
    doc_id: str | None = None
    body_status: str = "pending"
    withdrawn: bool = False
    updated_at: str | None = None
    ingested_at: str = field(default_factory=utc_now)
    raw_hash: str = ""

    def __post_init__(self) -> None:
        if self.source_type not in SOURCE_TYPES:
            raise InfoError(f"source_type 必须是 {SOURCE_TYPES} 之一，收到 {self.source_type!r}")
        if self.body_status not in BODY_STATUSES:
            raise InfoError(f"body_status 必须是 {BODY_STATUSES} 之一，收到 {self.body_status!r}")
        if not (self.title or "").strip():
            raise InfoError("title 不能为空")
        object.__setattr__(self, "published_at", normalize_time(self.published_at, "published_at"))
        object.__setattr__(self, "ingested_at", normalize_time(self.ingested_at, "ingested_at"))
        if self.updated_at is not None:
            object.__setattr__(self, "updated_at", normalize_time(self.updated_at, "updated_at"))
        if isinstance(self.entities, list):
            object.__setattr__(self, "entities", tuple(self.entities))
        if not self.raw_hash:
            object.__setattr__(
                self, "raw_hash", content_hash(self.title, self.summary, self.body)
            )

    @property
    def item_id(self) -> str:
        return item_id_for(self.source, self.source_id)

    def to_row(self) -> dict[str, Any]:
        """索引表行（不含正文）。"""
        return {
            "item_id": self.item_id,
            "source": self.source,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "published_at": self.published_at,
            "ingested_at": self.ingested_at,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "lang": self.lang,
            "entities": list(self.entities),
            "raw_hash": self.raw_hash,
            "doc_id": self.doc_id,
            "body_status": self.body_status,
            "withdrawn": 1 if self.withdrawn else 0,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class SourceResult:
    """一次拉取的完整交代。

    ``ok`` 必须显式 ``window_checked=True``——否则空列表会被误读成"没有信息"。
    ``partial`` 必须列出缺失项；``unavailable`` 必须给原因。
    """

    source: str
    status: str
    items: tuple[InfoItem, ...] = ()
    reason: str | None = None
    missing: tuple[str, ...] = ()
    window_checked: bool = False
    checked_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.status not in SOURCE_STATUSES:
            raise InfoError(f"status 必须是 {SOURCE_STATUSES} 之一，收到 {self.status!r}")
        if self.status == "unavailable" and not self.reason:
            raise InfoError("unavailable 必须给出原因")
        if self.status == "partial" and not (self.reason and self.missing):
            raise InfoError("partial 必须给出原因与缺失项")
        if self.status == "ok" and not self.window_checked:
            raise InfoError("ok 必须显式声明 window_checked=True（空结果不得冒充成功）")
        if isinstance(self.items, list):
            object.__setattr__(self, "items", tuple(self.items))
        if isinstance(self.missing, list):
            object.__setattr__(self, "missing", tuple(self.missing))

    @property
    def is_usable(self) -> bool:
        return self.status in ("ok", "partial")
