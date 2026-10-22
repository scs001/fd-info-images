"""详情抓取与落盘（`gov` 域）——fd-cn-gov 的**纯增量**能力。

与 `fd_cn_gov/scripts/` 下 11 个列表脚本的关系：那 11 个脚本只抓列表页并把
JSON print 到 stdout（服务柏讯线的指标目录，输出契约不可变）；本包补两件
它们没有的能力——**详情页抓取**与**落盘为引擎摄取契约的 JSONL**。

不改既有脚本、不改其输出契约、不共享代码（详见 design D5 与 storage 模块注释）。

命令入口见 `__main__.py`：
    python -m fd_cn_gov.detail list --script mnr_tzgg_archive --max-pages 2
    python -m fd_cn_gov.detail crawl --script mnr_tzgg_archive --max-items 20
"""
from __future__ import annotations

from .fetch import FetchPolicy, FetchResult, fetch_detail
from .pipeline import (
    DetailRecord,
    ParserUnavailable,
    RunStats,
    build_record,
    load_parser,
    run_detail_crawl,
)
from .sources import ListEntry, load_list_products, normalize_url, script_to_source
from .state import (
    empty_state,
    parse_state,
    remember_failure,
    remember_success,
    select_targets,
)
from .storage import (
    manifest_key,
    raw_key,
    root_summary,
    state_key,
    structured_part_key,
    structured_prefix,
)

__all__ = [
    "DetailRecord",
    "FetchPolicy",
    "FetchResult",
    "ListEntry",
    "ParserUnavailable",
    "RunStats",
    "build_record",
    "empty_state",
    "fetch_detail",
    "load_list_products",
    "load_parser",
    "manifest_key",
    "normalize_url",
    "parse_state",
    "raw_key",
    "remember_failure",
    "remember_success",
    "root_summary",
    "run_detail_crawl",
    "script_to_source",
    "select_targets",
    "state_key",
    "structured_part_key",
    "structured_prefix",
]
