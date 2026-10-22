"""Catalog crawl of MOFCOM (商务部) 新闻发布 landing preview.

The /xwfb/index.html landing statically renders 7 news sections (领导人活动,
部领导活动, 日常新闻发布, 新闻发言人谈话, 司局负责人发布, 例行新闻发布会,
专题发布), each previewing ~6 most-recent items:

    <div ergodic="view"><h4 class="sTitle_02"><a>section name</a></h4>
    <ul><li><a href="..." title="...">title</a><span>[2026-06-30]</span></li>...

SCOPE NOTE: this is a landing PREVIEW (~42 latest docs), NOT a full paginated
archive. MOFCOM's deep per-section archives are JS-rendered (no static
pagination path), so this scraper covers only what the landing exposes. The
section name + title + date + url are all static and reliable.

Run:  uv run --directory mcp/scrapling-uv-mcp python scripts/mofcom_xwfb_archive.py
"""
import json
import re
import sys
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import Fetcher

from fd_cn_gov.scraw_contract import ScrawArchive, ScrawColumn, ScrawManifest

BASE = "https://www.mofcom.gov.cn"
LANDING = f"{BASE}/xwfb/index.html"

MANIFEST = ScrawManifest(
    name="mofcom_xwfb_archive",
    label="MOFCOM News Preview (商务部新闻发布)",
    url=LANDING,
    description=(
        "Catalog crawl of the Ministry of Commerce (商务部) 新闻发布 landing at "
        "/xwfb/index.html. 7 sections (领导人活动, 部领导活动, 日常新闻发布, 新闻"
        "发言人谈话, 司局负责人发布, 例行新闻发布会, 专题发布), each previewing ~6 "
        "most-recent items. SCOPE: landing PREVIEW only (~42 docs) — MOFCOM's deep "
        "per-section archives are JS-rendered with no static pagination path. Each "
        "record has section, title, date (from [YYYY-MM-DD] span), url, doc_type."
    ),
    columns=[
        ScrawColumn(name="section", nullable=False,
                    description="news section: 领导人活动/日常新闻发布/例行新闻发布会/... (from <h4 class='sTitle_02'>)",
                    source_field="h4.sTitle_02", semantic_type="category"),
        ScrawColumn(name="title", nullable=False,
                    description='document title (from <a title="..."> attribute)',
                    source_field="a@title", semantic_type="title"),
        ScrawColumn(name="date", type="date", nullable=True,
                    description="publish date YYYY-MM-DD (from <span>[YYYY-MM-DD]</span>)",
                    source_field="span", semantic_type="date"),
        ScrawColumn(name="url", primary_key=True, nullable=False,
                    description="absolute document URL (.html/.shtml)",
                    source_field="a@href", semantic_type="url"),
        ScrawColumn(name="doc_type", nullable=False,
                    description="document format derived from URL extension: html or pdf",
                    source_field="url:ext", semantic_type="category"),
    ],
    archives=[ScrawArchive(section="新闻发布", subsection="landing", url=LANDING)],
    crawl={
        "scope": "新闻发布 landing at /xwfb/index.html (7 sections, ~6 items each = ~42 docs)",
        "default_max_pages": 1,
        "all_flag": False,
        "pagination": "none — landing preview only; deep per-section archives are JS-rendered",
        "item_selector": "div[ergodic=view] h4.sTitle_02 + ul li",
        "fields": {"title": "a@title", "date": "span [YYYY-MM-DD]", "url": "a@href"},
        "coverage_note": "PREVIEW scope (~42 latest docs), not full archive",
    },
)

_DATE_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2})\]")


def doc_type(url: str) -> str:
    return "pdf" if urlparse(url).path.lower().endswith(".pdf") else "html"


def parse_landing(page, base_url):
    for c in page.css('[ergodic="view"]'):
        for h4 in c.css("h4.sTitle_02"):
            # section name: from <a> inside h4, else h4 text
            name = ""
            h4_a = h4.css("a")
            if h4_a:
                name = h4_a[0].get_all_text(strip=True)
            if not name:
                name = (h4.get_all_text(strip=True) or "").split("\n")[0].strip()
            # the <ul> follows the h4 (or its wrapping <a>)
            anchor = h4.parent if h4.parent.tag == "a" else h4
            ul = anchor.next
            if ul is None or ul.tag != "ul":
                continue
            for li in ul.css("li"):
                links = li.css("a")
                if not links:
                    continue
                a = links[0]
                href = (a.attrib.get("href") or "").strip()
                if not href:
                    continue
                url = urljoin(base_url, href)
                title = (a.attrib.get("title") or a.get_all_text(strip=True) or "").strip()
                date = ""
                for sp in li.css("span"):
                    m = _DATE_RE.search(sp.get_all_text(strip=True) or "")
                    if m:
                        date = m.group(1)
                        break
                yield {"section": name, "title": title, "date": date, "url": url, "doc_type": doc_type(url)}


def crawl():
    page = Fetcher().get(LANDING)
    return list(parse_landing(page, LANDING))


def main():
    recs = crawl()
    # per-section spread
    from collections import Counter
    spread = Counter(r["section"] for r in recs)
    for sec, n in spread.items():
        print(f"# [{sec}] {n} docs", file=sys.stderr)
    print(f"# TOTAL: {len(recs)} docs across {len(spread)} sections (landing preview)", file=sys.stderr)
    print(json.dumps(recs, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
