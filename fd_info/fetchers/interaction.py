"""交易所互动问答 fetcher：SSE（上证e互动）实装，SZSE（互动易）维持不可用。

上游与口径（2026-10 实调；fd-cn-report 旧的 queryApi.do 已 404 作废）：

- SSE 上证e互动 ``sns.sseinfo.com``（HTTP）：
  1. 公司 uid 解析：GET ``/company.do?stockcode=<6位代码>``，页内 ``uid=(\\d+)``；
  2. 公司问答流：GET ``/ajax/userfeeds.do?type=10&pageSize=<n>&uid=<uid>&page=<n>``
     （需 UA=Mozilla/5.0、X-Requested-With、Referer 指回 company.do），HTML 片段；
  3. 全局流（未配置 stockcodes 时的默认通道）：GET
     ``/ajax/feeds.do?type=10&show=1&pageSize=<n>&lastid=-1&page=<n>``，
     片段内含「公司名(600188)」。
- SZSE 互动易不可用：irm.cninfo.com.cn 旧 API POST 一律 405、GET 落 SPA HTML、
  newircs 404 —— 不实现抓取，作为 missing 如实上报（:data:`SZSE_UNAVAILABLE`）。

字段口径：

- ``source_id``：平台条目数字 id（HTML 块 ``id="item-<数字>"`` 中的数字）。
  问答对（问题+公司回复）合成一条 InfoItem。
- 问题正文：以 ``id="m_feed_txt-<同号>"`` 容器为准（2026-10 实调：该 id 与外层
  ``item-<id>`` 同号）；容器内的用户/公司锚文本（如 ``:中国平安(601318)``）剔除，
  收藏/评论/点赞操作区与「请登录后再点赞！」等提示文本绝不入 title/body。
- ``published_at``：块内文档序首个可解析时间（真实流里时间在条目尾部
  ``m_feed_from`` 区，形如「昨天 09:12」「2026年10月19日 08:40」，也可能无类名）；
  绝对日期按当日 00:00 UTC，带时刻的按上海时区折算 UTC，
  「今天/昨天/前天 HH:MM」同上；解析不出时间的条目丢弃。
- ``entities``：公司代码。公司通道用配置的 stockcode，全局通道从
  「公司名(600188)」文本提取。
- ``body``：问+答全文（``问：…\\n答：…``）。真实流中的 ``m_feed_answer`` 是
  回答输入表单（选择答复嘉宾/发布按钮），属噪声，不当答复。

HTTP 一律 ``trust_env=False``：本机/生产环境的系统代理（127.0.0.1:7892）
会劫持请求，显式关闭。``transport`` 可注入，测试用固定 HTML 片段，绝不真打网。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Callable

import httpx

from ..models import InfoItem, SourceResult
from .base import FetchWindow, item_from_row

SOURCE = "sse-interaction"
SSE_BASE = "http://sns.sseinfo.com"
PAGE_SIZE = 30
USER_AGENT = "Mozilla/5.0"
SH_TZ = timezone(timedelta(hours=8))

#: SZSE 侧不可用的如实交代（1.4.1 实调结论；恢复后替换为真实抓取）
SZSE_UNAVAILABLE = (
    "szse: 深交所互动易上游不可用（irm.cninfo.com.cn 旧 API POST 一律 405、"
    "GET 返回 SPA 页面、newircs 端点 404；2026-10 实调），未实现抓取"
)

Transport = Callable[[str, dict[str, str]], str]

# ---------------------------------------------------------------- HTML 解析

_ITEM_ID_RE = re.compile(r"^item-(\d+)$")
_COMPANY_CODE_RE = re.compile(r"([\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9*·]{0,19})\((\d{6})\)")
# 中文日期「2026年10月19日 08:40」是真实流的主要时间格式，先规范化成横线式
_CN_DATE_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
# 「纯时间」文本：整段就是一个时间，才认作条目时间（避免把正文里的日期当发布时间）
_ABS_TIME_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?")
_REL_TIME_RE = re.compile(r"(今天|昨天|前天)\s*(?:(\d{1,2}):(\d{2}))?")
_PURE_TIME_RE = re.compile(
    r"^(?:(?:\d{4})-(?:\d{1,2})-(?:\d{1,2})(?:\s+\d{1,2}:\d{2})?|"
    r"(?:今天|昨天|前天)\s*\d{0,2}:?\d{0,2})$"
)
_LABELS = frozenset({"问", "答", "提问", "回复"})
# 结构类名一律「整词」匹配：子串匹配曾把 feed_quote（登录提示区）误判成
# 问题容器（feed_q 是它的子串），导致 title 变成「◆ ◆ 请登录后再点赞！」。
_QUESTION_CLASSES = frozenset({"m_feed_txt", "m_feed_q", "m_feed_question", "q_title", "question"})
_ANSWER_CLASSES = frozenset({"m_feed_a", "m_answer", "feed_answer", "answer"})
# 噪声区（类名整词）：头像/昵称、收藏评论操作区、时间来源区（时间另行扫描）、
# 登录提示区、回答输入表单（m_feed_answer 是表单，不是真实答复）等。
_NOISE_CLASSES = frozenset({
    "m_feed_face", "m_feed_func", "m_feed_handle", "m_feed_from", "m_feed_info",
    "m_feed_type", "m_feed_comments", "m_feed_media", "m_feed_share",
    "answer_feed", "m_feed_answer", "feed_quote", "q_tit", "q_tit2", "q_con", "q_btm",
    "ask_ico", "index_ico", "emotion", "m_txt",
})
_LOGIN_TIP_RE = re.compile(r"请登录后[^！!]*[！!]")
_NOISE_TEXT_TOKENS = (
    "◆", "|", "收藏", "评论", "点赞", "分享", "转发", "举报", "来自", "网站",
    "IOS", "Android", "微信", "APP", "表情", "发布", "选择答复嘉宾", "回答：", "回答:",
    "(字数仅限1000字以内)",
)


class _Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Any] = []  # _Node | str

    @property
    def classes(self) -> str:
        return self.attrs.get("class") or ""

    def texts(self):
        for child in self.children:
            if isinstance(child, str):
                yield child
            else:
                yield from child.texts()


class _TreeBuilder(HTMLParser):
    """标准库建树：只求文本序与结构类名，不追求完整 DOM。"""

    _VOID = frozenset({"br", "img", "hr", "input", "meta", "link"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs) -> None:
        node = _Node(tag, dict(attrs))
        self._stack[-1].children.append(node)
        if tag not in self._VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self._stack[-1].children.append(_Node(tag, dict(attrs)))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self._stack[-1].children.append(text)


def _walk(node: _Node):
    yield node
    for child in node.children:
        if not isinstance(child, str):
            yield from _walk(child)


@dataclass
class _FeedEntry:
    entry_id: str
    question: str = ""
    answer: str = ""
    times: tuple[str, ...] = field(default_factory=tuple)  # 文档序的「纯时间」文本
    codes: tuple[str, ...] = field(default_factory=tuple)  # 「公司名(600188)」提取的代码


def parse_feed(html: str) -> list[_FeedEntry]:
    """从 userfeeds/feeds 片段解析问答条目（宽容解析，坏块跳过不抛）。"""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    entries: list[_FeedEntry] = []
    consumed: set[int] = set()
    for node in _walk(builder.root):
        if id(node) in consumed:
            continue
        if node.tag != "div" or "m_feed_item" not in node.classes.split():
            continue
        consumed.update(id(inner) for inner in _walk(node))
        match = _ITEM_ID_RE.match(node.attrs.get("id") or "")
        if match:
            entries.append(_entry_from_block(node, match.group(1)))
    return entries


def _normalize_cn_date(text: str) -> str:
    """「2026年10月19日」→「2026-10-19」，时间解析统一走横线式。"""
    return _CN_DATE_RE.sub(
        lambda m: f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}", text,
    )


def _is_pure_time(text: str) -> bool:
    return bool(_PURE_TIME_RE.match(_normalize_cn_date(text)))


def _is_noise_text(text: str) -> bool:
    """整段仅由已知 UI 文案组成（含「请登录后再…！」句式）即噪声。"""
    remainder = _LOGIN_TIP_RE.sub("", text)
    for token in _NOISE_TEXT_TOKENS:
        remainder = remainder.replace(token, "")
    return not remainder.strip()


def _find_by_id(node: _Node, target_id: str) -> _Node | None:
    for inner in _walk(node):
        if inner.attrs.get("id") == target_id:
            return inner
    return None


def _first_structural(block: _Node, classes: frozenset[str]) -> _Node | None:
    """类名「整词」命中即视为结构容器（子串匹配会误中 feed_quote 等）。"""
    for node in _walk(block):
        if node is block or node.tag != "div":
            continue
        if classes & set(node.classes.split()):
            return node
    return None


def _body_text(node: _Node) -> str:
    """容器内正文：剔除锚文本（用户/公司名）、噪声区与纯 UI 文案。"""
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            text = child
        elif child.tag == "a":
            continue
        elif _NOISE_CLASSES & set(child.classes.split()):
            continue
        else:
            text = _body_text(child)
        if text and text not in _LABELS and not _is_noise_text(text):
            parts.append(text)
    return " ".join(parts).strip()


def _noise_region_texts(block: _Node) -> set[str]:
    texts: set[str] = set()
    for node in _walk(block):
        if _NOISE_CLASSES & set(node.classes.split()):
            texts.update(node.texts())
    return texts


def _entry_from_block(block: _Node, entry_id: str) -> _FeedEntry:
    # 问题正文：优先配对的 m_feed_txt-<id> 容器（id 与外层 item-<id> 同号）
    q_container = _find_by_id(block, f"m_feed_txt-{entry_id}")
    if q_container is None:
        q_container = _first_structural(block, _QUESTION_CLASSES)
    a_node = _first_structural(block, _ANSWER_CLASSES)
    all_texts = [t for t in block.texts() if t not in _LABELS]
    # 时间：优先带 time 类名的节点，其次任何「纯时间」文本（真实流的时间
    # 在条目尾部 m_feed_from 区、无类名，纯时间扫描必须覆盖到）
    classed = [
        t for node in _walk(block) if "time" in node.classes.lower()
        for t in node.texts()
    ]
    times = [t for t in classed if _is_pure_time(t)] or [
        t for t in all_texts if _is_pure_time(t)
    ]
    codes: list[str] = []
    for text in block.texts():
        for _, code in _COMPANY_CODE_RE.findall(text):
            if code not in codes:
                codes.append(code)
    question = _body_text(q_container).lstrip("：:　 ").strip() if q_container else ""
    answer = _body_text(a_node) if a_node else ""
    if not question or not answer:
        excluded = _noise_region_texts(block)
        for node in (q_container, a_node):
            if node is not None:
                excluded.update(node.texts())
        candidates = [
            t for t in all_texts
            if t not in excluded
            and not _is_pure_time(t)
            and not _COMPANY_CODE_RE.fullmatch(t)
            and not _is_noise_text(t)
        ]
        if not question and candidates:
            question = candidates[0].lstrip("：:　 ").strip()
            candidates = candidates[1:]
        if not answer and candidates:
            answer = "\n".join(candidates)
    return _FeedEntry(
        entry_id=entry_id,
        question=question,
        answer=answer,
        times=tuple(times),
        codes=tuple(codes),
    )


def _parse_time(text: str, now: datetime) -> str | None:
    """「2026-10-20 [09:30]」「2026年10月19日 [08:40]」「今天|昨天|前天 [HH:MM]」
    → ISO（UTC 的 Z 或当日日期）。"""
    text = _normalize_cn_date((text or "").strip())
    match = _ABS_TIME_RE.search(text)
    if match:
        year, month, day = int(match[1]), int(match[2]), int(match[3])
        if match[4] is not None:
            dt = datetime(year, month, day, int(match[4]), int(match[5]), tzinfo=SH_TZ)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"{year:04d}-{month:02d}-{day:02d}"  # 沿用 parse_list_date 的当日 00:00 UTC 口径
    match = _REL_TIME_RE.search(text)
    if match:
        shift = {"今天": 0, "昨天": -1, "前天": -2}[match[1]]
        base = (now.astimezone(SH_TZ) + timedelta(days=shift)).replace(
            hour=int(match[2] or 0), minute=int(match[3] or 0), second=0, microsecond=0,
        )
        return base.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _summarize(question: str) -> str:
    return " ".join(question.split())[:80]


# ---------------------------------------------------------------- fetcher


class InteractionSource:
    name = SOURCE

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        exchanges: tuple[str, ...] = ("sse", "szse"),
        stockcodes: tuple[str, ...] = (),
        page_size: int = PAGE_SIZE,
        max_pages: int = 1,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """``stockcodes`` 非空走公司问答流（uid 解析 + userfeeds），为空走全局流。"""
        self._transport = transport or self._http
        self.exchanges = tuple(exchanges)
        self.stockcodes = tuple(stockcodes)
        self.page_size = page_size
        self.max_pages = max_pages
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._uid_cache: dict[str, str] = {}

    def fetch(self, since: str, until: str) -> SourceResult:
        window = FetchWindow(since, until)
        items: list[InfoItem] = []
        missing: list[str] = []
        unsupported: list[str] = []
        ran = 0
        sse_failed = False
        for exchange in self.exchanges:
            if exchange == "sse":
                ran += 1
                got, missed = self._fetch_sse(window)
                items.extend(got)
                if missed:
                    sse_failed = True
                    missing.extend(missed)
            elif exchange == "szse":
                # 能力边界（上游 405/SPA 化），不是运行失败：不让整源变 unavailable。
                unsupported.append(SZSE_UNAVAILABLE)
            else:
                unsupported.append(f"{exchange}: 未支持的交易所")
        if ran == 0 or (sse_failed and not items):
            return SourceResult(
                SOURCE, "unavailable", reason="; ".join(missing or unsupported)
            )
        if missing or unsupported:
            return SourceResult(
                SOURCE, "partial", items=tuple(items), reason="部分交易所不可用或未实现",
                missing=tuple(missing + unsupported), window_checked=True,
            )
        return SourceResult(SOURCE, "ok", items=tuple(items), window_checked=True)

    # -- SSE ---------------------------------------------------------------

    def _fetch_sse(self, window: FetchWindow) -> tuple[list[InfoItem], list[str]]:
        items: list[InfoItem] = []
        missing: list[str] = []
        if self.stockcodes:
            for code in self.stockcodes:
                try:
                    items.extend(self._company_feed(code, window))
                except Exception as exc:  # noqa: BLE001 - 单只失败是一条数据
                    missing.append(f"sse/{code}: {type(exc).__name__}: {exc}")
        else:
            try:
                items.extend(self._global_feed(window))
            except Exception as exc:  # noqa: BLE001
                missing.append(f"sse: {type(exc).__name__}: {exc}")
        return items, missing

    def _company_feed(self, stockcode: str, window: FetchWindow) -> list[InfoItem]:
        uid = self._resolve_uid(stockcode)
        referer = f"{SSE_BASE}/company.do?stockcode={stockcode}"
        url_template = (
            f"{SSE_BASE}/ajax/userfeeds.do?type=10&pageSize={self.page_size}"
            f"&uid={uid}&page={{page}}"
        )
        return self._paged(
            url_template, referer, window, entities=(stockcode,), url=referer,
        )

    def _global_feed(self, window: FetchWindow) -> list[InfoItem]:
        url_template = (
            f"{SSE_BASE}/ajax/feeds.do?type=10&show=1&pageSize={self.page_size}"
            f"&lastid=-1&page={{page}}"
        )
        return self._paged(url_template, f"{SSE_BASE}/", window, entities=(), url=None)

    def _paged(
        self,
        url_template: str,
        referer: str,
        window: FetchWindow,
        *,
        entities: tuple[str, ...],
        url: str | None,
    ) -> list[InfoItem]:
        headers = {
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
        }
        out: list[InfoItem] = []
        for page in range(1, self.max_pages + 1):
            entries = parse_feed(self._transport(url_template.format(page=page), headers))
            if not entries:
                break
            out.extend(self._to_items(entries, window, entities=entities, url=url))
            if len(entries) < self.page_size:
                break
        return out

    def _resolve_uid(self, stockcode: str) -> str:
        if stockcode in self._uid_cache:
            return self._uid_cache[stockcode]
        html = self._transport(
            f"{SSE_BASE}/company.do?stockcode={stockcode}",
            {"User-Agent": USER_AGENT, "Referer": f"{SSE_BASE}/"},
        )
        match = re.search(r"uid=(\d+)", html)
        if not match:
            raise ValueError(f"company.do 未解析到 uid（stockcode={stockcode}）")
        self._uid_cache[stockcode] = match.group(1)
        return match.group(1)

    def _to_items(
        self,
        entries: list[_FeedEntry],
        window: FetchWindow,
        *,
        entities: tuple[str, ...],
        url: str | None,
    ) -> list[InfoItem]:
        now = self._clock()
        out: list[InfoItem] = []
        for entry in entries:
            published = next(
                (parsed for raw in entry.times if (parsed := _parse_time(raw, now))), None,
            )
            if published is None:
                continue  # 时间解析不出 → 丢弃该条，不编造发布时间
            if published[:10] < window.since or published[:10] > window.until:
                continue
            question = " ".join(entry.question.split())
            answer = " ".join(entry.answer.split())
            if not question:
                continue
            body = f"问：{question}\n答：{answer}" if answer else f"问：{question}"
            out.append(
                item_from_row(
                    source=SOURCE,
                    source_id=entry.entry_id,
                    source_type="interaction",
                    published=published,
                    title=_summarize(question),
                    summary=question,
                    body=body,
                    url=url,
                    entities=tuple(entry.codes) or entities,
                )
            )
        return out

    @staticmethod
    def _http(url: str, headers: dict[str, str]) -> str:
        # trust_env=False：系统代理（如本机 127.0.0.1:7892）会劫持请求，显式关闭。
        with httpx.Client(timeout=30.0, trust_env=False, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.text
