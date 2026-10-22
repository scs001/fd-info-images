"""Catalog crawl of the Ministry of Finance (财政部) /gkml/ 信息公开 archive.

The /gkml/ landing renders ~5 docs per section; the real archives live behind
4 sections, 6 sub-archives total:

  通知公告  → 3 sub-archives (通知通告 / 财政部令 / 财政部公告)
  财政数据  → 1 archive
  财政文告  → 1 archive (PDF-only, year folders, no pagination on landing)
  财经论坛  → 1 archive (调查研究/监管局动态)

Each archive renders docs as either
  <ul class="xwbd_lianbolistfrcon"><li><a title="..." href="...">...</a><span>YYYY-MM-DD</span></li>
or
  <ul class="xwfb_listbox"><li><a title="..." href="...">...</a><span>YYYY-MM-DD</span></li>
and paginates index.htm/index.html (page 1) → index_1.htm (page 2) → index_2.htm
… 404/empty on overflow. The detail URL almost always carries a `t<YYYYMMDD>_<id>.htm`
token — that is the canonical publish date; the visible <span> is a 1–4-day
release-lag fallback.

Default crawl is 50 pages per archive; --all / --max-pages 0 = full crawl.

Run:  uv run --directory mcp/scrapling-uv-mcp python scripts/mof_gkml_archive.py [--all|--max-pages N]
"""
import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import Fetcher

from fd_cn_gov.scraw_contract import ScrawArchive, ScrawColumn, ScrawManifest

ARCHIVES = [
    ("通知公告", "通知通告", "https://www.mof.gov.cn/gkml/bulinggonggao/tongzhitonggao/"),
    ("通知公告", "财政部令", "https://www.mof.gov.cn/gkml/bulinggonggao/czbl/"),
    ("通知公告", "财政部公告", "https://www.mof.gov.cn/gkml/bulinggonggao/czbgg/"),
    ("财政数据", "财政数据", "https://www.mof.gov.cn/gkml/caizhengshuju/"),
    ("财政文告", "财政文告", "https://www.mof.gov.cn/gkml/caizhengwengao/"),
    ("财经论坛", "财经论坛", "https://www.mof.gov.cn/gkml/diaochayanjiu/"),
]

# Stable contract — `register.py` reads this to write the daas database.
# columns ARE the output record schema (parse_archive's yield must match).
MANIFEST = ScrawManifest(
    name="mof_gkml_archive",
    label="MOF gkml Archive (财政部信息公开)",
    url="https://www.mof.gov.cn/gkml/",
    description=(
        "Catalog crawl of the Ministry of Finance (财政部) /gkml/ 信息公开 archive "
        "across 4 sections / 6 sub-archives (通知公告×3, 财政数据, 财政文告, 财经论坛). "
        "Each archive paginates index.htm → index_1.htm → index_2.htm … (404 on "
        "overflow). One record per listed document with section, subsection, title, "
        "date (URL t<YYYYMMDD>_ token, span fallback), url, and doc_type. Default "
        "crawl = 50 pages per archive; --all for full history."
    ),
    columns=[
        ScrawColumn(name="section", nullable=False,
                    description="top-level gkml section: 通知公告/财政数据/财政文告/财经论坛 (from seed config)",
                    source_field="meta:section", semantic_type="category"),
        ScrawColumn(name="subsection", nullable=False,
                    description="sub-archive name, e.g. 通知通告, 财政部令, 财政部公告, 财政数据 (from seed config)",
                    source_field="meta:subsection", semantic_type="category"),
        ScrawColumn(name="title", nullable=False,
                    description='document title (from <a title="..."> attribute, visible text fallback)',
                    source_field="a@title", semantic_type="title"),
        ScrawColumn(name="date", type="date", nullable=True,
                    description="publish date YYYY-MM-DD (URL t<YYYYMMDD>_ token; sibling <span> fallback)",
                    source_field="url:re:t(\\d{8})_", semantic_type="date"),
        ScrawColumn(name="url", primary_key=True, nullable=False,
                    description="absolute document URL (.htm/.html/.pdf)",
                    source_field="a@href", semantic_type="url"),
        ScrawColumn(name="doc_type", nullable=False,
                    description="document format derived from URL extension: html or pdf",
                    source_field="url:ext", semantic_type="category"),
    ],
    archives=[ScrawArchive(section=s, subsection=sub, url=u) for (s, sub, u) in ARCHIVES],
    crawl={
        "scope": "4 sections / 6 sub-archives under /gkml/",
        "default_max_pages": 50,
        "all_flag": True,
        "pagination": "index.htm (page 1) → index_{N-1}.htm (page N), 404 on overflow",
        "item_selector": "ul.xwbd_lianbolistfrcon > li, ul.xwfb_listbox > li",
        "fields": {"title": "a@title", "date": "url:re:t(\\d{8})_ + span fallback", "url": "a@href"},
    },
)

SLEEP = 0.3  # ponytail: gentle pacing on a .gov.cn host
_DATE_TOKEN = re.compile(r"t(\d{4})(\d{2})(\d{2})_")


def page_url(base_index: str, page_no: int) -> str:
    """page 1 = base/ ; page N≥2 = base/index_{N-1}.htm."""
    if page_no <= 1:
        return base_index
    base = base_index.rstrip("/") + "/"
    return f"{base}index_{page_no - 1}.htm"


def doc_type(url: str) -> str:
    path = urlparse(url).path
    return "pdf" if path.lower().endswith(".pdf") else "html"


def url_date(url: str) -> str:
    """Pull YYYY-MM-DD from the t<YYYYMMDD>_ token in the URL, '' if absent."""
    m = _DATE_TOKEN.search(url)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def parse_archive(page, base_url, section, subsection):
    """Yield record dicts for one archive page.

    MOF uses two list shapes that are otherwise identical:
      ul.xwbd_lianbolistfrcon > li > a[title][href] + span (date text)
      ul.xwfb_listbox        > li > a[title][href] + span (date text)
    """
    items = page.css("ul.xwbd_lianbolistfrcon > li, ul.xwfb_listbox > li")
    for li in items:
        anchors = li.css("a")
        if not anchors:
            continue
        a = anchors[0]
        href = (a.attrib.get("href") or "").strip()
        if not href:
            continue
        url = urljoin(base_url, href)
        # title from <a title=...>; fall back to visible text
        title = (a.attrib.get("title") or a.text or "").strip()
        # date: URL token first (canonical), then sibling <span>
        date = url_date(url)
        if not date:
            spans = li.css("span")
            if spans:
                date = (spans[0].text or "").strip()
        yield {
            "section": section,
            "subsection": subsection,
            "title": title,
            "date": date,
            "url": url,
            "doc_type": doc_type(url),
        }


def crawl_archive(section, subsection, base_index, max_pages):
    records = []
    page_no = 1
    while True:
        url = page_url(base_index, page_no)
        resp = Fetcher().get(url)
        if resp.status == 404 or not resp.html_content:
            break
        items = list(parse_archive(resp, url, section, subsection))
        if not items:
            break
        records.extend(items)
        if max_pages and page_no >= max_pages:
            break
        # MOF archives don't have a fixed per-page count (varies by section), so
        # we don't break on partial pages — rely on the next iteration returning
        # an empty list or 404 to terminate.
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
