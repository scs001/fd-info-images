from datetime import datetime, timezone

import httpx

from fd_info.fetchers.cninfo import CninfoSource
from fd_info.fetchers.ministry import MinistrySource
from fd_info.fetchers.interaction import InteractionSource
from fd_info.models import item_id_for
from fd_info.storage import Store


def cninfo_payload():
    return {
        "announcements": [
            {
                "announcementId": "1220000001",
                "announcementTitle": "关于 2026 年第三季度报告的公告",
                "announcementTime": 1729468800000,  # 2024-10-21
                "adjunctUrl": "finalpage/2026-10-21/1220000001.PDF",
            }
        ]
    }


def test_cninfo_replay_is_idempotent():
    src = CninfoSource(transport=lambda data: cninfo_payload(), columns=("sse",))
    first = src.fetch("2026-10-20", "2026-10-22")
    second = src.fetch("2026-10-20", "2026-10-22")
    assert first.status == second.status == "ok"
    assert len(first.items) == len(second.items) == 1
    assert first.items[0].item_id == second.items[0].item_id


def test_cninfo_partial_when_one_column_fails():
    def transport(data):
        if data["column"] == "szse":
            raise ValueError("boom")
        return cninfo_payload()

    src = CninfoSource(transport=transport, columns=("sse", "szse"))
    result = src.fetch("2026-10-20", "2026-10-22")
    assert result.status == "partial"
    assert result.missing and "szse" in result.missing[0]


def test_cninfo_unavailable_when_all_fail():
    def transport(data):
        raise ValueError("boom")

    result = CninfoSource(transport=transport, columns=("sse",)).fetch("2026-10-20", "2026-10-22")
    assert result.status == "unavailable"


class FakeManifest:
    def __init__(self):
        self.archives = [type("A", (), {"section": "通知", "subsection": "通知", "url": "http://x"})()]


class FakeModule:
    MANIFEST = FakeManifest()

    @staticmethod
    def crawl_archive(section, subsection, url, max_pages):
        return [
            {"title": "关于某政策的通知", "date": "2026-10-21", "url": "http://x/1.html"},
            {"title": "旧闻", "date": "2026-09-01", "url": "http://x/0.html"},
        ]


def test_ministry_filters_to_window():
    src = MinistrySource(scripts=("fake",), module_loader=lambda name: FakeModule())
    result = src.fetch("2026-10-20", "2026-10-22")
    assert result.status == "ok"
    assert [i.title for i in result.items] == ["关于某政策的通知"]
    assert result.items[0].source_type == "ministry"


def test_ministry_unavailable_without_package():
    def loader(name):
        raise ImportError("no fd_cn_gov")

    result = MinistrySource(scripts=("x",), module_loader=loader).fetch("2026-10-20", "2026-10-22")
    assert result.status == "unavailable"
    assert "no fd_cn_gov" in result.reason


# ---------------------------------------------------------------- SSE 互动
# 旧语义「默认 unavailable」已作废：SSE 侧实装后默认走真实 HTTP（离线测试一律
# 注入 transport），SZSE 侧维持 unavailable——见下方两个用例。


SSE_CLOCK = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)  # 上海时间 18:00


def sse_uid_page(uid="1318"):
    return f"<html><body>var company = new Company({uid}); company.init(uid={uid});</body></html>"


