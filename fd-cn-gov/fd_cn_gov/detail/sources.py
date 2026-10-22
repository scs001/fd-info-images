"""列表产物读取：消费既有 11 个列表脚本的输出，不改它们。

列表脚本的既有输出契约（**本模块只读，不改**）：stdout 是 JSON 数组，
每项含 `title` / `date` / `url` / `section`（+ 个别源的 `subsection` /
`department` / `doc_type`）。本模块把该数组规范化为详情抓取所需的条目：
  · `source_id` = 文档 URL 的规范化形式（引擎 provenance 的 stable_id 口径）
  · `source` = 部委标识（从脚本名映射，不由 URL 猜测）
  · `published` = 列表页给出的日期（详情页成文日期可能不同，两者都留）

为什么读 stdout 而不是 import 脚本的采集函数：既有脚本把抓取与打印写在一起，
import 会连带执行其 argparse 与网络路径；而 stdout 是它们**承诺的**契约。
将来柏讯线若给列表脚本加 JSON 落盘，本模块加一个 reader 即可，不必改调用方。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

# 脚本名 -> 部委标识（与 facet 词表的 issuer 取值对应，来源是 README 的 11 源清单）
SOURCE_BY_SCRIPT = {
    "mee_gsgg_archive": "mee",
    "mem_tzgg_archive": "mem",
    "mnr_tzgg_archive": "mnr",
    "moa_govpublic_archive": "moa",
    "mof_gkml_archive": "mof",
    "mofcom_xwfb_archive": "mofcom",
    "mohurd_xinwen_archive": "mohurd",
    "mot_shuju_archive": "mot",
    "ndrc_tzgg_archive": "ndrc",
    "pbc_xinwen_archive": "pbc",
    "safe_whxw_archive": "safe",
}


@dataclass
class ListEntry:
    """一条列表条目（详情抓取的输入）。"""

    source: str
    source_id: str  # 规范化 URL
    url: str
    title: str
    published: str | None
    section: str = ""
    subsection: str = ""
    doc_type: str = ""

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "url": self.url,
            "title": self.title,
            "published": self.published,
            "section": self.section,
            "subsection": self.subsection,
            "doc_type": self.doc_type,
        }


def normalize_url(url: str) -> str:
    """URL 规范化 -> source_id / stable_id。

    只做**不改变语义**的归一：
      · 去 fragment（#anchor 不影响资源身份）
      · scheme 与 host 转小写（URL 规范规定二者大小写不敏感）
      · 去默认端口与尾部空 query
    保留 path 大小写与 query —— 站点用它们区分资源（实测 ndrc 的
    `t20260921_1407757.html` 大小写敏感），擅自改写会造出 404 或撞键。
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = parts.netloc.lower()
    if (scheme == "http" and host.endswith(":80")) or (scheme == "https" and host.endswith(":443")):
        host = host.rsplit(":", 1)[0]
    query = parts.query if parts.query not in ("", "?") else ""
    return urlunsplit((scheme, host, parts.path, query, ""))


def script_to_source(script: str) -> str:
    """脚本名 -> 部委标识。未知脚本如实报错而非猜一个标识。"""
    name = re.sub(r"\.py$", "", str(script or ""))
    if name not in SOURCE_BY_SCRIPT:
        raise KeyError(f"未知列表脚本 {script!r}；可用={sorted(SOURCE_BY_SCRIPT)}")
    return SOURCE_BY_SCRIPT[name]


def load_list_products(raw: str | bytes | list, script: str) -> list[ListEntry]:
    """把列表脚本的 stdout（或已解析的数组）规范化为 ListEntry 列表。

    容错策略：缺 `url` 的条目**跳过并计数**（由调用方决定是否告警），
    但不静默丢弃整批——单条脏数据不该让整轮采集失败。
    """
    source = script_to_source(script)
    data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    if not isinstance(data, list):
        raise ValueError(f"列表产物应为 JSON 数组，实得 {type(data).__name__}")

    out: list[ListEntry] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        sid = normalize_url(url)
        if sid in seen:  # 列表页偶有重复条目（同文多栏目），按 URL 去重
            continue
        seen.add(sid)
        out.append(
            ListEntry(
                source=source,
                source_id=sid,
                url=url,
                title=str(item.get("title") or "").strip(),
                published=(str(item.get("date") or "").strip() or None),
                section=str(item.get("section") or "").strip(),
                subsection=str(item.get("subsection") or "").strip(),
                doc_type=str(item.get("doc_type") or "").strip(),
            )
        )
    return out
