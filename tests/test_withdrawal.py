"""撤回语义（change wire-info-stream-program 1.1.4）。

design D3：来源侧撤回按状态标记 withdrawn=true，不物理删除。验收：
撤回条目不消失、list_since/get_item 返回撤回状态、正文指针保留。
"""
from __future__ import annotations

import pytest

from fd_info.body_store import MemoryBodyStore
from fd_info.models import InfoItem, SourceResult
from fd_info.pipeline import ingest_result
from fd_info.service import InfoService
from fd_info.storage import Store


def item(**kw):
    return InfoItem(
        source="cninfo",
        source_id="a9",
        source_type="announcement",
        published_at="2026-10-21",
        title="关于收购的公告",
        **kw,
    )


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "info.db")
    yield s
    s.close()


def test_withdrawal_with_same_content_flips_flag_only(store):
    """内容未变的撤回：只翻 withdrawn 位，条目仍是同一条、不消失。"""
    _, _, item_id = store.upsert(item(body="正文"))
    created, changed, again = store.upsert(item(body="正文", withdrawn=True))
    assert (created, changed, again) == (False, True, item_id)

    row = store.get_item(item_id)
    assert row["withdrawn"] is True
    listed = store.list_since()["items"]
    assert [r["item_id"] for r in listed] == [item_id], "撤回条目不得从列表消失"
    assert listed[0]["withdrawn"] is True, "list_since 须返回撤回状态"


def test_withdrawal_via_pipeline_keeps_item_and_body_pointer(store):
    """端到端撤回：先正常入库存正文，来源侧撤回（正文已取不到）。

    修前两处缺陷会在此暴露：
      · pipeline 把撤回当内容变更继续走正文路径 → body_status 被改成
        failed、doc_id 被清空（撤回页 404，正文必然取不到）；
      · storage 变更分支无条件用新行的 NULL doc_id 覆盖旧指针。
    """
    service = InfoService(store, MemoryBodyStore())
    ok = SourceResult("cninfo", "ok", items=(item(body="公告正文全文"),), window_checked=True)
    summary = service.ingest(ok)
    assert summary.body_stored == 1
    item_id = item().item_id
    stored = service.get_item(item_id)
    assert stored["body_status"] == "stored" and stored["doc_id"]

    # 撤回：同一身份，来源侧标记 withdrawn，正文不再随行
    withdrawn = SourceResult("cninfo", "ok", items=(item(withdrawn=True),), window_checked=True)
    summary = service.ingest(withdrawn)

    row = service.get_item(item_id)
    assert row is not None, "撤回条目不消失"
    assert row["withdrawn"] is True, "get_item 须返回撤回状态"
    assert row["doc_id"] == stored["doc_id"], "撤回不得清掉正文指针（text-core 侧不删文档）"
    assert row["body_status"] == "stored", "撤回不得把已存正文改判 failed"
    assert row["published_at"] == stored["published_at"], "首次披露时间不随撤回漂移"

    listed = service.list_since(sources=["cninfo"])["items"]
    assert [r["item_id"] for r in listed] == [item_id]
    assert listed[0]["withdrawn"] is True, "list_since 须返回撤回状态"


def test_withdrawn_then_restored_flips_back(store):
    """撤回是状态不是终点：源侧恢复（如撤回误报）后位可翻回。"""
    _, _, item_id = store.upsert(item(body="正文"))
    store.upsert(item(body="正文", withdrawn=True))
    created, changed, _ = store.upsert(item(body="正文", withdrawn=False))
    assert (created, changed) == (False, True)
    assert store.get_item(item_id)["withdrawn"] is False
