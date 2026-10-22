"""Catalog crawl of MNR (自然资源部) 通知公告 archive.

The 通知公告 feed at /gk/tzgg/ lists MNR's notices. Items are TRS rows but
with the date span BEFORE the link:

    <ul class="ky_open_list"><li><span>2026-06-30</span>
        <a href="http://gi.mnr.gov.cn/202606/t20260630_2933017.html">...</a></li>

Doc URLs live on the gi.mnr.gov.cn (公开目录) subdomain and carry the TRS
t<YYYYMMDD>_ token. Pagination is createPageHTML(40): index.html → index_1.html
→ index_2.html … (404 on overflow), 25 docs/page.

Default crawl is 50 pages; --all / --max-pages 0 walks until a 404 or empty page.

Run:  uv run --directory mcp/scrapling-uv-mcp python scripts/mnr_tzgg_archive.py [--all|--max-pages N]
"""
import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import Fetcher

from fd_cn_gov.scraw_contract import ScrawArchive, ScrawColumn, ScrawManifest

BASE = "https://www.mnr.gov.cn"
ARCHIVES = [
    ("通知公告", "通知公告", f"{BASE}/gk/tzgg/"),
]
PER_PAGE = 25
SLEEP = 0.3

MANIFEST = ScrawManifest(
    name="mnr_tzgg_archive",
    label="MNR Notice Archive (自然资源部通知公告)",
    url=ARCHIVES[0][2],
    description=(
        "Catalog crawl of the Ministry of Natural Resources (自然资源部) 通知公告 "
        "archive at /gk/tzgg/. Each record is one listed document (doc URLs on the "
        "gi.mnr.gov.cn 公开目录 subdomain) with section, title (link text), date "
        "(span date; URL t<YYYYMMDD>_ token fallback), url, doc_type. Paginates the "
        "TRS createPageHTML(40) convention: index.html → index_{N-1}.html (404 on "
        "overflow), 25 docs/page. Default crawl = 50 pages; --all for full history."
    ),
    columns=[
        ScrawColumn(name="section", nullable=False,
                    description="archive section: 通知公告 (from seed config)",
                    source_field="meta:section", semantic_type="category"),
        ScrawColumn(name="title", nullable=False,
                    description="document title (from <a> link text — MNR items carry no title attr)",
                    source_field="a:text", semantic_type="title"),
        ScrawColumn(name="date", type="date", nullable=True,
                    description="publish date YYYY-MM-DD (span date before link; URL t<YYYYMMDD>_ token fallback)",
                    source_field="span", semantic_type="date"),
        ScrawColumn(name="url", primary_key=True, nullable=False,
                    description="absolute document URL on gi.mnr.gov.cn (.html)",
                    source_field="a@href", semantic_type="url"),
        ScrawColumn(name="doc_type", nullable=False,
                    description="document format derived from URL extension: html or pdf",
                    source_field="url:ext", semantic_type="category"),
    ],
    archives=[ScrawArchive(section=s, subsection=sub, url=u) for (s, sub, u) in ARCHIVES],
    crawl={
        "scope": "通知公告 feed at /gk/tzgg/ (docs on gi.mnr.gov.cn)",
        "default_max_pages": 50,
        "all_flag": True,
        "per_page": PER_PAGE,
        "pagination": 'TRS createPageHTML(40): index.html (page 1) → index_{N-1}.html (page N), 404 on overflow',
        "item_selector": "ul.ky_open_list > li",
        "fields": {"title": "a:text", "date": "span (before link)", "url": "a@href"},
    },
)

_DATE_TOKEN = re.compile(r"t(\d{4})(\d{2})(\d{2})_")


def page_url(base_index: str, page_no: int) -> str:
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
    for li in page.css("ul.ky_open_list > li"):
        anchors = li.css("a")
        if not anchors:
            continue
        a = anchors[0]
        href = (a.attrib.get("href") or "").strip()
        if not href:
            continue
        url = urljoin(base_url, href)
        title = (a.text or "").strip()
        date = ""
        spans = li.css("span")
        if spans:
            date = (spans[0].text or "").strip()
        if not date:
            date = url_date(url)
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
