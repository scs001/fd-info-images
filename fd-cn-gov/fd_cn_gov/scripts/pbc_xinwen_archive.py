"""Catalog crawl of the People's Bank of China (人民银行) 新闻发布 archive.

The 新闻发布 feed at /goutongjiaoliu/113456/113469/ is PBC's rolling
announcement stream (行长活动, 公告〔YYYY〕第N号, 央行新闻). ~8100 docs across
406 pages. Each item is a nested-table row:

    <font class="newslist_style"><a href=".../2026063018452585084/index.html"
        title="..." istitle="true">...</a></font><span class="hui12">2026-06-30</span>

Pagination is TRS WCM module-id style: page 1 = index.html, page N≥2 =
<moduleId>-<N>.html (module 11040 for this section). The doc URL token
`<YYYYMMDD><HHMMSS><seq>` carries the canonical publish date.

Default crawl is 50 pages (~1000 most-recent docs); --all / --max-pages 0
walks until a 404 or empty page.

Run:  uv run --directory mcp/scrapling-uv-mcp python scripts/pbc_xinwen_archive.py [--all|--max-pages N]
"""
import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import Fetcher

from fd_cn_gov.scraw_contract import ScrawArchive, ScrawColumn, ScrawManifest

BASE = "https://www.pbc.gov.cn"
SECTION = "新闻发布"
ARCHIVE_INDEX = f"{BASE}/goutongjiaoliu/113456/113469/index.html"
MODULE_ID = "11040"  # TRS module id for this section's paginator
PER_PAGE = 20
SLEEP = 0.3  # ponytail: gentle pacing on a .gov.cn host

# Stable contract — `register.py` reads this to write the daas database.
MANIFEST = ScrawManifest(
    name="pbc_xinwen_archive",
    label="PBC News Archive (人民银行新闻发布)",
    url=ARCHIVE_INDEX,
    description=(
        "Catalog crawl of the People's Bank of China (人民银行) 新闻发布 archive "
        "at /goutongjiaoliu/113456/113469/ — PBC's rolling announcement stream "
        "(行长活动, 公告〔YYYY〕第N号, 央行新闻, ~8100 docs / 406 pages). Each "
        "record is one listed document with section, title, date (URL "
        "<YYYYMMDD> token; span.hui12 fallback), url, doc_type. Paginates the "
        "TRS WCM module-id pattern: index.html (page 1) → 11040-<N>.html. "
        "Default crawl = 50 pages; --all for full history."
    ),
    columns=[
        ScrawColumn(name="section", nullable=False,
                    description="archive section: 新闻发布 (from seed config)",
                    source_field="meta:section", semantic_type="category"),
        ScrawColumn(name="title", nullable=False,
                    description='document title (from <a title="..."> attribute)',
                    source_field="a@title", semantic_type="title"),
        ScrawColumn(name="date", type="date", nullable=True,
                    description="publish date YYYY-MM-DD (URL <YYYYMMDD> token; span.hui12 fallback)",
                    source_field="url:re:(\\d{8})\\d{6,}", semantic_type="date"),
        ScrawColumn(name="url", primary_key=True, nullable=False,
                    description="absolute document URL (.html)",
                    source_field="a@href", semantic_type="url"),
        ScrawColumn(name="doc_type", nullable=False,
                    description="document format derived from URL extension: html or pdf",
                    source_field="url:ext", semantic_type="category"),
    ],
    archives=[ScrawArchive(section=SECTION, subsection="新闻发布", url=ARCHIVE_INDEX)],
    crawl={
        "scope": "新闻发布 feed at /goutongjiaoliu/113456/113469/",
        "default_max_pages": 50,
        "all_flag": True,
        "per_page": PER_PAGE,
        "pagination": "TRS module-id: index.html (page 1) → <moduleId>-<N>.html (page N), 404 on overflow",
        "module_id": MODULE_ID,
        "item_selector": 'font.newslist_style > a[title][istitle] + span.hui12',
        "fields": {"title": "a@title", "date": "url:re:(\\d{8})\\d{6,} + span.hui12 fallback", "url": "a@href"},
    },
)

# <a href="/goutongjiaoliu/113456/113469/2026063018452585084/index.html" ... title="..." istitle="true">...</a></font><span class="hui12">2026-06-30</span>
_ITEM_RE = re.compile(
    r'<a\s+href="(?P<href>/goutongjiaoliu/113456/113469/\d+/index\.html)"[^>]*?'
    r'title="(?P<title>[^"]*)"[^>]*?istitle="true"[^>]*>.*?</a>.*?'
    r'<span class="hui12">(?P<date>\d{4}-\d{2}-\d{2})</span>',
    re.DOTALL,
)
_URL_DATE_RE = re.compile(r"/(\d{4})(\d{2})(\d{2})\d{6,}/index\.html")


def page_url(page_no: int) -> str:
    """page 1 = index.html; page N≥2 = <moduleId>-<N>.html (TRS module-id pagination)."""
    if page_no <= 1:
        return ARCHIVE_INDEX
    base = ARCHIVE_INDEX.rsplit("index.html", 1)[0]
    return f"{base}{MODULE_ID}-{page_no}.html"


def doc_type(url: str) -> str:
    path = urlparse(url).path
    return "pdf" if path.lower().endswith(".pdf") else "html"


def url_date(url: str) -> str:
    """Pull YYYY-MM-DD from the URL's <YYYYMMDD><HHMMSS><seq> doc-id token, '' if absent."""
    m = _URL_DATE_RE.search(url)
    if not m:
        return ""
    y, mo, day = m.groups()
    if 1 <= int(mo) <= 12 and 1 <= int(day) <= 31:
        return f"{y}-{mo}-{day}"
    return ""


def parse_page(html: str, base_url: str):
    for m in _ITEM_RE.finditer(html):
        href = m.group("href").strip()
        if not href:
            continue
        url = urljoin(base_url, href)
        title = m.group("title").strip()
        date = url_date(url) or m.group("date").strip()
        yield {
            "section": SECTION,
            "title": title,
            "date": date,
            "url": url,
            "doc_type": doc_type(url),
        }


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
        # ponytail: PBC per-page count varies (~15); rely on 404/empty to terminate,
        # not a partial-page break (avoids stopping early on a short page).
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
    print(f"# [新闻发布] {len(recs)} docs (max_pages={'all' if not max_pages else max_pages})", file=sys.stderr)
    print(json.dumps(recs, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
