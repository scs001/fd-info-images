"""Catalog crawl of the Ministry of Transport (交通运输部) data hub /shuju/.

The landing page /shuju/index.html only previews ~12 docs per section. The real
archive lives behind 4 sections, each with one or more sub-archive index pages:

  数据开放  → /shuju/shujukaifang/index.html
  统计数据  → 7 archives (公路水路/铁路/民航/邮政/城市客运/港口/投资)
  分析公报  → 3 archives (行业公报/经济分析/运力分析)
  运价指数  → 4 archives (沿海散货/集装箱/长江/珠江)

Each archive lists docs as
  <li class="news-item"><a class="news-link" href="...">
    <span class="news-title">..</span><span class="news-date">YYYY-MM-DD</span>
  </a></li>
and paginates index.html (page 1) → index_1.html (page 2) → index_2.html (page
3) … 404 on overflow. 16 docs/page.

Default crawl is page 1 of each archive (latest ~16 docs/section, ~240 rows).
--max-pages N caps pages per archive; --all / --max-pages 0 = full crawl.

Run:  uv run --directory mcp/scrapling-uv-mcp python scripts/mot_shuju_archive.py [--all|--max-pages N]
"""
import argparse
import json
import sys
import time
from urllib.parse import urljoin, urlparse

from scrapling.fetchers import Fetcher

from fd_cn_gov.scraw_contract import ScrawArchive, ScrawColumn, ScrawManifest

ARCHIVES = [
    ("数据开放", "数据开放", "https://www.mot.gov.cn/shuju/shujukaifang/index.html"),
    ("统计数据", "公路水路运输", "https://www.mot.gov.cn/shuju/tongjishuju/gonglushuilu/index.html"),
    ("统计数据", "铁路", "https://www.mot.gov.cn/shuju/tongjishuju/tielu/index.html"),
    ("统计数据", "民航", "https://www.mot.gov.cn/shuju/tongjishuju/minhang/index.html"),
    ("统计数据", "邮政", "https://www.mot.gov.cn/shuju/tongjishuju/youzheng/index.html"),
    ("统计数据", "城市客运", "https://www.mot.gov.cn/shuju/tongjishuju/chengshikeyun/index.html"),
    ("统计数据", "港口", "https://www.mot.gov.cn/shuju/tongjishuju/gangkoutuntuliang/index.html"),
    ("统计数据", "投资", "https://www.mot.gov.cn/shuju/tongjishuju/gudingzctz/index.html"),
    ("分析公报", "行业公报", "https://www.mot.gov.cn/shuju/fenxigongbao/hangyegongbao/index.html"),
    ("分析公报", "经济分析", "https://www.mot.gov.cn/shuju/fenxigongbao/jingjifenxi/index.html"),
    ("分析公报", "运力分析", "https://www.mot.gov.cn/shuju/fenxigongbao/yunlifenxi/index.html"),
    ("运价指数", "中国沿海散货运价指数", "https://www.mot.gov.cn/shuju/yunjiazhishu/yanhaisanhuoyjzs/index.html"),
    ("运价指数", "中国集装箱运价指数", "https://www.mot.gov.cn/shuju/yunjiazhishu/chukoujizhuangxiangyjzs/index.html"),
    ("运价指数", "长江航运指数分析", "https://www.mot.gov.cn/shuju/yunjiazhishu/cjhangyunzsfx/index.html"),
    ("运价指数", "珠江水运经济运行分析", "https://www.mot.gov.cn/shuju/yunjiazhishu/zjshuiyunjingjiyxfx/index.html"),
]

PER_PAGE = 16
SLEEP = 0.3  # ponytail: gentle pacing — rate-limit insurance on a .gov.cn host

# Stable contract — `register.py` reads this to write the daas database.
# columns here ARE the output record schema (yield in parse_archive must match).
MANIFEST = ScrawManifest(
    name="mot_shuju_archive",
    label="MOT Data Hub Archive (交通运输部数据)",
    url="https://www.mot.gov.cn/shuju/index.html",
    description=(
        "Catalog of statistical / analysis / freight-index documents published "
        "by China's Ministry of Transport (交通运输部), crawled from the /shuju/ "
        "data hub. 4 sections (数据开放/统计数据/分析公报/运价指数) across 15 "
        "sub-archive index pages; each record is one listed document (HTML "
        "report or PDF) with title, publish date, URL, section and subsection. "
        "Paginates index.html→index_{N-1}.html (404 on overflow). Default crawl "
        "= page 1 of each archive (~270 docs); --all for full history."
    ),
    columns=[
        ScrawColumn(name="section", nullable=False,
                    description="top-level data section: 数据开放/统计数据/分析公报/运价指数 (from seed config, not DOM)",
                    source_field="meta:section", semantic_type="category"),
        ScrawColumn(name="subsection", nullable=False,
                    description="specific sub-archive / tab name, e.g. 公路水路运输, 行业公报, 中国沿海散货运价指数 (from seed config)",
                    source_field="meta:subsection", semantic_type="category"),
        ScrawColumn(name="title", nullable=False,
                    description='document title (from <span class="news-title">)',
                    source_field="span.news-title", semantic_type="title"),
        ScrawColumn(name="date", type="date", nullable=False,
                    description='publish date YYYY-MM-DD (from <span class="news-date">)',
                    source_field="span.news-date", semantic_type="date"),
        ScrawColumn(name="url", primary_key=True, nullable=False,
                    description="absolute document URL (.html report or .pdf)",
                    source_field="a.news-link@href", semantic_type="url"),
        ScrawColumn(name="doc_type", nullable=False,
                    description="document format derived from URL extension: html or pdf",
                    source_field="url:ext", semantic_type="category"),
    ],
    archives=[ScrawArchive(section=s, subsection=sub, url=u) for (s, sub, u) in ARCHIVES],
    crawl={
        "scope": "all 4 sections, 15 sub-archive index pages (数据开放 + 7 统计 + 3 分析公报 + 4 运价指数)",
        "default_max_pages": 1,
        "all_flag": True,
        "per_page": PER_PAGE,
        "pagination": "index.html (page 1) → index_{N-1}.html (page N), 404 on overflow",
        "item_selector": "li.news-item",
        "fields": {"title": "span.news-title", "date": "span.news-date", "url": "a.news-link@href"},
    },
)


def page_url(base_index: str, page_no: int) -> str:
    """page 1 = index.html; page N≥2 = index_{N-1}.html (offset-by-1)."""
    if page_no <= 1:
        return base_index
    base = base_index.rsplit("index.html", 1)[0]
    return f"{base}index_{page_no - 1}.html"


def doc_type(url: str) -> str:
    path = urlparse(url).path
    return "pdf" if path.lower().endswith(".pdf") else "html"


def parse_archive(page, base_url, section, subsection):
    """Yield record dicts for one archive page (scrapling CSS selectors)."""
    for li in page.css("li.news-item"):
        links = li.css("a.news-link")
        if not links:
            continue
        href = links[0].attrib.get("href", "").strip()
        if not href:
            continue
        url = urljoin(base_url, href)
        title_spans = li.css("span.news-title")
        date_spans = li.css("span.news-date")
        title = title_spans[0].text.strip() if title_spans else ""
        date = date_spans[0].text.strip() if date_spans else ""
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
        if len(items) < PER_PAGE:
            break  # last page
        page_no += 1
        time.sleep(SLEEP)
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    page_group = ap.add_mutually_exclusive_group()
    page_group.add_argument("--max-pages", type=int, default=1,
                             help="pages per archive (default 1; 0 = full crawl)")
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
