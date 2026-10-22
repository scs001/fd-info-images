"""正文存储抽象：索引在 fd-info，正文进 fd-text-core。

v1 用 ``SpoolBodyStore`` 落 JSONL 暂存（text-core 的既有摄取形态是
"采集器把 JSONL 写进桶，引擎拉取"）。测试用内存实现；接对象存储或
text-core HTTP 摄取时只换实现，不改编排。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from .models import InfoItem


class BodyUnavailable(RuntimeError):
    """正文不可取：条目保留，body_status=failed，不伪造空正文。"""


class BodyStore(Protocol):
    def put(self, item: InfoItem, body: str) -> str:
        """写入正文并返回 doc_id。"""

    def locate(self, doc_id: str, start: int, end: int) -> str:
        """返回 ``doc_id`` 的 ``[start, end)`` 逐字片段。"""


class MemoryBodyStore:
    def __init__(self) -> None:
        self.docs: dict[str, str] = {}

    def put(self, item: InfoItem, body: str) -> str:
        doc_id = item.doc_id or f"{item.source}:{item.source_id}"
        self.docs[doc_id] = body
        return doc_id

    def locate(self, doc_id: str, start: int, end: int) -> str:
        body = self.docs.get(doc_id)
        if body is None:
            raise BodyUnavailable(f"正文不存在：{doc_id}")
        if start < 0 or end > len(body) or start >= end:
            raise BodyUnavailable(f"区间越界：{start}..{end}（正文 {len(body)} 字）")
        return body[start:end]


class SpoolBodyStore:
    """按 ``<root>/<source>/structured/<YYYY-MM-DD>.jsonl`` 追加。

    ``doc_id`` 与条目身份一致，text-core 的 ``(domain, source, source_id)``
    去重可直接复用。
    """

    def __init__(self, root: str | Path, domain: str = "news") -> None:
        self.root = Path(root)
        self.domain = domain

    def put(self, item: InfoItem, body: str) -> str:
        doc_id = item.doc_id or f"{item.source}:{item.source_id}"
        day = item.published_at[:10]
        target = self.root / item.source / "structured" / f"{day}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "domain": self.domain,
            "source": item.source,
            "source_id": item.source_id,
            "title": item.title,
            "content": body,
            "url": item.url,
            "published_at": item.published_at,
            "lang": item.lang,
            "entities": list(item.entities),
        }
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return doc_id

    def locate(self, doc_id: str, start: int, end: int) -> str:
        source, _, source_id = doc_id.partition(":")
        for path in sorted((self.root / source / "structured").glob("*.jsonl"), reverse=True):
            for line in path.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                if rec.get("source") == source and rec.get("source_id") == source_id:
                    body = rec.get("content") or ""
                    if start < 0 or end > len(body) or start >= end:
                        raise BodyUnavailable(f"区间越界：{start}..{end}（正文 {len(body)} 字）")
                    return body[start:end]
        raise BodyUnavailable(f"正文不存在：{doc_id}")
