"""Catalog crawl of the NDRC (国家发改委) 通知公告 archive.

The 通知公告 feed at /xwdt/tzgg/ is NDRC's rolling notice stream (政策印发
通知, 项复函, ~20 pages). Each item is a clean TRS list row:

    <ul class="u-list"><li><a href="./202606/t20260625_1406081.html"
        title="...">...</a><span>2026/06/25</span></li></ul>

Pagination is the TRS createPageHTML(20,0,"index","html") convention:
page 1 = index.html, page N≥2 = index_{N-1}.html (404 on overflow). The doc
URL token t<YYYYMMDD>_ carries the canonical publish date; the visible span
uses slashes (2026/06/25) as a fallback.

Default crawl is 50 pages; --all / --max-pages 0 walks until a 404 or empty page.

Run:  uv run --directory mcp/scrapling-uv-mcp python scripts/ndrc_tzgg_archive.py [--all|--max-pages N]
"""
import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import Fetcher

from fd_cn_gov.scraw_contract import ScrawArchive, ScrawColumn, ScrawManifest

BASE = "https://www.ndrc.gov.cn"
ARCHIVES = [
    ("通知公告", "通知公告", f"{BASE}/xwdt/tzgg/"),
]
PER_PAGE = 20
SLEEP = 0.3  # ponytail: gentle pacing on a .gov.cn host

MANIFEST = ScrawManifest(
    name="ndrc_tzgg_archive",
    label="NDRC Notice Archive (发改委通知公告)",
    url=ARCHIVES[0][2],
    description=(
        "Catalog crawl of the National Development & Reform Commission (国家发改委) "
        "通知公告 archive at /xwdt/tzgg/ — NDRC's rolling notice stream (政策印发"
        "通知, 项复函, ~20 pages). Each record is one listed document with section, "
        "title, date (URL t<YYYYMMDD>_ token; span fallback), url, doc_type. "
        "Paginates the TRS createPageHTML convention: index.html → index_{N-1}.html "
        "(404 on overflow). Default crawl = 50 pages; --all for full history."
    ),
    columns=[
        ScrawColumn(name="section", nullable=False,
                    description="archive section: 通知公告 (from seed config)",
                    source_field="meta:section", semantic_type="category"),
        ScrawColumn(name="title", nullable=False,
                    description='document title (from <a title="..."> attribute)',
                    source_field="a@title", semantic_type="title"),
        ScrawColumn(name="date", type="date", nullable=True,
                    description="publish date YYYY-MM-DD (URL t<YYYYMMDD>_ token; span fallback, slashes→dashes)",
                    source_field="url:re:t(\\d{8})_", semantic_type="date"),
        ScrawColumn(name="url", primary_key=True, nullable=False,
                    description="absolute document URL (.html)",
                    source_field="a@href", semantic_type="url"),
        ScrawColumn(name="doc_type", nullable=False,
                    description="document format derived from URL extension: html or pdf",
                    source_field="url:ext", semantic_type="category"),
    ],
    archives=[ScrawArchive(section=s, subsection=sub, url=u) for (s, sub, u) in ARCHIVES],
    crawl={
        "scope": "通知公告 feed at /xwdt/tzgg/",
        "default_max_pages": 50,
        "all_flag": True,
        "per_page": PER_PAGE,
        "pagination": 'TRS createPageHTML: index.html (page 1) → index_{N-1}.html (page N), 404 on overflow',
        "item_selector": "ul.u-list > li",
        "fields": {"title": "a@title", "date": "url:re:t(\\d{8})_ + span fallback", "url": "a@href"},
    },
)

_DATE_TOKEN = re.compile(r"t(\d{4})(\d{2})(\d{2})_")


def page_url(base_index: str, page_no: int) -> str:
    """page 1 = base/ ; page N≥2 = base/index_{N-1}.html."""
    if page_no <= 1:
        return base_index
    base = base_index.rstrip("/") + "/"
    return f"{base}index_{page_no - 1}.html"


def doc_type(url: str) -> str:
    return "pdf" if urlparse(url).path.lower().endswith(".pdf") else "html"


def url_date(url: str) -> str:
    m = _DATE_TOKEN.search(url)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def parse_archive(page, base_url, section):
    for li in page.css("ul.u-list > li"):
        anchors = li.css("a")
        if not anchors:
            continue
        a = anchors[0]
        href = (a.attrib.get("href") or "").strip()
        if not href:
            continue
        url = urljoin(base_url, href)
        title = (a.attrib.get("title") or a.text or "").strip()
        date = url_date(url)
        if not date:
            spans = li.css("span")
            if spans:
                date = (spans[0].text or "").strip().replace("/", "-")
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
