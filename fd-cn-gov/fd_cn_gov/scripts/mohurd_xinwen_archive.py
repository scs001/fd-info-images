"""Catalog crawl of MOHURD news archive /xinwen/.

The /xinwen/<section>/index.html landing renders only the first 20 docs server-side;
the list is then re-fetched by a layui-laypage paginator hitting the same backend
API for every page (including page 1). The API is:

    GET https://www.mohurd.gov.cn/api-gateway/jpaas-publish-server/front/page/build/unit
        ?webId=86ca573ec4df405db627fdc2493677f3
        &pageId=<per-section>
        &tplSetId=fc259c381af3496d85e61997ea7771cb
        &pageType=column&tagId=栏目-list&parseType=bulidstatic
        &paramJson={"pageNo":N,"pageSize":20}

Response is JSON {data:{html:"<ul>...</ul>"}} carrying 20 <li class="date"> rows.
Per-section pageId + total counts:
    部门动态 jsyw → f317736c953f43b893310d52b48aadaa  (1681 docs ~ 85 pages)
    工作动态 gzdt → 919e942639b5477d96e4c97471c61d9f  (6019 docs ~ 301 pages)
    地方信息 dfxx → 13f214f3a89147ea859e47aab5f60d72  (13392 docs ~ 670 pages)

Default crawl is 50 pages per section (~3000 most-recent docs); --all / --max-pages 0
walks until the API returns an empty page.

Run:  uv run --directory mcp/scrapling-uv-mcp python scripts/mohurd_xinwen_archive.py [--all|--max-pages N]
"""
import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin, urlparse, quote

from scrapling.fetchers import Fetcher

from fd_cn_gov.scraw_contract import ScrawArchive, ScrawColumn, ScrawManifest

BASE = "https://www.mohurd.gov.cn"
API = f"{BASE}/api-gateway/jpaas-publish-server/front/page/build/unit"
WEB_ID = "86ca573ec4df405db627fdc2493677f3"
TPL_SET_ID = "fc259c381af3496d85e61997ea7771cb"

ARCHIVES = [
    ("部门动态", "jsyw", "f317736c953f43b893310d52b48aadaa"),
    ("工作动态", "gzdt", "919e942639b5477d96e4c97471c61d9f"),
    ("地方信息", "dfxx", "13f214f3a89147ea859e47aab5f60d72"),
]

# Stable contract — `register.py` reads this to write the daas database.
# columns ARE the output record schema (parse_page's yield must match).
MANIFEST = ScrawManifest(
    name="mohurd_xinwen_archive",
    label="MOHURD Xinwen Archive (住建部新闻动态)",
    url="https://www.mohurd.gov.cn/xinwen/",
    description=(
        "Catalog crawl of the Ministry of Housing & Urban-Rural Development (住建部) "
        "/xinwen/ news archive across 3 sections (部门动态 jsyw, 工作动态 gzdt, 地方信息 "
        "dfxx). Unlike the static .htm archives, the list is rendered by a "
        "layui-laypage paginator hitting a JSON backend API "
        "(/api-gateway/.../front/page/build/unit) per page; the crawler calls that "
        "API directly with pageNo/pageSize. One record per listed document with "
        "section, title, date (from <span class='date-info'>), url, doc_type. Default "
        "crawl = 50 pages per section (~3000 most-recent docs); --all walks until the "
        "API returns an empty page."
    ),
    columns=[
        ScrawColumn(name="section", nullable=False,
                    description="news section: 部门动态/工作动态/地方信息 (from seed config)",
                    source_field="meta:section", semantic_type="category"),
        ScrawColumn(name="title", nullable=False,
                    description='document title (from <a title="..."> attribute)',
                    source_field="a@title", semantic_type="title"),
        ScrawColumn(name="date", type="date", nullable=False,
                    description='publish date YYYY-MM-DD (from <span class="date-info">)',
                    source_field="span.date-info", semantic_type="date"),
        ScrawColumn(name="url", primary_key=True, nullable=False,
                    description="absolute document URL (.html/.pdf)",
                    source_field="a@href", semantic_type="url"),
        ScrawColumn(name="doc_type", nullable=False,
                    description="document format derived from URL extension: html or pdf",
                    source_field="url:ext", semantic_type="category"),
    ],
    archives=[ScrawArchive(section=s, subsection=slug, url=f"{BASE}/xinwen/{slug}/") for (s, slug, _) in ARCHIVES],
    crawl={
        "scope": "3 sections under /xinwen/ (部门动态, 工作动态, 地方信息)",
        "default_max_pages": 50,
        "all_flag": True,
        "per_page": 20,
        "pagination": "JSON API: /api-gateway/.../front/page/build/unit?pageId=<id>&paramJson={pageNo:N,pageSize:20} — empty page terminates",
        "api_endpoint": API,
        "item_selector": 'li.date > a.fl[title][href] + span.date-info (inside API-returned html)',
        "fields": {"title": "a@title", "date": "span.date-info", "url": "a@href"},
    },
)

