"""对象存储契约：公文详情产物的键布局（<bucket>/gov/{raw,structured,manifest}/）。

与引擎摄取契约对齐：引擎 `ingest.iter_structured` 读
`<bucket>/<domain>/structured/*.jsonl`，故本模块只负责把 JSONL 写到该前缀，
不定义第二套消费协议。

键布局照 `fd-law-data/lib/legal-sources/storage.js` 的成熟形态（该仓已在产）：
  · structured 分片键含 run id（UTC 时间戳 YYYYMMDDHHMMSS）——**分片一旦完整即不可变**。
    只带日期会让同一天的第二次运行覆盖第一次的分片，而 state 仍声称旧分片已完成，
    造成永久跳过的集合（该仓 ccdi full5 覆盖 full3 的真实事故）。run id 进键位
    使「新运行的分片排序在后」，折叠时天然 latest-wins。
  · state.json 与 manifest 不在 structured 前缀下——覆盖度统计扫该前缀时
    不会把状态文件误当成语料分片。

本文件只依赖标准库：键构造是纯字符串运算，不引 S3 客户端（调用方注入）。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

ROOT = "gov"
_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def domain_prefix(domain: str = ROOT) -> str:
    if not _DOMAIN_RE.match(domain):
        raise ValueError(f"非法域标识: {domain!r}")
    return domain


def structured_prefix(domain: str = ROOT) -> str:
    return f"{domain_prefix(domain)}/structured/"


def raw_prefix(domain: str = ROOT) -> str:
    return f"{domain_prefix(domain)}/raw/"


def manifest_prefix(domain: str = ROOT) -> str:
    return f"{domain_prefix(domain)}/manifest/"


def state_key(domain: str = ROOT) -> str:
    return f"{domain_prefix(domain)}/state.json"


def structured_part_key(run_id: str, part: int, domain: str = ROOT) -> str:
    """分片键。run_id 必须是 14 位 UTC 时间戳（校验而非假设）。"""
    stamp = str(run_id or "")
    if not re.fullmatch(r"\d{14}", stamp):
        raise ValueError(f"run id 必须是 14 位 UTC 时间戳(YYYYMMDDHHMMSS)，实得: {run_id!r}")
    return f"{structured_prefix(domain)}run-{stamp}-part{int(part):03d}.jsonl"


def manifest_key(run_id: str, domain: str = ROOT) -> str:
    stamp = str(run_id or "")
    if not re.fullmatch(r"\d{14}", stamp):
        raise ValueError(f"run id 必须是 14 位 UTC 时间戳，实得: {run_id!r}")
    return f"{manifest_prefix(domain)}run-{stamp}.manifest.json"


def safe_segment(value: str, *, max_length: int = 180) -> str:
    """让 URL/source_id 进键时不引入斜杠（键是扁平字符串，斜杠会造出假目录）。"""
    from urllib.parse import quote

    text = str(value or "unnamed")
    encoded = quote(text, safe="")
    if len(encoded) > max_length:
        encoded = encoded[:max_length]
    return encoded or "unnamed"


def raw_key(source_id: str, filename: str, domain: str = ROOT) -> str:
    return f"{raw_prefix(domain)}{safe_segment(source_id)}/{safe_segment(filename)}"


def run_id_utc(when: datetime | None = None) -> str:
    return (when or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")


def root_summary(domain: str = ROOT) -> dict:
    return {
        "root": domain_prefix(domain),
        "state": state_key(domain),
        "structured": structured_prefix(domain),
        "raw": raw_prefix(domain),
        "manifest": manifest_prefix(domain),
    }
