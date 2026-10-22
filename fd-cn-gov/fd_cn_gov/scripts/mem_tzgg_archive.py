"""Catalog crawl of MEM (应急管理部) 通知公告 archive.

The 通知公告 feed at /gk/tzgg/ lists MEM's notices. Items put the date span
INSIDE the link:

    <ul><li><a href="../zfxxgkpt/fdzdgknr/202606/t20260622_608438.shtml">title
        <span>2026-06-22</span></a></li>

Doc URLs are .shtml with the TRS t<YYYYMMDD>_ token. Pagination is offset-by-1
.shtml: page 1 = /gk/tzgg/ (dir, serves index.shtml), page N≥2 =
index_{N-1}.shtml (404 on overflow). ~47 docs/page, 25 pages, ~1183 docs total.

Default crawl is 50 pages; --all / --max-pages 0 walks until a 404 or empty page.

Run:  uv run --directory mcp/scrapling-uv-mcp python scripts/mem_tzgg_archive.py [--all|--max-pages N]
"""
import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import Fetcher

from fd_cn_gov.scraw_contract import ScrawArchive, ScrawColumn, ScrawManifest

BASE = "https://www.mem.gov.cn"
ARCHIVE_INDEX = f"{BASE}/gk/tzgg/"
PER_PAGE = 47
SLEEP = 0.3

MANIFEST = ScrawManifest(
    name="mem_tzgg_archive",
    label="MEM Notice Archive (应急管理部通知公告)",
    url=ARCHIVE_INDEX,
    description=(
        "Catalog crawl of the Ministry of Emergency Management (应急管理部) 通知公告 "
        "archive at /gk/tzgg/ (~1183 docs / 25 pages). Each record is one listed "
        "document (.shtml) with section, title (link text before the inner span), "
        "date (inner span; URL t<YYYYMMDD>_ token fallback), url, doc_type. Paginates "
        "offset-by-1 .shtml: /gk/tzgg/ (page 1) → index_{N-1}.shtml (404 on overflow). "
        "Default crawl = 50 pages; --all for full history."
    ),
    columns=[
        ScrawColumn(name="section", nullable=False,
                    description="archive section: 通知公告 (from seed config)",
                    source_field="meta:section", semantic_type="category"),
        ScrawColumn(name="title", nullable=False,
                    description="document title (link text before the inner <span> date)",
                    source_field="a:text-before-span", semantic_type="title"),
        ScrawColumn(name="date", type="date", nullable=True,
                    description="publish date YYYY-MM-DD (inner span; URL t<YYYYMMDD>_ token fallback)",
                    source_field="a span", semantic_type="date"),
        ScrawColumn(name="url", primary_key=True, nullable=False,
                    description="absolute document URL (.shtml)",
                    source_field="a@href", semantic_type="url"),
        ScrawColumn(name="doc_type", nullable=False,
                    description="document format derived from URL extension: html or pdf",
                    source_field="url:ext", semantic_type="category"),
    ],
    archives=[ScrawArchive(section="通知公告", subsection="通知公告", url=ARCHIVE_INDEX)],
    crawl={
        "scope": "通知公告 feed at /gk/tzgg/",
        "default_max_pages": 50,
        "all_flag": True,
        "per_page": PER_PAGE,
        "pagination": "offset-by-1 .shtml: /gk/tzgg/ (page 1) → index_{N-1}.shtml (page N), 404 on overflow",
        "item_selector": "ul li > a (href + text + inner span date)",
        "fields": {"title": "a:text-before-span", "date": "a span", "url": "a@href"},
    },
)

# <a href="../zfxxgkpt/.../t20260622_608438.shtml">title text<span>2026-06-22</span></a>
_ITEM_RE = re.compile(
    r'<a\s+href="(?P<href>[^"]*t\d{8}_\d+\.s?html?)"[^>]*>'
    r'(?P<title>.*?)<span>(?P<date>\d{4}-\d{2}-\d{2})</span>',
    re.DOTALL,
)
_DATE_TOKEN = re.compile(r"t(\d{4})(\d{2})(\d{2})_")


def page_url(page_no: int) -> str:
    if page_no <= 1:
        return ARCHIVE_INDEX
    return f"{ARCHIVE_INDEX}index_{page_no - 1}.shtml"


def doc_type(url: str) -> str:
    return "pdf" if urlparse(url).path.lower().endswith(".pdf") else "html"


def url_date(url: str) -> str:
    m = _DATE_TOKEN.search(url)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def parse_page(html: str, base_url: str):
    for m in _ITEM_RE.finditer(html):
        href = m.group("href").strip()
        url = urljoin(base_url, href)
        title = re.sub(r"<[^>]+>", "", m.group("title")).strip()
        date = m.group("date").strip() or url_date(url)
        yield {"section": "通知公告", "title": title, "date": date, "url": url, "doc_type": doc_type(url)}


def crawl(max_pages: int):
    records = []
    page_no = 1
    while True:
        url = page_url(page_no)
        resp = Fetcher().get(url)
        if resp.status == 404 or not resp.html_content:
            break
        items = list(parse_page(resp.html_content, url))
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
                            help="pages to crawl (default 50; 0 = full crawl)")
    page_group.add_argument("--all", action="store_true",
                            help="full crawl, no page cap (alias for --max-pages 0)")
    args = ap.parse_args()
    max_pages = 0 if args.all else args.max_pages

    recs = crawl(max_pages)
    print(f"# [通知公告] {len(recs)} docs (max_pages={'all' if not max_pages else max_pages})", file=sys.stderr)
    print(json.dumps(recs, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