PER_PAGE = 20
SLEEP = 0.3  # ponytail: gentle pacing — rate-limit insurance on a .gov.cn host

# Capture <li class="date"><a class="fl" href="..." ... title="..."><...</a><span class="date-info">YYYY-MM-DD</span>
_LI_RE = re.compile(
    r'<li class="date">\s*'
    r'<a[^>]*class="fl"[^>]*href="(?P<href>[^"]+)"[^>]*title="(?P<title>[^"]*)"[^>]*>'
    r'.*?'
    r'<span class="date-info">(?P<date>\d{4}-\d{2}-\d{2})</span>',
    re.DOTALL,
)


def page_url(page_id: str, page_no: int) -> str:
    """API call for one page — paramJson is JSON-encoded then URL-encoded."""
    param = quote(json.dumps({"pageNo": page_no, "pageSize": PER_PAGE}, separators=(",", ":")))
    return (
        f"{API}?webId={WEB_ID}&pageId={page_id}&parseType=bulidstatic"
        f"&pageType=column&tagId={quote('栏目-list')}&tplSetId={TPL_SET_ID}"
        f"&paramJson={param}"
    )


def doc_type(url: str) -> str:
    path = urlparse(url).path
    return "pdf" if path.lower().endswith(".pdf") else "html"


def parse_page(html: str, section: str, section_slug: str):
    """Yield record dicts for one API-page's inner HTML."""
    section_base = f"{BASE}/xinwen/{section_slug}/"
    for m in _LI_RE.finditer(html):
        href = m.group("href").strip()
        if not href:
            continue
        url = urljoin(section_base, href)
        yield {
            "section": section,
            "title": m.group("title").strip(),
            "date": m.group("date"),
            "url": url,
            "doc_type": doc_type(url),
        }


def crawl_archive(section: str, section_slug: str, page_id: str, max_pages: int):
    records = []
    page_no = 1
    while True:
        url = page_url(page_id, page_no)
        resp = Fetcher().get(url)
        if resp.status != 200 or not resp.body:
            break
        try:
            payload = json.loads(resp.body)
        except Exception:
            break
        if not payload.get("success"):
            break
        inner_html = (payload.get("data") or {}).get("html") or ""
        items = list(parse_page(inner_html, section, section_slug))
        if not items:
            break
        records.extend(items)
        if max_pages and page_no >= max_pages:
            break
        if len(items) < PER_PAGE:
            break  # last page
        page_no += 1
        time.sleep(SLEEP)
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    page_group = ap.add_mutually_exclusive_group()
    page_group.add_argument("--max-pages", type=int, default=50,
                             help="pages per section (default 50; 0 = full crawl)")
    page_group.add_argument("--all", action="store_true",
                            help="full crawl, no page cap (alias for --max-pages 0)")
    args = ap.parse_args()
    max_pages = 0 if args.all else args.max_pages

    all_records = []
    for section, slug, page_id in ARCHIVES:
        recs = crawl_archive(section, slug, page_id, max_pages)
        print(f"# [{section}/{slug}] {len(recs)} docs", file=sys.stderr)
        all_records.extend(recs)
        time.sleep(SLEEP)
    print(f"# TOTAL: {len(all_records)} docs across {len(ARCHIVES)} sections "
          f"(max_pages={'all' if not max_pages else max_pages})", file=sys.stderr)
    print(json.dumps(all_records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
