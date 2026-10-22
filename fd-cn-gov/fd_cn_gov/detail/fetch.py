"""详情页抓取：礼貌抓取 + 重试 + **失败如实留痕**。

与 `fd_cn_gov/scripts/` 下 11 个列表脚本的关系：那些脚本抓列表页并 print JSON 到
stdout（服务柏讯线的指标目录，输出契约不可变）；本模块抓**详情页**并落盘，
是纯增量能力，两者不共享代码也不互相调用。

抓取纪律（对 .gov.cn 站点，实测行为见 design D5）：
  · 跟随重定向 —— 实测 http://gi.mnr.gov.cn/... 会 302 到 https://
  · 显式礼貌 UA —— 标识用途，便于站方识别与联系
  · 限速 —— 默认每请求间隔 0.5s（比列表脚本的 0.3s 更保守：详情页更重）
  · 重试 —— 网络异常与 5xx 重试（指数退避）；**404 不重试**（确定性缺失，重试无意义）
  · 失败**必须**记入 state.failures 并计数——静默跳过是这类管线最危险的失效模式
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urljoin

# 礼貌 UA：标识项目与用途（部分站点对无 UA 请求直接拒绝）
USER_AGENT = (
    "fd-cn-gov-corpus/0.1 (+https://github.com/FindDataTechnology/fd-cn-gov; "
    "public-information corpus indexing)"
)

# 可重试的状态码（服务端暂时性问题）。404 明确不在内。
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass
class FetchResult:
    """一次抓取的结果。失败时 body 为空且 error 非空——不返回半成品。"""

    url: str
    final_url: str = ""
    status: int = 0
    body: str = ""
    error: str = ""
    attempts: int = 0
    retryable: bool = False
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.body) and not self.error


@dataclass
class FetchPolicy:
    """抓取策略（可覆盖，便于测试与不同站点的差异化限速）。"""

    delay: float = 0.5  # 每请求之间的最小间隔（秒）
    timeout: float = 25.0
    max_attempts: int = 3
    backoff: float = 1.5  # 退避基数：第 n 次重试前等 backoff**n 秒
    user_agent: str = USER_AGENT
    _last_at: float = field(default=0.0, repr=False)

    def wait_turn(self) -> None:
        """全局最小间隔（单线程顺序抓取时的节流点）。"""
        if self.delay <= 0:
            return
        gap = time.monotonic() - self._last_at
        if gap < self.delay:
            time.sleep(self.delay - gap)
        self._last_at = time.monotonic()


def fetch_detail(url: str, policy: FetchPolicy | None = None, *, fetcher=None) -> FetchResult:
    """抓一个详情页。返回结果**永不抛异常**（失败是数据，不是控制流）。

    为什么吞异常：调用方要把失败逐条写进 state.failures 并计数；
    抛异常会让「一条失败」变成「整批中断」，丢掉其余条目的抓取机会。
    """
    pol = policy or FetchPolicy()
    if fetcher is None:
        from scrapling.fetchers import Fetcher

        fetcher = Fetcher()

    res = FetchResult(url=url)
    started = time.monotonic()
    last_error = ""
    for attempt in range(1, pol.max_attempts + 1):
        res.attempts = attempt
        if attempt > 1:
            time.sleep(pol.backoff ** attempt)
        pol.wait_turn()
        try:
            resp = fetcher.get(
                url,
                follow_redirects=True,
                headers={"User-Agent": pol.user_agent},
                stealthy_headers=False,
                timeout=pol.timeout,
            )
        except Exception as exc:  # 网络层异常（超时/连接重置/TLS）
            last_error = f"{type(exc).__name__}: {exc}"
            res.retryable = True
            continue

        status = int(getattr(resp, "status", 0) or 0)
        body = getattr(resp, "html_content", None) or ""
        if isinstance(body, bytes):
            body = body.decode("utf-8", "replace")
        res.status = status
        res.final_url = str(getattr(resp, "url", "") or url)

        if status == 200:
            if not body:
                # 200 但空体：站点的软失败（维护页/拦截页）。如实记为失败。
                last_error = "HTTP 200 但响应体为空"
                res.retryable = True
                continue
            res.body = body
            res.error = ""
            res.retryable = False
            res.elapsed = time.monotonic() - started
            return res

        last_error = f"HTTP {status}"
        if status not in RETRYABLE_STATUS:
            # 确定性失败（404/403 等）：重试不会改变结果，立即收手并如实记录
            res.retryable = False
            res.error = last_error
            res.elapsed = time.monotonic() - started
            return res
        res.retryable = True

    res.error = last_error or "未知错误"
    res.elapsed = time.monotonic() - started
    return res


def absolutize(url: str, base_url: str) -> str:
    """相对链接转绝对（解析器拿到 HTML 片段不知道 base，故在采集侧补全）。

    实测自然资源部附件是 `./P0202606306...docx` 形态的相对链接。
    """
    if not url:
        return ""
    if url.startswith(("http://", "https://", "//")):
        return url
    return urljoin(base_url, url)