def sse_company_feed_html():
    """照 2026-10-23 实调抓包（sns.sseinfo.com userfeeds.do, uid=1318）裁剪的真实结构：
    问题在 m_feed_txt-<id> 容器（锚文本前缀），噪声在 m_feed_func/m_feed_type 区。"""
    return """
    <style>.feed_quote{margin-top: 25px;}</style>
    <div class="m_feed_item" style="display: flex">
        <input type="hidden" id="currentPage" value="1" />
    </div>
    <div class="m_feed_item m_question" id="item-1798148">
      <div class="m_feed_line"></div>
      <div class="m_feed_detail">
        <div class="m_feed_face">
          <a rel="face" href="user.do?uid=336926" title="投资者_1783306714104">
            <img title="投资者_1783306714104" src="avatar.png"></a>
          <p>投资者_1783306714104</p>
        </div>
        <div class="m_feed_cnt ">
          <div class="m_feed_info"><div class="ask_ico index_ico"></div></div>
          <div class="m_feed_txt" id="m_feed_txt-1798148"><a href='user.do?uid=1318' >:中国平安(601318)</a>董秘您好！欣喜看到公司业绩强劲复苏，但当前估值仍处低位。请问：

1. 公司是否有计划推出"注销式回购"，以实质性提振每股收益与市场信心？

2. 公司险资涉房风险是否已实质性出清？期待回复！
          </div>
          <div class="m_feed_func">
            <div class="m_feed_handle">
              <a href="javascript:love(1798148);" id="love-1798148"><em></em></a>
              <i class="m_txt">|</i>
              <a href="javascript:favorite(1798148);" id="favorite-1798148">收藏 </a>
              <i class="m_txt">|</i>
              <a href="javascript:comment(1798148);">评论<span id="totalNum_1798148"></span></a>
            </div>
            <div class="m_feed_from"><span>昨天 09:12</span><em>来自</em><a href="javascript:;">网站</a></div>
          </div>
          <div class="m_feed_type" id="love_list_1798148" style="display: none;">
            <div class="feed_quote">
              <div class="q_tit l_p"><em class="S_line1_c S_line1_c1">◆</em><span class="S_bg4_c S_bg4_c1">◆</span></div>
              <div class="q_con"><a href="javascript:pleaseLogin();">请登录后再点赞！</a></div>
            </div>
          </div>
          <div class="m_feed_type" id="favorite_list_1798148" style="display: none;">
            <div class="feed_quote">
              <div class="q_tit l_f"><em>◆</em><span>◆</span></div>
              <div class="q_con"><a href="javascript:pleaseLogin();">请登录后再收藏！</a></div>
            </div>
          </div>
          <div class="m_feed_comments" id="comment_list_1798148"></div>
        </div>
      </div>
    </div>
    <div class="m_feed_item m_question" id="item-1796939">
      <div class="m_feed_detail">
        <div class="m_feed_face"><p>投资者_1559185031000</p></div>
        <div class="m_feed_cnt ">
          <div class="m_feed_txt" id="m_feed_txt-1796939"><a href='user.do?uid=1318' >:中国平安(601318)</a>请问贵司还认为自己股价低股吗？今年以来证监会鼓励市场主动回购报答投资者，贵司是否考虑也进行一定回购注销？</div>
          <div class="m_feed_func">
            <div class="m_feed_from"><span>2026年10月19日 08:40</span><em>来自</em><a href="javascript:;">IOS</a></div>
          </div>
          <div class="m_feed_type" id="love_list_1796939" style="display: none;">
            <div class="feed_quote"><div class="q_con"><a href="javascript:pleaseLogin();">请登录后再点赞！</a></div></div>
          </div>
        </div>
      </div>
    </div>
    <div class="m_feed_item m_question" id="item-1794868">
      <div class="m_feed_detail">
        <div class="m_feed_txt" id="m_feed_txt-1794868"><a href='user.do?uid=1318' >:中国平安(601318)</a>董秘您好，请问企业当下的困境跟风险点是什么？</div>
        <div class="m_feed_func"><div class="m_feed_from"><span>2026年10月12日 08:57</span></div></div>
      </div>
    </div>
    <div class="m_feed_item m_question" id="item-1794999">
      <div class="m_feed_detail">
        <div class="m_feed_txt" id="m_feed_txt-1794999"><a href='user.do?uid=1318' >:中国平安(601318)</a>时间格式不认识的问题应整条丢弃。</div>
        <div class="m_feed_func"><div class="m_feed_from"><span>3天前</span></div></div>
      </div>
    </div>
    """


