import pytest

from fd_info.models import InfoItem, SourceResult
from fd_info.pipeline import ingest_result
from fd_info.body_store import MemoryBodyStore
from fd_info.storage import Store


def item(source_id="a1", title="公告一", published="2026-10-20", **kw):
    return InfoItem(
        source="cninfo",
        source_id=source_id,
        source_type="announcement",
        published_at=published,
        title=title,
        **kw,
    )


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "info.db")
    yield s
    s.close()


def test_upsert_is_idempotent(store):
    created, changed, item_id = store.upsert(item())
    assert (created, changed) == (True, True)
    created, changed, again = store.upsert(item())
    assert (created, changed, again) == (False, False, item_id)
    assert len(store.list_since()["items"]) == 1


def test_content_change_updates_same_item(store):
    _, _, item_id = store.upsert(item())
    created, changed, again = store.upsert(item(title="公告一（修订）"))
    assert (created, changed, again) == (False, True, item_id)
    assert store.get_item(item_id)["title"] == "公告一（修订）"


def test_cursor_is_stable_when_new_rows_arrive(store):
    for i in range(5):
        store.upsert(item(source_id=f"a{i}", published=f"2026-10-2{i}"))
    first = store.list_since(limit=2)
    # 翻页途中插入一条更新的条目
    store.upsert(item(source_id="new", published="2026-10-29"))
    second = store.list_since(limit=2, cursor=first["next_cursor"])
    first_ids = {r["item_id"] for r in first["items"]}
    second_ids = {r["item_id"] for r in second["items"]}
    assert not (first_ids & second_ids)
    assert len(second_ids) == 2


def test_withdrawal_is_recorded_not_deleted(store):
    _, _, item_id = store.upsert(item())
    store.upsert(item(withdrawn=True))
    assert store.get_item(item_id)["withdrawn"] is True
    assert len(store.list_since()["items"]) == 1


def test_health_tracks_failures(store):
    store.set_source_state("cninfo", ok=True, watermark_published="2026-10-20T00:00:00Z")
    assert store.health()[0]["status"] == "ok"
    store.set_source_state("cninfo", ok=False, error="403")
    row = store.health()[0]
    assert row["status"] == "unavailable"
    assert row["consecutive_failures"] == 1


def test_pipeline_marks_body_stored_or_failed(store):
    store_body = MemoryBodyStore()
    ok = SourceResult(
        "cninfo", "ok",
        items=(item(source_id="with-body", body="正文"), item(source_id="no-body")),
        window_checked=True,
    )
    summary = ingest_result(ok, store, store_body)
    assert summary.body_stored == 1 and summary.body_failed == 1
    rows = {r["source_id"]: r for r in store.list_since()["items"]}
    assert rows["with-body"]["body_status"] == "stored"
    assert rows["no-body"]["body_status"] == "failed"


def test_unavailable_source_is_recorded_not_raised(store):
    result = SourceResult("cninfo", "unavailable", reason="403")
    summary = ingest_result(result, store, MemoryBodyStore())
    assert summary.status == "unavailable"
    assert store.health()[0]["status"] == "unavailable"


def test_body_fetcher_receives_item_url(store):
    """回归（2026-10-27 真缺陷）：BodyFetcher 是 url 签名；历史实现按 item 调用
    导致摄取期正文从未成功（ministry/cninfo 均被误判为上游失败）。"""
    calls: list[str] = []

    def fetcher(url: str) -> str | None:
        calls.append(url)
        return "拉到的正文"

    body_store = MemoryBodyStore()
    result = SourceResult(
        "cninfo", "ok",
        items=(item(source_id="u1", url="https://example.test/a.PDF"),),
        window_checked=True,
    )
    summary = ingest_result(result, store, body_store, body_fetcher=fetcher)
    assert calls == ["https://example.test/a.PDF"], "fetcher 必须收到 url 字符串"
    assert summary.body_stored == 1
    rows = store.list_since()["items"]
    assert rows[0]["body_status"] == "stored"


def test_body_fetcher_skipped_when_no_url(store):
    calls: list[str] = []

    result = SourceResult(
        "cninfo", "ok",
        items=(item(source_id="u2"),),
        window_checked=True,
    )
    summary = ingest_result(result, store, MemoryBodyStore(),
                            body_fetcher=lambda u: calls.append(u) or "x")
    assert calls == [] and summary.body_failed == 1
