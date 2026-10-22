"""MCP 入口：四个只读工具（spec: info-stream）。

用法：``python -m fd_info.server``（stdio）或 ``FD_INFO_DB=... fd-info serve``。
"""

from __future__ import annotations

import os

from . import __version__
from .body_store import SpoolBodyStore
from .service import InfoService
from .storage import Store


def build_service() -> InfoService:
    store = Store(os.environ.get("FD_INFO_DB", "./fd_info.db"))
    body_store = SpoolBodyStore(os.environ.get("FD_INFO_SPOOL", "./fd_info_spool"))
    url = os.environ.get("FD_TEXT_CORE_URL")
    hook = None
    if url:
        from .textcore_search import TextCoreSearch

        hook = TextCoreSearch(url, token=os.environ.get("FD_TEXT_CORE_TOKEN"))
    return InfoService(store, body_store, search_hook=hook)


def build_server():  # pragma: no cover - 传输层，测试不装载 fastmcp
    from fastmcp import FastMCP

    mcp = FastMCP("fd-info", version=__version__)
    service = build_service()

    @mcp.tool
    def list_since(
        cursor: str | None = None,
        sources: list[str] | None = None,
        source_types: list[str] | None = None,
        published_after: str | None = None,
        limit: int = 50,
    ) -> dict:
        """按发布时间倒序列出信息条目；cursor 来自上一页的 next_cursor。"""
        return service.list_since(
            cursor=cursor,
            sources=sources,
            source_types=source_types,
            published_after=published_after,
            limit=limit,
        )

    @mcp.tool
    def get_item(item_id: str) -> dict:
        """取条目详情（含 doc_id、正文状态、原文链接）。"""
        try:
            return service.get_item(item_id)
        except KeyError as exc:
            return {"error": str(exc)}

    @mcp.tool
    def search_info(
        query: str,
        sources: list[str] | None = None,
        source_types: list[str] | None = None,
        published_after: str | None = None,
        limit: int = 20,
    ) -> dict:
        """检索信息条目；返回 mode 字段说明走的是语义检索还是索引检索。"""
        return service.search_info(
            query,
            sources=sources,
            source_types=source_types,
            published_after=published_after,
            limit=limit,
        )

    @mcp.tool
    def locate_evidence(doc_id: str, start: int, end: int) -> dict:
        """按正文区间取逐字片段；正文缺失或区间越界返回结构化错误。"""
        return service.locate(doc_id, start, end)

    @mcp.tool
    def stream_health() -> dict:
        """各来源水位、最近成功时间、入库延迟 p95 与失败计数。"""
        return {"sources": service.health()}

    return mcp


def main() -> None:  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(prog="fd-info-server")
    parser.add_argument("--http", type=int, default=None, help="HTTP 端口（默认 stdio）")
    args = parser.parse_args()
    app = build_server()
    if args.http:
        _start_health_server(app, args.http + 1)
        app.run(transport="http", host="0.0.0.0", port=args.http)
    else:
        app.run()


def _start_health_server(app, port: int) -> None:  # pragma: no cover
    """k8s 探针专用 /health；工具面为空时 503，不拿 /mcp 当探针。"""
    import asyncio
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            tools = asyncio.run(app._list_tools())
            body = json.dumps({"status": "ok", "tools": len(tools)}).encode()
            self.send_response(200 if tools else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    threading.Thread(
        target=lambda: HTTPServer(("0.0.0.0", port), Handler).serve_forever(),
        daemon=True,
    ).start()


if __name__ == "__main__":  # pragma: no cover
    main()