def sse_global_feed_html():
    """照实调抓包（feeds.do 全局流）：条目含回答输入表单（m_feed_answer）——是噪声不是答复。"""
    return """
    <div class="m_feed_item m_question" id="item-1798400">
      <div class="m_feed_detail">
        <div class="m_feed_face"><p>投资者_1774508974173</p></div>
        <div class="m_feed_cnt ">
          <div class="m_feed_info"><div class="ask_ico index_ico"></div></div>
          <div class="m_feed_txt" id="m_feed_txt-1798400"><a href='user.do?uid=188' >:兖矿能源(600188)</a>请问公司，未来内蒙古荣信二期建设完成后，有没有可能开工建设气化渣资源化项目？</div>
          <div class="m_feed_func clearfix">
            <div class="m_feed_handle">
              <a href="javascript:love(1798400);"><em></em></a>
              <i class="m_txt">|</i>
              <a href="javascript:favorite(1798400);">收藏</a>
            </div>
            <div class="m_feed_from"><span>昨天 17:51</span><em>来自</em><a href="javascript:;">Android</a></div>
          </div>
          <div class="m_feed_type" id="love_list_1798400" style="display: none;">
            <div class="feed_quote"><div class="q_tit l_p"><em>◆</em><span>◆</span></div>
            <div class="q_con"><a href="javascript:pleaseLogin();">请登录后再点赞！</a></div></div>
          </div>
          <div class="feed_quote feed_wb hidden answer_feed">
            <div class="q_tit2"><em>◆</em><span>◆</span></div>
            <div class="m_feed_answer " id="m_feed_answer_1798400">
              <span>选择答复嘉宾</span>
              <select id="member-1798400"></select>
              <span>(字数仅限1000字以内)</span>
              <textarea id="text-1798400">回答：</textarea>
              <span class="emotion">表情</span>
              <input type="button" value="发布" id="answer-1798400">
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="m_feed_item m_question" id="item-50002">
      <div class="m_feed_detail">
        <div class="m_feed_txt" id="m_feed_txt-50002"><a href='user.do?uid=273765' >:华丰科技(688629)</a>公司主营业务的订单数据，能否披露个大概？</div>
        <div class="m_feed_func"><div class="m_feed_from"><span class="v_time">2026-10-21 15:20</span></div></div>
      </div>
    </div>
    """


def sse_router(url, headers):
    """按真实端点族路由的假 transport：绝不真打网。"""
    if "company.do" in url and "stockcode=" in url:
        return sse_uid_page()
    if "userfeeds.do" in url:
        return sse_company_feed_html()
    if "feeds.do" in url:
        return sse_global_feed_html()
    raise AssertionError(f"意外请求：{url}")


def make_sse_source(**kw):
    kw.setdefault("exchanges", ("sse",))
    kw.setdefault("stockcodes", ("601318",))
    kw.setdefault("transport", sse_router)
    kw.setdefault("clock", lambda: SSE_CLOCK)
    return InteractionSource(**kw)


def test_sse_interaction_parses_company_feed():
    result = make_sse_source().fetch("2026-10-19", "2026-10-23")
    assert result.status == "ok" and result.window_checked
    assert len(result.items) == 2  # 窗口外（10-12）与时间解析不出（3天前）的条目被丢弃

    first, second = result.items
    assert first.source == "sse-interaction"
    assert first.source_type == "interaction"
    assert first.source_id == "1798148"
    assert first.item_id == item_id_for("sse-interaction", "1798148")
    # title 是 m_feed_txt-<id> 容器里的问题正文，绝不是操作区/提示区文本
    assert first.title.startswith("董秘您好！欣喜看到公司业绩强劲复苏")
    assert "注销式回购" in first.body and "险资涉房风险" in first.body
    assert first.body.startswith("问：董秘您好！")
    for noise in ("请登录", "◆", "收藏", "评论", "投资者_", "来自", "(601318)"):
        assert noise not in first.title and noise not in first.body, noise
    assert first.published_at == "2026-10-22T01:12:00Z"  # 昨天 09:12（上海）→ UTC
    assert first.entities == ("601318",)
    assert first.url == "http://sns.sseinfo.com/company.do?stockcode=601318"
    assert second.source_id == "1796939"
    assert second.published_at == "2026-10-19T00:40:00Z"  # 2026年10月19日 08:40（上海）→ UTC


