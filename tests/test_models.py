from datetime import datetime, timezone

import pytest

from fd_info.models import InfoError, InfoItem, SourceResult, item_id_for


def make(**kw):
    base = dict(
        source="cninfo",
        source_id="ann-1",
        source_type="announcement",
        published_at="2026-10-20",
        title="某公司年度报告",
    )
    base.update(kw)
    return InfoItem(**base)


def test_item_id_is_stable_and_independent_of_title():
    a = make()
    b = make(title="某公司年度报告（修订）")
    assert a.item_id == b.item_id == item_id_for("cninfo", "ann-1")
    assert a.raw_hash != b.raw_hash


def test_time_is_normalized_to_utc_z():
    item = make(published_at="2026-10-20 08:30:00")
    assert item.published_at == "2026-10-20T08:30:00Z"


def test_source_type_and_body_status_are_validated():
    with pytest.raises(InfoError):
        make(source_type="rss")
    with pytest.raises(InfoError):
        make(body_status="half")


def test_ok_requires_window_checked():
    with pytest.raises(InfoError):
        SourceResult(source="cninfo", status="ok")
    assert SourceResult(source="cninfo", status="ok", window_checked=True).is_usable


def test_unavailable_and_partial_need_reason():
    with pytest.raises(InfoError):
        SourceResult(source="cninfo", status="unavailable")
    with pytest.raises(InfoError):
        SourceResult(source="cninfo", status="partial", reason="x")
    assert SourceResult(source="cninfo", status="partial", reason="x", missing=("sse",)).is_usable


def test_ingested_at_defaults_to_now():
    item = make()
    age = datetime.now(timezone.utc) - datetime.strptime(
        item.ingested_at, "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=timezone.utc)
    assert age.total_seconds() < 5
