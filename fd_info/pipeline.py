"""一轮摄取：拉取结果 → 索引 → 正文 → 水位。

失败是数据，不是控制流：来源不可用时写 source_state 并返回摘要，
不抛异常中断其他来源。正文不可取的条目保留 `body_status=failed`，
绝不把未落正文标成 stored。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .body_store import BodyStore, BodyUnavailable
from .models import InfoItem, SourceResult
from .storage import Store

BodyFetcher = Callable[[InfoItem], str | None]


@dataclass
class IngestSummary:
    source: str
    status: str
    total: int = 0
    created: int = 0
    changed: int = 0
    unchanged: int = 0
    body_stored: int = 0
    body_failed: int = 0
    reason: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def ingest_result(
    result: SourceResult,
    store: Store,
    body_store: BodyStore,
    *,
    body_fetcher: BodyFetcher | None = None,
) -> IngestSummary:
    summary = IngestSummary(source=result.source, status=result.status, total=len(result.items))
    if not result.is_usable:
        summary.reason = result.reason or "来源不可用"
        store.set_source_state(result.source, ok=False, error=summary.reason)
        return summary

    newest = None
    for item in result.items:
        created, changed, item_id = store.upsert(item)
        summary.created += int(created)
        summary.changed += int(changed)
        summary.unchanged += int(not created and not changed)
        if newest is None or (item.published_at, item.item_id) > (newest.published_at, newest.item_id):
            newest = item

        if not (created or changed):
            continue
        if item.withdrawn and not created:
            # 撤回（design D3）：只翻 withdrawn 位即止，正文指针与 body_status
            # 保持原样——不物理删除，text-core 侧文档与历史不动。撤回页通常
            # 已取不到正文，若继续走正文路径会把 doc_id 错误清成 failed。
            continue
        if item.body is None and body_fetcher is not None:
            try:
                body = body_fetcher(item)
            except BodyUnavailable:
                body = None
        else:
            body = item.body
        if body:
            try:
                doc_id = body_store.put(item, body)
                store.mark_body(item_id, doc_id=doc_id, body_status="stored")
                summary.body_stored += 1
            except BodyUnavailable:
                store.mark_body(item_id, doc_id=None, body_status="failed")
                summary.body_failed += 1
        else:
            store.mark_body(item_id, doc_id=None, body_status="failed")
            summary.body_failed += 1

    if newest is not None:
        store.set_source_state(
            result.source,
            ok=True,
            watermark_published=newest.published_at,
            watermark_item_id=newest.item_id,
        )
    else:
        # 合法空窗口：仍推进成功时间，水位保持不变。
        store.set_source_state(result.source, ok=True)
    return summary