def test_sse_interaction_replay_yields_single_item_id(tmp_path):
    src = make_sse_source()
    first = src.fetch("2026-10-19", "2026-10-23")
    second = src.fetch("2026-10-19", "2026-10-23")
    assert [i.item_id for i in first.items] == [i.item_id for i in second.items]

    store = Store(tmp_path / "info.db")
    try:
        flags = [store.upsert(item)[:2] for item in first.items]
        assert all(created for created, _ in flags)
        flags = [store.upsert(item)[:2] for item in second.items]
        assert flags == [(False, False), (False, False)]  # 重放零副作用
        assert len(store.list_since(sources=["sse-interaction"])["items"]) == 2
    finally:
        store.close()


def test_sse_interaction_global_feed_extracts_entities():
    src = make_sse_source(stockcodes=())  # 未配置股票代码 → 全局流
    result = src.fetch("2026-10-19", "2026-10-23")
    assert result.status == "ok"
    assert len(result.items) == 2

    first, second = result.items
    assert first.source_id == "1798400"
    assert first.entities == ("600188",)  # 从锚文本「兖矿能源(600188)」提取
    assert "气化渣" in first.body
    # m_feed_answer 是回答输入表单（选择答复嘉宾/回答：/表情/发布），绝不当答复
    for noise in ("选择答复嘉宾", "回答：", "表情", "发布", "请登录", "◆", "收藏"):
        assert noise not in first.title and noise not in first.body, noise
    assert first.url is None  # 不编造详情页链接
    assert second.source_id == "50002"
    assert second.entities == ("688629",)
    assert second.published_at == "2026-10-21T07:20:00Z"  # 横线式日期 15:20（上海）→ UTC


def test_szse_interaction_leg_is_unavailable():
    def no_network(url, headers):
        raise AssertionError("SZSE 分支不应触网")

    result = InteractionSource(exchanges=("szse",), transport=no_network).fetch(
        "2026-10-20", "2026-10-22"
    )
    assert result.status == "unavailable"
    assert "互动易" in result.reason and "405" in result.reason


def test_interaction_default_reports_szse_missing():
    src = InteractionSource(transport=sse_router, clock=lambda: SSE_CLOCK, stockcodes=())
    result = src.fetch("2026-10-19", "2026-10-23")
    assert result.status == "partial"  # 默认 (sse, szse)：SSE 可用 + SZSE 如实缺席
    assert [m.split(":", 1)[0] for m in result.missing] == ["szse"]
    assert len(result.items) == 2


def test_sse_interaction_unavailable_on_transport_error():
    def transport(url, headers):
        raise httpx.ConnectError("connection refused")

    result = make_sse_source(transport=transport).fetch("2026-10-20", "2026-10-22")
    assert result.status == "unavailable"
    assert "connection refused" in result.reason


def test_sse_interaction_maps_upstream_5xx_to_unavailable(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs.get("trust_env") is False  # 必须显式关闭系统代理

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, headers=None):
            return httpx.Response(503, request=httpx.Request("GET", url))

    monkeypatch.setattr("fd_info.fetchers.interaction.httpx.Client", FakeClient)
    result = InteractionSource(exchanges=("sse",)).fetch("2026-10-20", "2026-10-22")
    assert result.status == "unavailable"
    assert "503" in result.reason


class DetailOnlyModule:
    """有 MANIFEST、无 crawl_archive：能力边界，不是运行失败。"""

    MANIFEST = FakeManifest()


def test_ministry_unsupported_script_is_partial_not_unavailable():
    def loader(name):
        return FakeModule() if name == "ok" else DetailOnlyModule()

    src = MinistrySource(scripts=("ok", "detail-only"), module_loader=loader)
    result = src.fetch("2026-10-20", "2026-10-22")
    assert result.status == "partial"
    assert [i.title for i in result.items] == ["关于某政策的通知"]
    assert "detail-only" in result.missing


def test_ministry_all_unsupported_is_unavailable():
    src = MinistrySource(scripts=("a", "b"), module_loader=lambda n: DetailOnlyModule())
    result = src.fetch("2026-10-20", "2026-10-22")
    assert result.status == "unavailable"
    assert "a" in result.reason and "b" in result.reason
