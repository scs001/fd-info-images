"""Full-archive crawl of the Ministry of Agriculture (农业农村部) open-info hub.

Crawls the 机构分类 (institution-classified) archive at /govpublic/, which the
landing page (/gk/) only previews. Each of 24 department categories paginates
via /govpublic/1/2/<id>/index.htm → index_2.htm → ... (16 docs/page, 404 on
overflow). Emits {department, title, date, url} as JSON to stdout.

Run:  uv run --directory mcp/scrapling-uv-mcp python scripts/moa_govpublic_archive.py
"""
import json
import re
import sys
import time
from urllib.parse import urljoin

from scrapling.fetchers import Fetcher

from fd_cn_gov.scraw_contract import ScrawColumn, ScrawManifest

HUB = "https://www.moa.gov.cn/govpublic/"
ROOT = "https://www.moa.gov.cn/govpublic/"
PER_PAGE = 16
SLEEP = 0.3  # ponytail: gentle pacing, single-threaded — rate-limit insurance

# Stable contract — `register.py` reads this to write the daas database.
# Archives are discovered at runtime (list_categories), so MANIFEST.archives
# stays empty; the crawl recipe documents how they're found instead.
MANIFEST = ScrawManifest(
    name="moa_govpublic_archive",
    label="MOA GovPublic Archive (农业农村部 机构分类)",
    url=HUB,
    description=(
        "Full crawl of the Ministry of Agriculture (农业农村部) 机构分类 archive "
        "at /govpublic/, which the landing page (/gk/) only previews. 24 "
        "department categories each paginate /govpublic/1/2/<id>/index.htm → "
        "index_2.htm → … (16 docs/page, 404 on overflow). Emits one record per "
        "listed document with department, title, date (from URL t<date> token), "
        "and URL."
    ),
    columns=[
        ScrawColumn(name="department", nullable=False,
                    description="department / category name (e.g. 办公厅, 法规司)",
                    source_field="hub:CAT_RE", semantic_type="category"),
        ScrawColumn(name="department_id", nullable=False,
                    description="numeric id used in the category path /1/2/<id>/",
                    source_field="hub:CAT_RE", semantic_type="identifier"),
        ScrawColumn(name="title", nullable=False,
                    description="document title (from <a title=...>)",
                    source_field="a@title", semantic_type="title"),
        ScrawColumn(name="date", type="date", nullable=True,
                    description="publish date YYYY-MM-DD (URL t<date> token, span fallback)",
                    source_field="url:re:t(\\d{8})_", semantic_type="date"),
        ScrawColumn(name="url", primary_key=True, nullable=False,
                    description="absolute document URL (.htm)",
                    source_field="a@href", semantic_type="url"),
    ],
    archives=[],  # discovered at runtime via CAT_RE on the hub page
    crawl={
        "scope": "all 24 department categories under /govpublic/, full pagination",
        "default_max_pages": 0,           # full crawl is the default for this scraper
        "per_page": PER_PAGE,
        "pagination": "/govpublic/1/2/<id>/index.htm → index_{N}.htm (404 on overflow)",
        "category_selector": 'href="./1/2/<id>/index.htm?id=<id>"',
        "item_selector": '<a title="..." href="*.htm">',
        "date_source": "URL token t<YYYYMMDD>_ (authoritative), span fallback",
    },
)


# title="..." href="....htm" OR href="....htm" title="..." — both orderings seen
ITEM_RE = re.compile(
    r'<a\s+(?:title="([^"]*)"\s+href="([^"]+\.htm)"|href="([^"]+\.htm)"\s+title="([^"]*)")'
)
CAT_RE = re.compile(r'href="\./1/2/(\d+)/index\.htm\?id=\d+"[^>]*>(.*?)</a>', re.DOTALL)
# date span right after the link
DATE_RE = re.compile(r'</a>\s*<span[^>]*>(\d{4}-\d{2}-\d{2})</span>')


def fetch(url):
    page = Fetcher().get(url)
    return page.status, page.html_content


def list_categories():
    _, html = fetch(HUB)
    cats = []
    for m in CAT_RE.finditer(html):
        nid, inner = m.group(1), m.group(2)
        label = re.sub(r"<[^>]+>", "", inner).strip()
        if label:
            cats.append((nid, label))
    return cats


URL_DATE_RE = re.compile(r"t(\d{4})(\d{2})(\d{2})_")


def date_from_url(url):
    """Authoritative: derive YYYY-MM-DD from the URL's t<date> token."""
    m = URL_DATE_RE.search(url)
    if not m:
        return ""
    y, mo, day = m.groups()
    if 1 <= int(mo) <= 12 and 1 <= int(day) <= 31:
        return f"{y}-{mo}-{day}"
    return ""


def parse_page(html, base_url):
    """Return list of (title, url, date) for one archive page."""
    items = []
    for m in ITEM_RE.finditer(html):
        title = (m.group(1) or m.group(4) or "").strip()
        href = m.group(2) or m.group(3)
        if not href:
            continue
        url = urljoin(base_url, href)
        # date from URL token (authoritative); span is only a fallback
        date = date_from_url(url)
        if not date:
            tail = html[m.end():m.end() + 80]
            d = DATE_RE.search(tail)
            date = d.group(1) if d else ""
        items.append((title, url, date))
    return items


def crawl_category(nid, label):
    base = f"{ROOT}1/2/{nid}/"
    records = []
    page_no = 1
    while True:
        path = "index.htm" if page_no == 1 else f"index_{page_no}.htm"
        url = base + path
        status, html = fetch(url)
        if status == 404 or not html:
            break
        items = parse_page(html, base)
        if not items:
            break
        for title, url, date in items:
            records.append({
                "department": label,
                "department_id": nid,
                "title": title,
                "date": date,
                "url": url,
            })
        if len(items) < PER_PAGE:
            break  # last page
        page_no += 1
        time.sleep(SLEEP)
    return records


def main():
    cats = list_categories()
    print(f"# {len(cats)} departments", file=sys.stderr)
    all_records = []
    for nid, label in cats:
        recs = crawl_category(nid, label)
        print(f"# {label} (id={nid}): {len(recs)} docs", file=sys.stderr)
        all_records.extend(recs)
        time.sleep(SLEEP)
    print(f"# TOTAL: {len(all_records)} docs", file=sys.stderr)
    print(json.dumps(all_records, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
