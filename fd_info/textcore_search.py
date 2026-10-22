"""text-core 混合检索客户端（MCP streamable-http）。

只在配置了 ``FD_TEXT_CORE_URL`` 时启用；握手失败/超时返回空并标注降级，
由 :class:`InfoService` 决定是否回落到索引检索。测试注入 fake transport。
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


class TextCoreSearch:
    def __init__(
        self,
        url: str,
        *,
        tool: str = "news_search",
        token: str | None = None,
        transport: Callable[[dict, dict], dict] | None = None,
    ) -> None:
        self.url = url
        self.tool = tool
        self.token = token
        self._transport = transport
        self._rpc_id = 0

    def __call__(self, query: str, limit: int) -> list[dict[str, Any]]:
        if self._transport is not None:
            payload = self._transport(
                {"name": self.tool, "arguments": {"query": query, "limit": limit}}, {}
            )
            return _rows(payload)
        try:
            return self._http_search(query, limit)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return []

    def _http_search(self, query: str, limit: int) -> list[dict[str, Any]]:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            session = self._initialize(client)
            self._rpc_id += 1
            resp = client.post(
                self.url,
                headers=self._headers(session),
                json={
                    "jsonrpc": "2.0",
                    "id": self._rpc_id,
                    "method": "tools/call",
                    "params": {
                        "name": self.tool,
                        "arguments": {"query": query, "limit": limit},
                    },
                },
            )
            resp.raise_for_status()
            payload = _decode(resp)
            if "error" in payload:
                raise ValueError(str(payload["error"]))
            return _rows(payload)

    def _initialize(self, client: httpx.Client) -> str:
        for version in PROTOCOL_VERSIONS:
            self._rpc_id += 1
            resp = client.post(
                self.url,
                headers=self._headers(),
                json={
                    "jsonrpc": "2.0",
                    "id": self._rpc_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": version,
                        "capabilities": {},
                        "clientInfo": {"name": "fd-info", "version": "0.1"},
                    },
                },
            )
            if resp.status_code == 400 and "protocol" in resp.text.lower():
                continue
            resp.raise_for_status()
            payload = _decode(resp)
            if "error" in payload:
                raise ValueError(str(payload["error"]))
            session = resp.headers.get("mcp-session-id")
            if not session:
                raise ValueError("initialize 未返回 Mcp-Session-Id")
            client.post(
                self.url,
                headers=self._headers(session),
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            return session
        raise ValueError("所有协议版本都被拒绝")

    def _headers(self, session: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if session:
            headers["Mcp-Session-Id"] = session
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


def _decode(resp: httpx.Response) -> dict[str, Any]:
    text = resp.text.strip()
    if text.startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise ValueError("响应既不是 JSON 也不是 SSE")


def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result") or payload
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        rows = structured.get("results") or structured.get("items") or []
        return [r for r in rows if isinstance(r, dict)]
    content = result.get("content") or []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                data = json.loads(block.get("text") or "")
            except json.JSONDecodeError:
                continue
            rows = data.get("results") or data.get("items") or []
            return [r for r in rows if isinstance(r, dict)]
    return []
