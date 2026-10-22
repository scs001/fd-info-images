"""正文版本化在索引侧的验证（change wire-info-stream-program 1.1.3）。

同 (source, source_id) 三次内容变更走 pipeline：
info_items 始终一行（unique(source, source_id) 无冲突）、doc_id 回填指向
最新版、published_at 保持首次披露时间；旧版正文仍可定位（历史可查）。
正文版本本体在 fd-text-core（corpus_documents superseded/current），
那侧的验证见 fd-text-core tests/test_news_versioning.py。
"""
from __future__ import annotations

import pytest

from fd_info.models import InfoItem, SourceResult, content_hash
from fd_info.pipeline import ingest_result
from fd_info.storage import Store


class VersioningBodyStore:
    """模拟 text-core 版本化正文库：每次 put 产出一个新 doc_id，旧版保留。"""

    def __init__(self) -> None:
        self.docs: dict[str, str] = {}
        self.put_log: list[str] = []

    def put(self, item: InfoItem, body: str) -> str:
        doc_id = f"{item.source}:{item.source_id}:v{len(self.put_log) + 1}"
        self.docs[doc_id] = body
        self.put_log.append(doc_id)
        return doc_id

    def locate(self, doc_id: str, start: int, end: int) -> str:
        body = self.docs.get(doc_id)
        if body is None:
            raise KeyError(doc_id)
        assert 0 <= start < end <= len(body)
        return body[start:end]


def item(body: str) -> InfoItem:
    return InfoItem(
        source="cninfo",
        source_id="b7",
        source_type="announcement",
        published_at="2026-10-22",
        title="修订中的公告",
        body=body,
    )


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "info.db")
    yield s
    s.close()


def test_three_versions_single_row_doc_id_points_to_latest(store):
    bodies = ("第一版正文。", "第二版正文，修订了金额。", "第三版正文，再次修订。")
    bodies_store = VersioningBodyStore()

    for body in bodies:
        result = SourceResult("cninfo", "ok", items=(item(body),), window_checked=True)
        summary = ingest_result(result, store, bodies_store)
        assert summary.created == 0 or summary.changed == 1, summary.to_dict()

    items = store.list_since()["items"]
    assert len(items) == 1, "三次修订不得产生新条目（unique(source, source_id) 无冲突）"
    row = items[0]

    assert row["doc_id"] == "cninfo:b7:v3", "doc_id 回填须指向最新版"
    assert row["raw_hash"] == content_hash("修订中的公告", None, bodies[2])
    assert row["published_at"] == "2026-10-22T00:00:00Z", "published_at 保持首次披露时间"
    assert row["updated_at"], "修订轮次应落 updated_at"
    assert row["body_status"] == "stored"
    assert len(bodies_store.put_log) == 3

    # 旧版正文仍可定位：历史可查，不随版本退役消失
    for n, body in enumerate(bodies, start=1):
        assert bodies_store.locate(f"cninfo:b7:v{n}", 0, 3) == body[:3]

    # 同内容重放：零副作用，不写第四版
    result = SourceResult("cninfo", "ok", items=(item(bodies[2]),), window_checked=True)
    summary = ingest_result(result, store, bodies_store)
    assert summary.unchanged == 1 and summary.body_stored == 0
    assert len(bodies_store.put_log) == 3
    assert store.get_item(row["item_id"])["doc_id"] == "cninfo:b7:v3"
