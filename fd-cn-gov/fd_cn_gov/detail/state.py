"""增量状态：内容哈希水位 + 失败台账（断点续抓的依据）。

形态与 `fd-law-data/lib/legal-sources/state.js` 同构（该仓已在产），但独立实现——
两仓分属不同产品线，共享代码会造出跨线耦合；这里对齐的是**契约**不是代码。

`hashes`：source_id -> 观测到的正文内容哈希。未变则跳过（省一次解析与一次嵌入）。
`failures`：保留重试证据，且**不覆盖已成功的哈希**——某条上次成功、本次刷新失败时，
下轮仍能靠哈希判定「未变」而不必重抓，失败记录只用于呈现与重试排序。
`watermark`：已抓取的最大发布时间（增量水位）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

# 哈希表规模上限。超限是 WARNING 而非裁剪——静默裁剪会让下轮全量重抓且日志无解释。
DEFAULT_MAX_HASH_ENTRIES = 20000


def empty_state(domain: str) -> dict:
    return {
        "version": 1,
        "domain": domain,
        "hashes": {},
        "failures": {},
        "watermark": None,
        "updatedAt": None,
    }


def parse_state(raw: bytes | str | None, domain: str) -> dict:
    """解析状态；损坏时降级为空状态（绝不臆造成功）。"""
    if not raw:
        return empty_state(domain)
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return empty_state(domain)
    base = empty_state(domain)
    base.update({k: v for k, v in data.items() if k in base})
    base["hashes"] = dict(data.get("hashes") or {})
    base["failures"] = dict(data.get("failures") or {})
    return base


def dump_state(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True)


def state_size_report(state: dict, *, max_entries: int = DEFAULT_MAX_HASH_ENTRIES) -> dict:
    n = len(state.get("hashes") or {})
    return {"hashes": n, "max": max_entries, "exceeded": n > max_entries}


def remember_success(state: dict, source_id: str, content_hash: str, published: str | None = None) -> dict:
    if not source_id or not content_hash:
        raise ValueError("remember_success 需要 source_id 与 content_hash")
    state["hashes"][source_id] = content_hash
    state.setdefault("failures", {}).pop(source_id, None)
    if published and (not state.get("watermark") or str(published) > str(state["watermark"])):
        state["watermark"] = published
    return state


def remember_failure(state: dict, source_id: str, reason: str, kind: str = "detail") -> dict:
    prior = (state.get("failures") or {}).get(source_id)
    now = datetime.now(timezone.utc).isoformat()
    state.setdefault("failures", {})[source_id] = {
        "sourceId": source_id,
        "kind": kind,
        "reason": str(reason or "unknown")[:1000],
        "attempts": (prior or {}).get("attempts", 0) + 1,
        "firstSeenAt": (prior or {}).get("firstSeenAt", now),
        "lastSeenAt": now,
    }
    return state


def _field(entry: object, name: str):
    """取条目的字段：兼容 dict 与 dataclass/对象两种形态。

    调用方有两条路径（列表产物解析出的 ListEntry，或测试/脚本直接传的 dict），
    强行要求一种形态会让另一条路径在运行时才炸——这类接口用鸭子取值更稳。
    """
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


def select_targets(entries: list, state: dict, *, mode: str = "incremental") -> tuple[list, list]:
    """选出需抓取的条目。

    条目须有 `source_id`；可选 `hash`（列表页可算出的内容指纹，用于列表层就
    判定未变）。列表与详情必须读**同一个键**，否则详情侧记的成功列表侧永远
    查不到（该仓 party 源的真实事故：193 篇 0 命中）。
    """
    targets: list = []
    skipped: list = []
    hashes = state.get("hashes") or {}
    for entry in entries:
        sid = _field(entry, "source_id")
        if not sid:
            skipped.append({"entry": entry, "reason": "missing source_id"})
            continue
        known = hashes.get(sid)
        declared = _field(entry, "hash")
        if mode != "full" and known and (not declared or declared == known):
            skipped.append({"source_id": sid, "reason": "unchanged"})
            continue
        targets.append(entry)
    # 新条目优先，历史失败条目排尾：把慢性失败前置会让每轮开局就超失败率阈值
    failed = set((state.get("failures") or {}).keys())
    targets.sort(key=lambda e: 1 if _field(e, "source_id") in failed else 0)
    return targets, skipped
