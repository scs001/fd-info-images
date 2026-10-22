"""工具面双向断言（离线，不依赖 fastmcp）。

工具面台账与实现必须一一对应：台账多写的工具是幻觉，实现多出的工具是漏登。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "fd_info" / "server.py"
SURFACE = ROOT / "tool-surface.json"


def implemented_tools() -> set[str]:
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            if isinstance(deco, ast.Attribute) and deco.attr == "tool":
                names.add(node.name)
    return names


def declared_tools() -> set[str]:
    data = json.loads(SURFACE.read_text(encoding="utf-8"))
    return set(data["tool_annotations"])


def main() -> int:
    declared, implemented = declared_tools(), implemented_tools()
    problems = []
    if missing := declared - implemented:
        problems.append(f"台账有、实现无：{sorted(missing)}")
    if extra := implemented - declared:
        problems.append(f"实现有、台账无：{sorted(extra)}")
    if problems:
        print("FAIL: " + "; ".join(problems))
        return 1
    print(f"OK: {len(declared)} tools 双向一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
