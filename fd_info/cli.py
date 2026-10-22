"""一轮拉取入口（供 k8s CronJob 调用）。

用法::

    FD_INFO_DB=/data/fd_info.db python -m fd_info.cli fetch --source cninfo --days 2
    python -m fd_info.cli health
    python -m fd_info.cli list --limit 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .body_store import SpoolBodyStore
from .fetchers import DEFAULT_ENABLED, SOURCES
from .fetchers.base import day_bounds
from .fetchers.ministry import fetch_body as ministry_body
from .service import InfoService
from .storage import Store


def _service() -> InfoService:
    store = Store(os.environ.get("FD_INFO_DB", "./fd_info.db"))
    body_store = SpoolBodyStore(os.environ.get("FD_INFO_SPOOL", "./fd_info_spool"))
    return InfoService(store, body_store)


def _window(days: int) -> tuple[str, str]:
    return day_bounds(days)


def cmd_fetch(args: argparse.Namespace) -> int:
    service = _service()
    names = args.source or list(DEFAULT_ENABLED)
    since, until = _window(args.days)
    out = []
    for name in names:
        cls = SOURCES.get(name)
        if cls is None:
            out.append({"source": name, "status": "unavailable", "reason": "未注册的来源"})
            continue
        source = cls()
        result = source.fetch(since, until)
        body_fetcher = ministry_body if name == "cn-gov" else None
        summary = service.ingest(result, body_fetcher=body_fetcher)
        out.append(summary.to_dict())
    print(json.dumps({"since": since, "until": until, "runs": out}, ensure_ascii=False))
    return 0 if all(r.get("status") in ("ok", "partial") for r in out) else 1


def cmd_health(_: argparse.Namespace) -> int:
    print(json.dumps({"sources": _service().health()}, ensure_ascii=False))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    print(json.dumps(_service().list_since(limit=args.limit), ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fd-info")
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch", help="拉取一轮信息条目")
    fetch.add_argument("--source", action="append", choices=sorted(SOURCES))
    fetch.add_argument("--days", type=int, default=2)
    fetch.set_defaults(func=cmd_fetch)

    health = sub.add_parser("health", help="来源健康度")
    health.set_defaults(func=cmd_health)

    listing = sub.add_parser("list", help="列出最近条目")
    listing.add_argument("--limit", type=int, default=20)
    listing.set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
