from fd_info.body_store import MemoryBodyStore
from fd_info.models import InfoItem, SourceResult
from fd_info.service import InfoService
from fd_info.storage import Store
from fd_info.textcore_search import TextCoreSearch


def item(**kw):
    base = dict(
        source="cninfo",
        source_id="a1",
        source_type="announcement",
        published_at="2026-10-20",
        title="季度报告",
    )
    base.update(kw)
    return InfoItem(**base)


def test_textcore_search_parses_structured_content():
    def transport(payload, headers):
        return {
            "result": {
                "structuredContent": {
                    "results": [{"item_id": "x", "score": 0.9, "chunks": [{"start": 0, "end": 2}]}]
                }
            }
        }

    hook = TextCoreSearch("http://unused", transport=transport)
    assert hook("report", 5)[0]["item_id"] == "x"


def test_service_uses_hook_and_filters(tmp_path):
    store = Store(tmp_path / "i.db")
    service = InfoService(
        store,
        MemoryBodyStore(),
        search_hook=lambda q, n: [{"item_id": item().item_id, "chunks": []}],
    )
    service.ingest(SourceResult("cninfo", "ok", items=(item(),), window_checked=True))
    assert service.search_info("report")["mode"] == "text-core"
    assert service.search_info("report", sources=["other"])["count"] == 0
    store.close()


def test_index_search_is_labeled(tmp_path):
    store = Store(tmp_path / "i.db")
    service = InfoService(store, MemoryBodyStore())
    service.ingest(SourceResult("cninfo", "ok", items=(item(),), window_checked=True))
    result = service.search_info("季度")
    assert result["mode"] == "index"
    assert result["count"] == 1
    store.close()


def test_locate_returns_quote_or_structured_error(tmp_path):
    body = MemoryBodyStore()
    service = InfoService(Store(tmp_path / "i.db"), body)
    body.put(item(), "0123456789")
    doc_id = f"cninfo:a1"
    assert service.locate(doc_id, 2, 5)["quote"] == "234"
    bad = service.locate(doc_id, 5, 99)
    assert bad["ok"] is False and "越界" in bad["error"]


def test_textcore_rows_normalized_to_item_id():
    """text-core 真行只带 source/source_id——必须派生 item_id 并聚 chunk（生产首验断层）。"""
    from fd_info.textcore_search import _rows
    payload = {"result": {"structuredContent": {"ok": True, "results": [
        {"source": "sse-interaction", "source_id": "1798400", "chunk_text": "问：…",
         "char_start": 0, "char_end": 10, "document_id": 1},
        {"source": "sse-interaction", "source_id": "1798400", "chunk_text": "答：…",
         "char_start": 10, "char_end": 20, "document_id": 1},
    ]}}}
    rows = _rows(payload)
    assert len(rows) == 2
    assert all(r["item_id"] for r in rows)
    from fd_info.textcore_search import TextCoreSearch
    agg = TextCoreSearch._aggregate(rows)
    assert len(agg) == 1
    assert len(agg[0]["chunks"]) == 2


def test_textcore_failure_raises_not_empty():
    """检索面挂了必须可区分：显式异常，不返回空列表冒充无命中。"""
    import pytest
    from fd_info.textcore_search import TextCoreUnavailable

    def transport(payload, headers):
        raise ValueError("boom")

    with pytest.raises(TextCoreUnavailable):
        TextCoreSearch("http://unused", transport=transport)("report", 5)


def test_search_degrades_to_index_with_reason(tmp_path):
    from fd_info.textcore_search import TextCoreUnavailable

    def broken_hook(query, limit):
        raise TextCoreUnavailable("text-core 不可用：connection refused")

    store = Store(tmp_path / "i.db")
    service = InfoService(store, MemoryBodyStore(), search_hook=broken_hook)
    service.ingest(SourceResult("cninfo", "ok", items=(item(),), window_checked=True))
    result = service.search_info("季度")
    assert result["mode"] == "index"
    assert result["count"] == 1
    assert "connection refused" in result["degraded_reason"]
    store.close()


def test_degraded_empty_result_still_carries_reason(tmp_path):
    from fd_info.textcore_search import TextCoreUnavailable

    def broken_hook(query, limit):
        raise TextCoreUnavailable("timeout")

    store = Store(tmp_path / "i.db")
    service = InfoService(store, MemoryBodyStore(), search_hook=broken_hook)
    result = service.search_info("不存在的词")
    assert result["count"] == 0
    assert result["mode"] == "index"
    assert result["degraded_reason"]
    store.close()
