from fd_info.body_store import BodyUnavailable, SpoolBodyStore
from fd_info.models import InfoItem

import pytest


def item(**kw):
    base = dict(
        source="cn-gov",
        source_id="http://x/1.html",
        source_type="ministry",
        published_at="2026-10-21",
        title="通知",
    )
    base.update(kw)
    return InfoItem(**base)


def test_spool_writes_jsonl_and_locates_quote(tmp_path):
    store = SpoolBodyStore(tmp_path)
    it = item()
    doc_id = store.put(it, "政策全文：自 2026 年起施行。")
    assert doc_id == "cn-gov:http://x/1.html"
    assert (tmp_path / "cn-gov" / "structured" / "2026-10-21.jsonl").exists()
    assert store.locate(doc_id, 0, 6) == "政策全文：自"


def test_spool_missing_body_and_bounds(tmp_path):
    store = SpoolBodyStore(tmp_path)
    with pytest.raises(BodyUnavailable):
        store.locate("cn-gov:none", 0, 1)
    doc_id = store.put(item(), "abc")
    with pytest.raises(BodyUnavailable):
        store.locate(doc_id, 2, 99)
