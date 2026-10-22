"""查询面服务：游标、详情、检索、证据定位、健康度。

检索默认走索引（标题/摘要）；注入 ``search_hook`` 时转调 text-core 的
混合检索。降级必须可见（``mode`` 字段），不把索引命中冒充全文语义命中。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .body_store import BodyStore, BodyUnavailable
from .models import InfoItem, SourceResult
from .pipeline import BodyFetcher, IngestSummary, ingest_result
from .storage import Store

SearchHook = Callable[[str, int], list[dict[str, Any]]]


class InfoService:
    def __init__(
        self,
        store: Store,
        body_store: BodyStore,
        *,
        search_hook: SearchHook | None = None,
    ) -> None:
        self.store = store
        self.body_store = body_store
        self.search_hook = search_hook

    def ingest(self, result: SourceResult, *, body_fetcher: BodyFetcher | None = None) -> IngestSummary:
        return ingest_result(result, self.store, self.body_store, body_fetcher=body_fetcher)

    def list_since(self, **kwargs) -> dict[str, Any]:
        return self.store.list_since(**kwargs)

    def get_item(self, item_id: str) -> dict[str, Any]:
        row = self.store.get_item(item_id)
        if row is None:
            raise KeyError(f"条目不存在：{item_id}")
        return row

    def health(self) -> list[dict[str, Any]]:
        return self.store.health()

    def locate(self, doc_id: str, start: int, end: int) -> dict[str, Any]:
        try:
            quote = self.body_store.locate(doc_id, start, end)
        except BodyUnavailable as exc:
            return {"ok": False, "error": str(exc), "doc_id": doc_id}
        return {"ok": True, "doc_id": doc_id, "start": start, "end": end, "quote": quote}

    def search_info(
        self,
        query: str,
        *,
        sources: list[str] | None = None,
        source_types: list[str] | None = None,
        published_after: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if self.search_hook is not None:
            hits = self.search_hook(query, limit)
            items = []
            for hit in hits:
                item_id = hit.get("item_id")
                if not item_id:
                    continue
                row = self.store.get_item(str(item_id))
                if row is None:
                    continue
                if sources and row["source"] not in sources:
                    continue
                if source_types and row["source_type"] not in source_types:
                    continue
                if published_after and row["published_at"] < published_after:
                    continue
                row = {**row, "chunks": hit.get("chunks", [])}
                items.append(row)
            return {"items": items[:limit], "count": len(items[:limit]), "mode": "text-core"}

        # 索引检索：LIKE 兜底。显式 mode 让调用方知道这不是语义检索。
        needle = f"%{query.strip()}%"
        rows = self.store.list_since(limit=500)["items"]
        items = [
            r for r in rows
            if query.strip()
            and (query.strip() in r["title"] or query.strip() in (r.get("summary") or ""))
            and (not sources or r["source"] in sources)
            and (not source_types or r["source_type"] in source_types)
            and (not published_after or r["published_at"] >= published_after)
        ][:limit]
        return {"items": items, "count": len(items), "mode": "index"}
