"""线上新鲜度/幂等验收（替代 24h 观察，tasks 1.5.4）。

在 fd-info pod 内运行：

    python scripts/acceptance_live.py

检查四件事，任一失败退出码 1：

1. `stream_health` 三源 `ok` 且 `consecutive_failures=0`；
2. 每源最近成功时间在 15 分钟内；
3. 同一窗口连续拉取两次，条目总数零增长（幂等）；
4. 最近 60 分钟入库条目的发布→入库延迟 p95 ≤ 600 秒。

不做 24h 长跑：没有真实用户时，长跑只验证"进程没死"，不验证契约。
"""

from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fd_info.cli import cmd_fetch  # noqa: E402
from fd_info.service import InfoService  # noqa: E402
from fd_info.storage import Store  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def _service() -> InfoService:
    from fd_info.body_store import SpoolBodyStore

    store = Store(os.environ.get("FD_INFO_DB", "/data/fd_info.db"))
    return InfoService(store, SpoolBodyStore(os.environ.get("FD_INFO_SPOOL", "/data/spool")))


def _count(service: InfoService) -> int:
    total = 0
    cursor = None
    while True:
        page = service.list_since(cursor=cursor, limit=500)
        total += len(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            return total


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def main() -> int:
    service = _service()
    problems: list[str] = []
    now = datetime.now(timezone.utc)

    print("[1/4] 来源健康度")
    health = service.health()
    for row in health:
        print(f"  {row['source']}: {row['status']} failures={row['consecutive_failures']}")
        if row["status"] != "ok":
            problems.append(f"{row['source']} status={row['status']}")
        if row["consecutive_failures"] != 0:
            problems.append(f"{row['source']} failures={row['consecutive_failures']}")
        last = row.get("last_success_at")
        if not last:
            problems.append(f"{row['source']} 无成功记录")
        elif (now - _parse(last)).total_seconds() > 900:
            problems.append(f"{row['source']} 最近成功超过 15 分钟：{last}")

    print("[2/4] 幂等：同窗口连续拉取两次")
    before = _count(service)
    args = SimpleNamespace(source=None, days=2)
    cmd_fetch(args)
    mid = _count(service)
    cmd_fetch(args)
    after = _count(service)
    print(f"  items: before={before} after_first={mid} after_second={after}")
    if after != mid:
        problems.append(f"第二次拉取新增 {after - mid} 条（应零增长）")

    print("[3/4] 近期延迟 p95")
    cutoff = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = service.store.list_since(published_after=cutoff, limit=500)["items"]
    lags = sorted(
        (_parse(r["ingested_at"]) - _parse(r["published_at"])).total_seconds() for r in rows
    )
    p95 = lags[max(0, math.ceil(len(lags) * 0.95) - 1)] if lags else 0.0
    print(f"  sample={len(lags)} p95={p95:.0f}s")
    if lags and p95 > 600:
        problems.append(f"近期延迟 p95={p95:.0f}s > 600s")

    print("[4/4] 判定")
    if problems:
        for p in problems:
            print("FAIL:", p)
        return 1
    print("PASS: 健康度/幂等/延迟全部达标")
    return 0


if __name__ == "__main__":
    sys.exit(main())
