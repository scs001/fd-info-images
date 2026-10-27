# fd-info — 柏讯信息流索引

统一信息条目契约 + 幂等摄取 + 游标查询 + 原文证据定位。**只做索引**：正文写
`fd-text-core` 的语料库，本包不复制正文、不做推送。

## 它解决什么

柏讯已有分析方法论（19 个套件）与图文信息流面板，但没有任何持续流入的信息源：
公告面只有 `year` 没有游标，最快轮询是 5 分钟却没有资讯任务。本包补这条活水。

## 快速开始

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -e ".[test]"
.venv/bin/python -m pytest -q                 # 19+ 项全离线

# 拉一轮（默认公告 + 部委）
FD_INFO_DB=/tmp/fd_info.db FD_INFO_SPOOL=/tmp/fd_info_spool \
  .venv/bin/python -m fd_info.cli fetch --days 2
.venv/bin/python -m fd_info.cli health
```

## 环境变量

| 变量 | 缺省 | 说明 |
|---|---|---|
| `FD_INFO_DB` | `./fd_info.db` | 索引库（SQLite；生产指持久卷） |
| `FD_INFO_SPOOL` | `./fd_info_spool` | 正文 JSONL 暂存（text-core 拉取） |
| `FD_TEXT_CORE_URL` | 未设 | 设置后 `search_info` 转调 text-core 混合检索；否则索引检索并标 `mode=index` |
| `FD_TEXT_CORE_TOKEN` | 未设 | text-core 网关 Bearer |

## 设计边界

见同仓 `docs/ARCHITECTURE.md` 与工作区
`openspec/changes/wire-info-stream-program/design.md`。

## 正文回填（backfill-bodies，2026-10-26）

摄取管线只在条目新建/变更时取正文（pipeline.ingest_result 的 `unchanged → continue`），
瞬时上游失败（部委站点抖动/WAF）会让条目**永久停在 `body_status=failed`**。
`fd-info backfill-bodies` 是唯一自愈路径：按 `published_at` 倒序重试 failed 条目正文
（仅 ministry——cninfo 是 PDF 正文管道缺口、interaction 在 fetch 时自带正文），
成功即写 spool（ship-spool 下个周期推 text-core 入索引）并翻 `stored`。

生产配套：`fd-info-backfill-bodies` CronJob（每日 22:30 UTC，fd-info.yaml，GitOps）。

2026-10-26：ministry 12/12 回填；**cninfo PDF 管道 v2 上产**（`fetch_body` pypdf+httpx）。
2026-10-27 深挖两处真根因并修复：
1. **`BodyFetcher` 签名错位**（管线按 item 调用、实现按 url）——摄取期正文**从未成功过**，
   历史「上游瞬时失败」实为此 bug；修后摄取期即带正文（a8a0701）。
2. **`no_text` 三分类**（本版）：扫描/图片型 PDF 下载成功但零文本层——静默重试会让这类
   永久失败项按「最新优先」永远占住回填名额，真正可取的老条目轮不到。`no_text` 显式落库、
   不再重试（待 OCR 能力接入）；`failed` 保留给可重试类（404/网络/解析异常）。
