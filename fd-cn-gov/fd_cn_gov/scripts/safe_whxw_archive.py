"""Catalog crawl of SAFE (国家外汇管理局) 外汇新闻 archive.

The 外汇新闻 feed at /safe/whxw/ is SAFE's rolling news stream. Each item:

    <ul><li><dt><a href="/safe/2026/0626/27629.html" title="...">...</a></dt>
        <dd>2026-06-29</dd></li></ul>

Pagination is path-based: page 1 = index.html, page N≥2 = index_N.html
(no offset — page 2 is index_2.html, not index_1.html). The <dd> carries the
clean YYYY-MM-DD display date; the URL path /YYYY/MMDD/ is a fallback (it can
lag the display date by a few days).

Default crawl is 50 pages; --all / --max-pages 0 walks until a 404 or empty page.

Run:  uv run --directory mcp/scrapling-uv-mcp python scripts/safe_whxw_archive.py [--all|--max-pages N]
"""
import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import Fetcher

from fd_cn_gov.scraw_contract import ScrawArchive, ScrawColumn, ScrawManifest

BASE = "https://www.safe.gov.cn"
ARCHIVES = [
    ("外汇新闻", "外汇新闻", f"{BASE}/safe/whxw/index.html"),
]
PER_PAGE = 20
SLEEP = 0.3

MANIFEST = ScrawManifest(
    name="safe_whxw_archive",
    label="SAFE News Archive (外汇局外汇新闻)",
    url=ARCHIVES[0][2],
    description=(
        "Catalog crawl of the State Administration of Foreign Exchange (国家外汇管理局) "
        "外汇新闻 archive at /safe/whxw/ — SAFE's rolling news stream. Each record is "
        "one listed document with section, title, date (from <dd> display date; URL "
        "path /YYYY/MMDD/ fallback), url, doc_type. Paginates index.html → index_N.html "
        "(page N = index_N.html, no offset, 404 on overflow). Default crawl = 50 pages; "
        "--all for full history."
    ),
    columns=[
        ScrawColumn(name="section", nullable=False,
                    description="archive section: 外汇新闻 (from seed config)",
                    source_field="meta:section", semantic_type="category"),
        ScrawColumn(name="title", nullable=False,
                    description='document title (from <a title="..."> attribute)',
                    source_field="a@title", semantic_type="title"),
        ScrawColumn(name="date", type="date", nullable=True,
                    description="publish date YYYY-MM-DD (from <dd> display date; URL path /YYYY/MMDD/ fallback)",
                    source_field="dd", semantic_type="date"),
        ScrawColumn(name="url", primary_key=True, nullable=False,
                    description="absolute document URL (.html)",
                    source_field="a@href", semantic_type="url"),
        ScrawColumn(name="doc_type", nullable=False,
                    description="document format derived from URL extension: html or pdf",
                    source_field="url:ext", semantic_type="category"),
    ],
    archives=[ScrawArchive(section=s, subsection=sub, url=u) for (s, sub, u) in ARCHIVES],
    crawl={
        "scope": "外汇新闻 feed at /safe/whxw/",
        "default_max_pages": 50,
        "all_flag": True,
        "per_page": PER_PAGE,
        "pagination": "index.html (page 1) → index_N.html (page N, no offset), 404 on overflow",
        "item_selector": "div.list_conr li (requires dt a + dd)",
        "fields": {"title": "dt a@title", "date": "dd", "url": "dt a@href"},
    },
)


def page_url(base_index: str, page_no: int) -> str:
    """page 1 = base/index.html; page N≥2 = base/index_N.html (no offset)."""
    if page_no <= 1:
        return base_index
    base = base_index.rsplit("index.html", 1)[0]
    return f"{base}index_{page_no}.html"


def doc_type(url: str) -> str:
    return "pdf" if urlparse(url).path.lower().endswith(".pdf") else "html"


def parse_archive(page, base_url, section):
    # ponytail: scope to the news container — bare `ul li` also matches nav menus.
    # Real news items live under div.list_conr and have both <dt><a> and <dd>.
    for li in page.css("div.list_conr li"):
        dts = li.css("dt a")
        dds = li.css("dd")
        if not dts or not dds:
            continue  # skip nav items (no dt link + dd date pair)
        a = dts[0]
        href = (a.attrib.get("href") or "").strip()
        if not href:
            continue
        url = urljoin(base_url, href)
        title = (a.attrib.get("title") or a.text or "").strip()
        date = (dds[0].text or "").strip()
        yield {"section": section, "title": title, "date": date, "url": url, "doc_type": doc_type(url)}


def crawl_archive(section, subsection, base_index, max_pages):
    records = []
    page_no = 1
    while True:
        url = page_url(base_index, page_no)
        resp = Fetcher().get(url)
        if resp.status == 404 or not resp.html_content:
            break
        items = list(parse_archive(resp, url, section))
        if not items:
            break
        records.extend(items)
        if max_pages and page_no >= max_pages:
            break
        page_no += 1
        time.sleep(SLEEP)
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    page_group = ap.add_mutually_exclusive_group()
    page_group.add_argument("--max-pages", type=int, default=50,
                            help="pages per archive (default 50; 0 = full crawl)")
    page_group.add_argument("--all", action="store_true",
                            help="full crawl, no page cap (alias for --max-pages 0)")
    args = ap.parse_args()
    max_pages = 0 if args.all else args.max_pages

    all_records = []
    for section, subsection, url in ARCHIVES:
        recs = crawl_archive(section, subsection, url, max_pages)
        print(f"# [{section}/{subsection}] {len(recs)} docs", file=sys.stderr)
        all_records.extend(recs)
        time.sleep(SLEEP)
    print(f"# TOTAL: {len(all_records)} docs across {len(ARCHIVES)} archives "
          f"(max_pages={'all' if not max_pages else max_pages})", file=sys.stderr)
    print(json.dumps(all_records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
