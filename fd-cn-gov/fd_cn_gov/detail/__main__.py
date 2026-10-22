"""`fd_cn_gov.detail` 命令入口：抓详情 -> 解析 -> 落盘。

    # 只看列表产物规范化结果（不抓详情、不落盘）
    python -m fd_cn_gov.detail list --script mnr_tzgg_archive --max-pages 2

    # 抓详情并落盘（需 FDTEXT_S3_* 与 fd-text-core）
    python -m fd_cn_gov.detail crawl --script mnr_tzgg_archive --max-items 20

设计说明：
  · 列表侧调用**既有** 11 个脚本（subprocess 取 stdout），不改它们
  · 详情侧走 `fd_cn_gov.detail.pipeline`（抓取 + 复用引擎垂域解析器 + 落盘）
  · 落盘默认 --dry-run（只统计不写桶），避免误触生产桶
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

from .pipeline import ParserUnavailable, run_detail_crawl
from .sources import SOURCE_BY_SCRIPT, load_list_products
from .storage import root_summary


def _run_list_script(script: str, max_pages: int | None, all_pages: bool) -> str:
    """跑既有列表脚本并取其 stdout（它们的契约就是 stdout 的 JSON 数组）。"""
    argv = [sys.executable, "-m", f"fd_cn_gov.scripts.{script}"]
    if all_pages:
        argv.append("--all")
    elif max_pages is not None:
        argv += ["--max-pages", str(max_pages)]
    proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"列表脚本 {script} 退出码 {proc.returncode}；stderr 末尾：\n{proc.stderr[-800:]}"
        )
    return proc.stdout


def _make_s3() -> tuple[Any, str]:
    """按引擎同款环境变量构造 S3 客户端（不引入新配置面）。"""
    import boto3
    from botocore.config import Config

    endpoint = os.environ.get("FDTEXT_S3_ENDPOINT", "").strip()
    access = os.environ.get("FDTEXT_S3_ACCESS_KEY", "").strip()
    secret = os.environ.get("FDTEXT_S3_SECRET_KEY", "").strip()
    bucket = os.environ.get("FDTEXT_S3_BUCKET", "fd-text").strip()
    missing = [k for k, v in (
        ("FDTEXT_S3_ENDPOINT", endpoint),
        ("FDTEXT_S3_ACCESS_KEY", access),
        ("FDTEXT_S3_SECRET_KEY", secret),
    ) if not v]
    if missing:
        raise SystemExit(f"缺环境变量：{', '.join(missing)}（落盘需要对象存储凭据）")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        config=Config(connect_timeout=6, read_timeout=30, signature_version="s3v4"),
        region_name="us-east-1",
    )
    return client, bucket


def _load_state(s3: Any, bucket: str, domain: str) -> str | None:
    from .storage import state_key

    try:
        return s3.get_object(Bucket=bucket, Key=state_key(domain))["Body"].read().decode("utf-8")
    except Exception:
        # 首次运行（对象不存在）与读取故障在此合并：都退化为空状态。
        # 退化为空状态只意味着本轮可能多抓一些，不会造出错误数据。
        return None


def cmd_list(args: argparse.Namespace) -> int:
    raw = _run_list_script(args.script, args.max_pages, args.all)
    entries = load_list_products(raw, args.script)
    print(f"# {args.script}: 列表 {len(entries)} 条（规范化后）", file=sys.stderr)
    for e in entries[: args.show]:
        print(json.dumps(e.to_dict(), ensure_ascii=False))
    if len(entries) > args.show:
        print(f"# … 另有 {len(entries) - args.show} 条（--show 调整显示条数）", file=sys.stderr)
    return 0


def cmd_crawl(args: argparse.Namespace) -> int:
    raw = _run_list_script(args.script, args.max_pages, args.all)
    entries = load_list_products(raw, args.script)
    print(f"# 列表 {len(entries)} 条 -> 目标上限 {args.max_items or '不限'}", file=sys.stderr)

    if args.dry_run:
        print(f"# --dry-run：不抓详情、不落盘。键布局：{json.dumps(root_summary(args.domain), ensure_ascii=False)}",
              file=sys.stderr)
        return 0

    s3, bucket = _make_s3()
    state_raw = _load_state(s3, bucket, args.domain)
    try:
        stats = run_detail_crawl(
            entries,
            s3=s3,
            bucket=bucket,
            domain=args.domain,
            mode=args.mode,
            max_items=args.max_items,
            part_size=args.part_size,
            state_raw=state_raw,
        )
    except ParserUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(stats.as_dict(), ensure_ascii=False))
    if stats.failures:
        print(f"# 失败 {len(stats.failures)} 条（前 5 条）：", file=sys.stderr)
        for f in stats.failures[:5]:
            print(f"#   [{f['stage']}] {f['reason']} {f['url']}", file=sys.stderr)
    return 0 if stats.failed == 0 and stats.parse_failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m fd_cn_gov.detail",
        description="公文详情抓取与落盘（复用既有 11 个列表脚本 + 引擎垂域解析器）。",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--script", required=True, choices=sorted(SOURCE_BY_SCRIPT),
                        help="既有列表脚本名（11 源之一）")
    pg = common.add_mutually_exclusive_group()
    pg.add_argument("--max-pages", type=int, default=None, help="列表页数上限（默认=脚本自身默认）")
    pg.add_argument("--all", action="store_true", help="列表全量翻页")

    p_list = sub.add_parser("list", parents=[common], help="只看列表规范化结果（不抓详情）")
    p_list.add_argument("--show", type=int, default=5, help="显示前 N 条（默认 5）")
    p_list.set_defaults(func=cmd_list)

    p_crawl = sub.add_parser("crawl", parents=[common], help="抓详情并落盘")
    p_crawl.add_argument("--domain", default="gov", help="落盘域前缀（默认 gov）")
    p_crawl.add_argument("--max-items", type=int, default=0, help="本轮最多抓几条（0=不限）")
    p_crawl.add_argument("--part-size", type=int, default=200, help="每个 JSONL 分片的条数")
    p_crawl.add_argument("--mode", choices=("incremental", "full"), default="incremental",
                         help="incremental=按哈希水位跳过未变；full=全部重抓")
    p_crawl.add_argument("--dry-run", action="store_true", help="不抓取、不落盘，只打印键布局")
    p_crawl.set_defaults(func=cmd_crawl)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
