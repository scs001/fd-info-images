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
import time
from pathlib import Path

from .body_store import SpoolBodyStore
from .fetchers import DEFAULT_ENABLED, SOURCES
from .fetchers.base import day_bounds
from .fetchers.cninfo import fetch_body as cninfo_body
from .fetchers.cninfo import fetch_body_detailed as cninfo_body_detailed
from .fetchers.ministry import fetch_body as ministry_body
from .models import InfoItem
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
        body_fetcher = {"cn-gov": ministry_body, "cninfo": cninfo_body}.get(name)
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


def cmd_backfill_bodies(args: argparse.Namespace) -> int:
    """重试 body_status=failed 的条目正文（design：摄取只在新建/变更时取正文，
    瞬时上游失败会永久停在 failed——本命令是唯一的自愈路径）。

    已接正文的源：ministry（HTML）与 announcement/cninfo（PDF，2026-10-26 起）；
    interaction 在 fetch 时自带正文、不在本列。可选 --source 限定（source_type：
    ministry|announcement）、--limit 截断、--sleep 上游礼貌间隔。
    """
    service = _service()
    store = service.store
    body_store = service.body_store
    fetchers = {"ministry": ministry_body, "announcement": cninfo_body_detailed}
    db = store._conn
    sql = "select item_id, source, source_id, source_type, published_at, title, url, doc_id from info_items where body_status='failed'"
    params: list[str] = []
    if args.source:
        sql += " and source_type=?"
        params.append(args.source)
    sql += " order by published_at desc limit ?"
    params.append(str(args.limit))
    rows = db.execute(sql, params).fetchall()
    ok = skipped = failed = 0
    for item_id, source, source_id, stype, pub, title, url, doc_id in rows:
        body_fetcher = fetchers.get(stype)
        if body_fetcher is None or not url:
            skipped += 1
            continue
        body = body_fetcher(url)
        if isinstance(body, tuple):  # 详细版：分类处置
            body, status = body
            if not body:
                if status == "no_text":
                    store.mark_body(item_id, doc_id=None, body_status="no_text")
                    skipped += 1
                else:
                    failed += 1
                continue
        item = InfoItem(source=source, source_id=source_id, source_type=stype,
                        published_at=pub, title=title, url=url, doc_id=doc_id)
        new_doc = body_store.put(item, body)
        store.mark_body(item_id, doc_id=new_doc, body_status="stored")
        ok += 1
        if args.sleep:
            time.sleep(args.sleep)
    print(json.dumps({"checked": len(rows), "backfilled": ok, "still_failed": failed,
                      "skipped": skipped}, ensure_ascii=False))
    return 0 if failed == 0 else 1


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

    backfill = sub.add_parser("backfill-bodies", help="重试 failed 条目的正文（ministry/cninfo）")
    backfill.add_argument("--source", default=None, help="ministry|announcement（缺省=全部已接正文的源）")
    backfill.add_argument("--limit", type=int, default=200)
    backfill.add_argument("--sleep", type=float, default=1.0)
    backfill.set_defaults(func=cmd_backfill_bodies)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
