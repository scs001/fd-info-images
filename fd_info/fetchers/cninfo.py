"""巨潮公告 fetcher。

直接调 CNINFO 的公开 JSON 接口（与 fd-cn-report 同一端点），只取列表元数据；
正文（PDF）不在 v1 转换范围内，`body` 留空 → `body_status=failed`，如实标注。

``transport`` 可注入：测试用固定响应，绝不真打网。
"""

from __future__ import annotations

from typing import Any, Callable

import httpx

from ..models import SourceResult
from .base import FetchWindow, item_from_row, parse_list_date

ENDPOINT = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
SOURCE = "cninfo"
PAGE_SIZE = 30


class CninfoSource:
    name = SOURCE

    def __init__(
        self,
        transport: Callable[[dict[str, str]], dict[str, Any]] | None = None,
        *,
        columns: tuple[str, ...] = ("sse", "szse", "bse"),
        max_pages: int = 20,
    ) -> None:
        self._transport = transport or self._http
        self.columns = columns
        self.max_pages = max_pages

    def fetch(self, since: str, until: str) -> SourceResult:
        window = FetchWindow(since, until)
        items = []
        missing: list[str] = []
        for column in self.columns:
            try:
                items.extend(self._fetch_column(column, window))
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                missing.append(f"{column}: {exc}")
        if missing and not items:
            return SourceResult(SOURCE, "unavailable", reason="; ".join(missing))
        if missing:
            return SourceResult(
                SOURCE, "partial", items=tuple(items), reason="部分板块拉取失败",
                missing=tuple(missing), window_checked=True,
            )
        return SourceResult(SOURCE, "ok", items=tuple(items), window_checked=True)

    def _fetch_column(self, column: str, window: FetchWindow) -> list:
        out = []
        for page in range(1, self.max_pages + 1):
            payload = self._transport(
                {
                    "column": column,
                    "tabName": "fulltext",
                    "pageSize": str(PAGE_SIZE),
                    "pageNum": str(page),
                    "seDate": f"{window.since}~{window.until}",
                    "sortName": "",
                    "sortType": "",
                    "isHLtitle": "true",
                }
            )
            rows = payload.get("announcements") or []
            if not rows:
                break
            for row in rows:
                items = self._to_item(row)
                if items is not None:
                    out.append(items)
            if len(rows) < PAGE_SIZE:
                break
        return out

    def _to_item(self, row: dict[str, Any]):
        title = (row.get("announcementTitle") or "").strip()
        source_id = str(row.get("announcementId") or "").strip()
        published = row.get("announcementTime")
        if not (title and source_id and published):
            return None
        # CNINFO 的 announcementTime 是毫秒时间戳
        if isinstance(published, (int, float)):
            from datetime import datetime, timezone

            published = datetime.fromtimestamp(published / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        adjunct = (row.get("adjunctUrl") or "").strip()
        url = f"https://static.cninfo.com.cn/{adjunct}" if adjunct else None
        return item_from_row(
            source=SOURCE,
            source_id=source_id,
            source_type="announcement",
            published=parse_list_date(str(published))[:10],
            title=title,
            url=url,
        )

    @staticmethod
    def _http(data: dict[str, str]) -> dict[str, Any]:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.post(
                ENDPOINT,
                data=data,
                headers={
                    "User-Agent": "fd-info/0.1 (+https://github.com/FindDataTechnology)",
                    "Referer": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
                },
            )
            resp.raise_for_status()
            return resp.json()


def fetch_body(url: str, *, download: Callable[[str], bytes] | None = None) -> str | None:
    """公告 PDF 正文：下载 + pypdf 提取；失败返回 None（调用方标 failed）。

    v2（2026-10-26）：v1 只取列表元数据、正文一律 failed（2019 条存量）；
    巨潮公告正文即 PDF（static.cninfo.com.cn/finalpage/*.PDF），本函数补上
    提取链路。``download`` 可注入（测试用固定字节，绝不真打网）。
    """
    if not url:
        return None
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        data = (download or _download_pdf)(url)
        if not data:
            return None
        import io

        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        return text or None
    except Exception:  # noqa: BLE001 — 上游千奇百怪，统一按不可取
        return None


def _download_pdf(url: str) -> bytes:
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(
            url,
            headers={"User-Agent": "fd-info/0.1 (+https://github.com/FindDataTechnology)"},
        )
        resp.raise_for_status()
        return resp.content
