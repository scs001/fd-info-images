#!/usr/bin/env python3
"""fd-cn-gov CLI: crawl, list, describe, build-registry.

Usage:
  fd-cn-gov crawl <name> [--max-pages N | --all]   # run a scraper
  fd-cn-gov list                                     # list registered sources
  fd-cn-gov describe <name>                          # show a source + its columns
  fd-cn-gov build-registry                           # regenerate registry.db/json
"""
from __future__ import annotations

import argparse
import importlib
import sys
from typing import Optional

from fd_cn_gov.registry import list_sources, get_source, get_columns

SCRIPT_NAMES = [
    "mee_gsgg_archive", "mem_tzgg_archive", "mnr_tzgg_archive",
    "moa_govpublic_archive", "mof_gkml_archive", "mofcom_xwfb_archive",
    "mohurd_xinwen_archive", "mot_shuju_archive", "ndrc_tzgg_archive",
    "pbc_xinwen_archive", "safe_whxw_archive",
]


def _cmd_crawl(args) -> int:
    name = args.name
    if name not in SCRIPT_NAMES:
        print(
            f"error: unknown scraper {name!r}\navailable: {' '.join(SCRIPT_NAMES)}",
            file=sys.stderr,
        )
        return 2
    # Forward the page-cap flags to the script's argparse via synthesized argv.
    script_argv = [name]
    if args.all:
        script_argv.append("--all")
    elif args.max_pages is not None:
        script_argv += ["--max-pages", str(args.max_pages)]
    mod = importlib.import_module(f"fd_cn_gov.scripts.{name}")
    old = sys.argv
    sys.argv = script_argv
    try:
        mod.main()
    finally:
        sys.argv = old
    return 0


def _cmd_list(args) -> int:
    for s in list_sources():
        print(f"{s.name}\t{s.label}\t{s.url}")
    return 0


def _cmd_describe(args) -> int:
    try:
        s = get_source(args.name)
    except KeyError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"name:        {s.name}")
    print(f"label:       {s.label}")
    print(f"url:         {s.url}")
    print(f"category:    {s.category}")
    print(f"description: {s.description}")
    print("columns:")
    for c in get_columns(args.name):
        pk = " [PK]" if c.primary_key else ""
        nn = "" if c.nullable else " NOT NULL"
        print(f"  - {c.name}: {c.type}{pk}{nn}  ({c.semantic_type or '-'})")
        if c.description:
            print(f"      {c.description}")
        if c.source_field:
            print(f"      source: {c.source_field}")
    return 0


def _cmd_build_registry(args) -> int:
    from fd_cn_gov.build_registry import build
    print(build())
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="fd-cn-gov",
        description="Chinese ministry open-information scrapers + registry.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_crawl = sub.add_parser("crawl", help="run a scraper; prints JSON records to stdout")
    p_crawl.add_argument("name", choices=None, help="scraper name (e.g. mof_gkml_archive)")
    pg = p_crawl.add_mutually_exclusive_group()
    pg.add_argument("--max-pages", type=int, help="pages per archive (default: script default, usually 50)")
    pg.add_argument("--all", action="store_true", help="full crawl, no page cap")
    p_crawl.set_defaults(func=_cmd_crawl)

    p_list = sub.add_parser("list", help="list registered sources")
    p_list.set_defaults(func=_cmd_list)

    p_desc = sub.add_parser("describe", help="show a source + its column schema")
    p_desc.add_argument("name", help="scraper name")
    p_desc.set_defaults(func=_cmd_describe)

    p_build = sub.add_parser("build-registry", help="regenerate registry.db + registry.json from MANIFESTs")
    p_build.set_defaults(func=_cmd_build_registry)
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
