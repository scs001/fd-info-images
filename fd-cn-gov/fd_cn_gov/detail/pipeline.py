"""详情抓取管线：列表产物 -> 抓详情 -> 垂域解析器 -> structured/*.jsonl + manifest + state。

职责边界（design D5）：
  · 抓取与落盘在**本仓**（采集仓，已有礼貌抓取基建）
  · 正文与元数据解析**复用引擎垂域解析器**（`fd_text_core.verticals.gov.parser`），
    本模块**不复制第二份解析实现**——复制会造出两份会各自漂移的解析器
  · 摄取（入 PG）不在本模块：引擎的 `ingest.load_documents` 消费本模块产出的 JSONL

依赖方向：本模块**可选**依赖 `fd-text-core`（仅详情抓取路径需要）。
fd-cn-gov 的既有能力（11 个列表脚本 / catalog / registry）不依赖它，
故该依赖是延迟导入 + 明确报错，不写进包依赖——那会破坏本仓的 PyPI 发布路径。

JSONL 记录契约（与引擎摄取契约对齐，两仓间的唯一接口）：
    {
      "domain": "gov",                    # 引擎分区键
      "source": "mof",                    # 部委标识 -> provenance.source
      "source_id": "http://...htm",       # 规范化 URL -> provenance.stable_id
      "url": "http://...htm",             # 原始链接（对外回链用）
      "title": "...",
      "published": "2026-10-08" | null,   # 列表页日期
      "doc_date": "2026-09-28" | null,    # 解析出的成文日期（落款优先）
      "content": "正文全文",
      "metadata": {...}                   # 结构树（文号/机关/文种/层级/附件/引用）
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .fetch import FetchPolicy, absolutize, fetch_detail
from .sources import ListEntry
from .state import (
    dump_state,
    parse_state,
    remember_failure,
    remember_success,
    select_targets,
    state_size_report,
)
from .storage import (
    manifest_key,
    run_id_utc,
    state_key,
    structured_part_key,
)


class ParserUnavailable(RuntimeError):
    """引擎垂域解析器不可用（未安装 fd-text-core）。"""


def load_parser() -> Callable[[str], Any]:
    """取引擎的公文垂域解析器。

    延迟导入 + 明确报错：本仓既有能力不依赖 fd-text-core，
    缺它时给出可执行的修复指引，而不是让 import 错误在半路炸开。
    """
    try:
        from fd_text_core.verticals.gov.parser import parse_gov_doc
    except ImportError as exc:  # pragma: no cover - 环境相关
        raise ParserUnavailable(
            "公文详情抓取需要引擎垂域解析器（fd-text-core）。"
            "安装：pip install -e /path/to/fd-text-core（或设置 PYTHONPATH）"
        ) from exc
    return parse_gov_doc


@dataclass
class DetailRecord:
    """一篇公文的详情产物（= JSONL 的一行）。"""

    domain: str
    source: str
    source_id: str
    url: str
    title: str | None
    published: str | None
    doc_date: str | None
    content: str
    metadata: dict = field(default_factory=dict)

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "domain": self.domain,
                "source": self.source,
                "source_id": self.source_id,
                "url": self.url,
                "title": self.title,
                "published": self.published,
                "doc_date": self.doc_date,
                "content": self.content,
                "metadata": self.metadata,
            },
            ensure_ascii=False,
        )


def build_record(entry: ListEntry, html: str, *, parser: Callable | None = None) -> DetailRecord:
    """HTML -> DetailRecord（结构树全部进 metadata，供 facet 与结构检索消费）。"""
    parse = parser or load_parser()
    doc = parse(html)

    # 附件相对链接转绝对：解析器拿到的是 HTML 片段，不知道 base URL
    attachments = [
        {"name": a.name, "url": absolutize(a.url, entry.url), "kind": a.kind}
        for a in doc.attachments
    ]
    metadata = {
        "doc_number": doc.doc_number,
        "issuer": doc.issuer,
        # 归一前的原始机关名：检索用归一后的 issuer（facet 词表可匹配），
        # 原值留档供追溯（「这条为什么归到其他」要能查回来）。
        # 用 getattr 兜底：旧版引擎解析器没有该字段，缺它不应让整批抓取失败。
        "issuer_raw": getattr(doc, "issuer_raw", None),
        "addressee": doc.addressee,
        "doc_type": doc.doc_type,
        "index_no": doc.index_no,
        "site_date": doc.site_date,
        "sections": [{"path": p, "text": t} for p, t in doc.sections],
        "attachments": attachments,
        "cited_docs": doc.cited_docs,
        # 列表页给的分栏信息（facet 之外的溯源线索）
        "section": entry.section,
        "subsection": entry.subsection,
    }
    return DetailRecord(
        domain="gov",
        source=entry.source,
        source_id=entry.source_id,
        url=entry.url,
        # 解析出的标题优先于列表页标题（列表页标题偶有截断）
        title=doc.title or entry.title or None,
        published=entry.published,
        doc_date=doc.date,
        content=doc.body,
        metadata=metadata,
    )


@dataclass
class RunStats:
    """一轮采集的统计。失败数与原因必须如实呈现，不吞掉。"""

    listed: int = 0
    skipped_unchanged: int = 0
    fetched: int = 0
    parse_failed: int = 0
    failed: int = 0
    written: int = 0
    failures: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "listed": self.listed,
            "skipped_unchanged": self.skipped_unchanged,
            "fetched": self.fetched,
            "parse_failed": self.parse_failed,
            "failed": self.failed,
            "written": self.written,
        }


def run_detail_crawl(
    entries: list[ListEntry],
    *,
    s3: Any,
    bucket: str,
    domain: str = "gov",
    policy: FetchPolicy | None = None,
    mode: str = "incremental",
    max_items: int = 0,
    part_size: int = 200,
    fetcher: Any = None,
    parser: Callable | None = None,
    state_raw: str | bytes | None = None,
    log: Callable[[str], None] = print,
) -> RunStats:
    """抓详情、解析、落盘。返回统计（含逐条失败原因）。

    `s3` 需提供 get_object/put_object（boto3 客户端或测试替身），
    键构造见 storage 模块——本函数不碰 S3 键的拼装细节。

    断点续抓：state 里的哈希水位决定跳过哪些条目；本轮结束后写回 state。
    分片一旦完整即不可变（键含 run id），故崩溃重跑不会覆盖已落盘的分片。
    """
    stats = RunStats(listed=len(entries))
    state = parse_state(state_raw, domain)

    targets, skipped = select_targets(entries, state, mode=mode)
    stats.skipped_unchanged = len(skipped)
    if max_items:
        targets = targets[:max_items]

    records: list[DetailRecord] = []
    for entry in targets:
        res = fetch_detail(entry.url, policy, fetcher=fetcher)
        if not res.ok:
            stats.failed += 1
            remember_failure(state, entry.source_id, res.error or "抓取失败", kind="fetch")
            stats.failures.append(
                {"source": entry.source, "url": entry.url, "stage": "fetch", "reason": res.error}
            )
            log(f"[fail/fetch] {res.error} {entry.url}")
            continue
        stats.fetched += 1

        try:
            rec = build_record(entry, res.body, parser=parser)
        except Exception as exc:  # 解析异常如实计入，不静默跳过
            stats.parse_failed += 1
            reason = f"{type(exc).__name__}: {exc}"
            remember_failure(state, entry.source_id, reason, kind="parse")
            stats.failures.append(
                {"source": entry.source, "url": entry.url, "stage": "parse", "reason": reason}
            )
            log(f"[fail/parse] {reason} {entry.url}")
            continue

        if not rec.content.strip():
            # 正文为空是数据问题（容器没选中/页面确实是空壳）：记失败，不入语料
            stats.parse_failed += 1
            remember_failure(state, entry.source_id, "正文为空（容器未命中或页面无正文）", kind="empty")
            stats.failures.append(
                {"source": entry.source, "url": entry.url, "stage": "empty", "reason": "正文为空"}
            )
            log(f"[fail/empty] 正文为空 {entry.url}")
            continue

        records.append(rec)
        import hashlib

        remember_success(
            state, entry.source_id, hashlib.sha256(rec.content.encode("utf-8")).hexdigest(),
            published=rec.published,
        )

    # 落盘：分片 -> manifest -> state（顺序有讲究，见下）
    run_id = run_id_utc()
    parts_written = 0
    for i in range(0, len(records), max(1, part_size)):
        chunk = records[i : i + part_size]
        key = structured_part_key(run_id, parts_written + 1, domain)
        body = "\n".join(r.to_jsonl() for r in chunk) + "\n"
        s3.put_object(Bucket=bucket, Key=key, Body=body.encode("utf-8"),
                      ContentType="application/x-ndjson")
        parts_written += 1
        stats.written += len(chunk)

    size_report = state_size_report(state)
    manifest = {
        "domain": domain,
        "run_id": run_id,
        "bucket": bucket,
        "parts": parts_written,
        "records": stats.written,
        "stats": stats.as_dict(),
        "watermark": state.get("watermark"),
        "state_hashes": size_report["hashes"],
        "failures": stats.failures[:200],  # manifest 里只留前 200 条，全量在 state
        "sources": sorted({e.source for e in entries}),
    }
    # 先写 manifest 再写 state：manifest 是「这批分片已完整落盘」的凭据，
    # state 是「哪些条已成功」的水位。反序的话，写完 state 就崩会让下轮
    # 认为这些条已抓、而分片其实不存在——数据永久缺失且无迹可查。
    s3.put_object(Bucket=bucket, Key=manifest_key(run_id, domain),
                  Body=json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"),
                  ContentType="application/json")
    s3.put_object(Bucket=bucket, Key=state_key(domain),
                  Body=dump_state(state).encode("utf-8"),
                  ContentType="application/json")

    if size_report["exceeded"]:
        log(f"[warn] state 哈希表 {size_report['hashes']} 条已超上限 {size_report['max']}；"
            "未裁剪（静默裁剪会导致下轮全量重抓且日志无解释）")
    return stats
