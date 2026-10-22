"""部委发布 fetcher：复用 fd-cn-gov 的 11 个列表脚本。

不在本仓复制选择器。适配器动态导入 ``fd_cn_gov.scripts.<name>``，读其
MANIFEST.archives 与 ``crawl_archive``——原仓零改动；未安装 fd-cn-gov 时
如实返回 unavailable，不假装成功。

正文（详情页）由 ``fd_cn_gov.detail.fetch_detail`` 取；同样为可选依赖。
"""

from __future__ import annotations

import importlib
from typing import Callable, Iterable

from ..models import InfoItem, SourceResult
from .base import FetchWindow, item_from_row

SOURCE = "cn-gov"

#: 11 个部委脚本（与 fd-cn-gov/fd_cn_gov/scripts/ 同名）
SCRIPTS: tuple[str, ...] = (
    "mee_gsgg_archive",
    "mem_tzgg_archive",
    "mnr_tzgg_archive",
    "moa_govpublic_archive",
    "mof_gkml_archive",
    "mofcom_xwfb_archive",
    "mohurd_xinwen_archive",
    "mot_shuju_archive",
    "ndrc_tzgg_archive",
    "pbc_xinwen_archive",
    "safe_whxw_archive",
)


class MinistrySource:
    name = SOURCE

    def __init__(
        self,
        *,
        scripts: Iterable[str] = SCRIPTS,
        module_loader: Callable[[str], object] | None = None,
        max_pages: int = 3,
    ) -> None:
        self.scripts = tuple(scripts)
        self._load = module_loader or self._import
        self.max_pages = max_pages

    def fetch(self, since: str, until: str) -> SourceResult:
        window = FetchWindow(since, until)
        items: list[InfoItem] = []
        missing: list[str] = []
        for name in self.scripts:
            try:
                module = self._load(name)
                records = _crawl(module, self.max_pages)
                items.extend(_within(records, window, name))
            except Exception as exc:  # noqa: BLE001 - 单源失败是一条数据，不是整批中断
                missing.append(f"{name}: {exc}")
        if missing and not items:
            return SourceResult(SOURCE, "unavailable", reason="; ".join(missing))
        if missing:
            return SourceResult(
                SOURCE, "partial", items=tuple(items),
                reason="部分部委拉取失败", missing=tuple(missing), window_checked=True,
            )
        return SourceResult(SOURCE, "ok", items=tuple(items), window_checked=True)

    @staticmethod
    def _import(name: str):
        return importlib.import_module(f"fd_cn_gov.scripts.{name}")


def _crawl(module: object, max_pages: int) -> list[dict]:
    crawl = getattr(module, "crawl_archive", None)
    archives = getattr(getattr(module, "MANIFEST", None), "archives", None) or []
    if crawl is None:
        raise RuntimeError("模块没有 crawl_archive")
    rows: list[dict] = []
    for archive in archives:
        section = getattr(archive, "section", "")
        subsection = getattr(archive, "subsection", section)
        url = getattr(archive, "url", "")
        rows.extend(crawl(section, subsection, url, max_pages))
    return rows


def _within(records: list[dict], window: FetchWindow, script: str) -> list[InfoItem]:
    out: list[InfoItem] = []
    for row in records:
        date = str(row.get("date") or "")[:10]
        if not date or date < window.since or date > window.until:
            continue
        url = str(row.get("url") or "").strip()
        title = str(row.get("title") or "").strip()
        if not (url and title):
            continue
        out.append(
            item_from_row(
                source=SOURCE,
                source_id=url,
                source_type="ministry",
                published=date,
                title=title,
                url=url,
            )
        )
    return out


def fetch_body(url: str) -> str | None:
    """详情页正文：可选依赖 fd-cn-gov；失败返回 None（调用方标 failed）。"""
    try:
        from fd_cn_gov.detail.fetch import fetch_detail
    except ImportError:
        return None
    result = fetch_detail(url)
    if not getattr(result, "ok", False):
        return None
    body = (getattr(result, "body", "") or "").strip()
    return body or None
