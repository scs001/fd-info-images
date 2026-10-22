"""Catalog crawl of MEE (生态环境部) 公示公告 archive.

The 公示公告 feed at /ywdt/gsgg/ lists MEE's public notices. Items:

    <ul><li><a class="ll_xxgk_gsq_list_a" href="./gongshi/gsq/202606/t20260624_1160129.shtml">title</a>
        <span>2026-06-24</span></li>

Doc URLs are .shtml with the TRS t<YYYYMMDD>_ token. Pagination is the TRS
createPageHTML convention: page 1 = /ywdt/gsgg/ (dir), page N≥2 = index_{N-1}.html
(404 on overflow).

Default crawl is 50 pages; --all / --max-pages 0 walks until a 404 or empty page.

Run:  uv run --directory mcp/scrapling-uv-mcp python scripts/mee_gsgg_archive.py [--all|--max-pages N]
"""
import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import Fetcher

from fd_cn_gov.scraw_contract import ScrawArchive, ScrawColumn, ScrawManifest

BASE = "https://www.mee.gov.cn"
ARCHIVES = [
    ("公示公告", "公示公告", f"{BASE}/ywdt/gsgg/"),
]
PER_PAGE = 25
SLEEP = 0.3

MANIFEST = ScrawManifest(
    name="mee_gsgg_archive",
    label="MEE Notice Archive (生态环境部公示公告)",
    url=ARCHIVES[0][2],
    description=(
        "Catalog crawl of the Ministry of Ecology & Environment (生态环境部) 公示公告 "
        "archive at /ywdt/gsgg/. Each record is one listed document (.shtml) with "
        "section, title (link text — MEE items carry no title attr), date (span date; "
        "URL t<YYYYMMDD>_ token fallback), url, doc_type. Paginates the TRS "
        "createPageHTML convention: /ywdt/gsgg/ (page 1) → index_{N-1}.html (404 on "
        "overflow). Default crawl = 50 pages; --all for full history."
    ),
    columns=[
        ScrawColumn(name="section", nullable=False,
                    description="archive section: 公示公告 (from seed config)",
                    source_field="meta:section", semantic_type="category"),
        ScrawColumn(name="title", nullable=False,
                    description="document title (from <a> link text — MEE items carry no title attr)",
                    source_field="a:text", semantic_type="title"),
        ScrawColumn(name="date", type="date", nullable=True,
                    description="publish date YYYY-MM-DD (span date after link; URL t<YYYYMMDD>_ token fallback)",
                    source_field="span", semantic_type="date"),
        ScrawColumn(name="url", primary_key=True, nullable=False,
                    description="absolute document URL (.shtml)",
                    source_field="a@href", semantic_type="url"),
        ScrawColumn(name="doc_type", nullable=False,
                    description="document format derived from URL extension: html or pdf",
                    source_field="url:ext", semantic_type="category"),
    ],
    archives=[ScrawArchive(section=s, subsection=sub, url=u) for (s, sub, u) in ARCHIVES],
    crawl={
        "scope": "公示公告 feed at /ywdt/gsgg/",
        "default_max_pages": 50,
        "all_flag": True,
        "per_page": PER_PAGE,
        "pagination": "TRS createPageHTML: /ywdt/gsgg/ (page 1) → index_{N-1}.html (page N), 404 on overflow",
        "item_selector": "ul li (a.ll_xxgk_gsq_list_a + span)",
        "fields": {"title": "a:text", "date": "span + url:re:t(\\d{8})_", "url": "a@href"},
    },
)

_DATE_TOKEN = re.compile(r"t(\d{4})(\d{2})(\d{2})_")


def page_url(base_index: str, page_no: int) -> str:
    if page_no <= 1:
        return base_index
    base = base_index.rstrip("/") + "/"
    return f"{base}index_{page_no - 1}.html"


def doc_type(url: str) -> str:
    p = urlparse(url).path.lower()
    if p.endswith(".pdf"):
        return "pdf"
    return "html"  # .shtml and .html both reported as html


def url_date(url: str) -> str:
    m = _DATE_TOKEN.search(url)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def parse_archive(page, base_url, section):
    for li in page.css("ul li"):
        links = li.css("a.ll_xxgk_gsq_list_a")
        if not links:
            continue
        a = links[0]
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
