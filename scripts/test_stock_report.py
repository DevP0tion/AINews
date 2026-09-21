#!/usr/bin/env python3
"""주식 리포트 검증·중복·렌더링 self-check. 실행: python3 scripts/test_stock_report.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import stock_report  # noqa: E402
from stock_report import (  # noqa: E402
    WEEKLY_DAY, build_weekly, filter_new, group_by_stock, group_reports,
    consensus_change, is_weekly_day, load_histories, normalize_url,
    past_events, upcoming_events, validate_input, with_level_context,
)
from collect_research import (  # noqa: E402
    merge_reports, merge_trend, parse_reports, prune_reports, prune_trend,
    ticker_of as research_ticker, trend_snapshot,
)
from render_stock_page import (  # noqa: E402
    fmt_billion, fmt_change, fmt_consensus, fmt_daylabel, fmt_dday,
    fmt_level_context, fmt_pct, fmt_price, fmt_volume, group_by_month,
    month_label, nice_bounds, render, render_calendar, render_chart,
    render_drawer, render_investor, render_reports, render_stale,
    render_target_change, render_weekly,
)
from collect_stock import is_stale, quote_time_iso  # noqa: E402
from collect_investor import (  # noqa: E402
    merge_day, pick_investor_values, prune, ticker_of,
)

NAMES = {"삼성전자", "SK하이닉스"}
OK_URL = "https://www.mk.co.kr/news/stock/1"


def item(**kw):
    base = {"title": "제목", "summary": "요약", "url": OK_URL, "source": "매일경제 증권"}
    base.update(kw)
    return base


# --- 입력 검증 --------------------------------------------------------------

# 정상 항목은 통과하고 필드가 보존된다
v = validate_input({"market_news": [item()], "stock_news": []}, NAMES)
assert v["market_news"] == [item()], v

# http(s)가 아닌 URL은 drop
for bad in ("javascript:alert(1)", "data:text/html,x", "//evil.test/x", "", None, 123):
    v = validate_input({"market_news": [item(url=bad)]}, NAMES)
    assert v["market_news"] == [], (bad, v)

# title이 없거나 문자열이 아니면 drop
for bad in ("", "   ", None, {"a": 1}):
    v = validate_input({"market_news": [item(title=bad)]}, NAMES)
    assert v["market_news"] == [], (bad, v)

# stock_news의 stock은 watchlist name과 정확히 일치해야 한다 (별칭·영문명 불가)
for bad in ("삼전", "Samsung Electronics", "없는종목", None):
    v = validate_input({"stock_news": [item(stock=bad)]}, NAMES)
    assert v["stock_news"] == [], (bad, v)
v = validate_input({"stock_news": [item(stock="삼성전자")]}, NAMES)
assert len(v["stock_news"]) == 1, v

# 입력이 object/배열이 아니어도 죽지 않는다
assert validate_input(None, NAMES) == {"market_news": [], "stock_news": []}
assert validate_input({"market_news": "not a list"}, NAMES)["market_news"] == []
assert validate_input({"market_news": [None, 1, "x"]}, NAMES)["market_news"] == []

# market_news 상한 초과분은 drop
many = validate_input({"market_news": [item(url=f"{OK_URL}/{i}") for i in range(30)]}, NAMES)
assert len(many["market_news"]) == 10, len(many["market_news"])


# --- 중복 판정 --------------------------------------------------------------

# tracking param과 trailing slash는 무시하고 같은 기사로 본다
seen = {normalize_url(OK_URL)}
fresh, dup = filter_new([item(url=OK_URL + "/?utm_source=x")], seen)
assert (fresh, dup) == ([], 1), (fresh, dup)

# 처음 보는 URL은 통과하고 정규화 결과가 붙는다
fresh, dup = filter_new([item(url="https://x.test/new")], seen)
assert dup == 0 and fresh[0]["_normalized_url"] == "https://x.test/new", (fresh, dup)


# --- 종목별 묶기 ------------------------------------------------------------

watchlist = [{"name": "삼성전자"}, {"name": "SK하이닉스"}]
grouped = group_by_stock(
    [item(stock="삼성전자", url=f"{OK_URL}/{i}") for i in range(5)], watchlist,
)
# 종목당 상한 3건, 뉴스 없는 종목도 키는 남는다
assert len(grouped["삼성전자"]) == 3, grouped
assert grouped["SK하이닉스"] == [], grouped
# stock 키는 출력에서 제거된다 (묶음 자체가 종목을 나타내므로)
assert "stock" not in grouped["삼성전자"][0], grouped["삼성전자"][0]


# --- 포맷 -------------------------------------------------------------------

# 원화 주가는 정수, 지수·환율은 소수 2자리 — 둘 다 currency가 KRW라 통화로는 못 가린다
assert fmt_price(273250.0, "KRW", is_stock=True) == "273,250"
assert fmt_price(6998.16, "KRW") == "6,998.16"      # 코스피
assert fmt_price(1383.46, "KRW") == "1,383.46"      # 원/달러
assert fmt_price(833.76, "KRW") == "833.76"         # 코스닥
assert fmt_price(26522.545, "USD") == "26,522.54"   # 나스닥
assert fmt_price(None, "KRW") == "—"
assert fmt_change(None, None) == ("—", "flat")
assert fmt_change(100, 1.5)[1] == "up"
assert fmt_change(-100, -1.5)[1] == "down"
assert fmt_change(0, 0)[1] == "flat"
assert fmt_volume(140_210_400).endswith("억"), fmt_volume(140_210_400)
assert fmt_volume(None) == "—"


# --- 레벨 컨텍스트 ----------------------------------------------------------

# 시계열은 별도 파일에서 symbol로 찾아 붙인다. history 자체는 archive에 싣지 않는다
HISTORIES = {"005930.KS": [{"date": "2026-09-18", "close": 80.0},
                           {"date": "2026-09-21", "close": 90.0}]}

merged = with_level_context([{
    "symbol": "005930.KS", "name": "삼성전자", "price": 90.0, "currency": "KRW",
}], HISTORIES)
assert "history" not in merged[0], merged[0]
assert merged[0]["price"] == 90.0, "기존 시세 필드는 그대로 보존"
assert merged[0]["level_context"]["period_high"] == 90.0, merged[0]

# 시계열이 없으면 level_context 키 자체를 만들지 않는다 (빈 값으로 채우지 않는다)
assert "level_context" not in with_level_context([{"symbol": "X", "name": "X"}], {})[0]
assert "level_context" not in with_level_context(
    [{"symbol": "005930.KS", "name": "삼성전자"}], {},
)[0], "시계열 파일이 통째로 없어도 죽지 않는다"
assert with_level_context([None, "x"], HISTORIES) == []

# raw 파일에 history가 남아 있어도 읽지 않는다 (분리 이전 형식과의 혼동 방지)
legacy = with_level_context(
    [{"symbol": "X", "name": "X", "history": [{"date": "2026-09-21", "close": 1}]}], {},
)
assert "level_context" not in legacy[0] and "history" not in legacy[0], legacy[0]

# curated JSON이 level_context를 흉내 내도 반영되지 않는다 — 계산값만 쓴다
faked = with_level_context(
    [{"symbol": "X", "name": "X", "level_context": {"period_high": 1}}], {},
)
assert "level_context" not in faked[0], faked[0]

# 시계열 파일 읽기 — 없으면 빈 dict, 형식이 깨져도 죽지 않는다
import json as _json  # noqa: E402
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as _tmp:
    _root = pathlib.Path(_tmp)
    (_root / "inbox").mkdir()
    _saved_repo = stock_report.REPO_DIR
    stock_report.REPO_DIR = _root
    try:
        assert load_histories("2026-09-21") == {}, "파일이 없으면 빈 dict"

        def _write(payload):
            (_root / "inbox" / "2026-09-21-stock-history.json").write_text(
                _json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        _write({"date": "2026-09-21", "range": "6mo",
                "history": {"005930.KS": [{"date": "2026-09-21", "close": 1.0}],
                            "깨진값": "배열이 아님"}})
        loaded = load_histories("2026-09-21")
        assert list(loaded) == ["005930.KS"], loaded

        for bad in ({"history": []}, {"history": "x"}, [], "nope"):
            _write(bad)
            assert load_histories("2026-09-21") == {}, bad
    finally:
        stock_report.REPO_DIR = _saved_repo

# 표기 형식: "고점 -X.X% · 저점 +X.X% · N일 박스 A~B"
line = fmt_level_context(
    {"pct_from_high": -12.44, "pct_from_low": 31.0,
     "box_low": 254000.0, "box_high": 273250.0, "box_window": 20},
    "KRW",
)
assert line == "고점 -12.4% · 저점 +31.0% · 20일 박스 254,000~273,250", line

# 박스 일수는 상수가 아니라 실제 표본 수를 찍는다 (시계열이 짧은 경우)
short_line = fmt_level_context(
    {"pct_from_high": 0.0, "pct_from_low": 0.0,
     "box_low": 100.0, "box_high": 100.0, "box_window": 1}, "KRW",
)
assert short_line.endswith("1일 박스 100~100"), short_line

# 0은 부호를 붙이지 않는다 — "고점 +0.0%"는 고점을 넘은 것처럼 읽힌다
zero_line = fmt_level_context(
    {"pct_from_high": 0.0, "pct_from_low": 0.0,
     "box_low": 1.0, "box_high": 1.0, "box_window": 1}, "KRW",
)
assert zero_line.startswith("고점 0.0% · 저점 0.0%"), zero_line
# 반올림해서 0이 되는 값도 마찬가지
assert fmt_level_context({"pct_from_high": -0.02}, "KRW") == "고점 0.0%"

# 값이 없으면 줄을 만들지 않는다
assert fmt_level_context(None, "KRW") == ""
assert fmt_level_context({}, "KRW") == ""

# 페이지에도 그대로 실린다
lvl_page = render("2026-09-21", {
    "quotes": {"indices": [], "stocks": [{
        "symbol": "005930.KS", "name": "삼성전자", "price": 273250.0,
        "change": 0, "change_pct": 0, "currency": "KRW",
        "level_context": {"pct_from_high": -12.44, "pct_from_low": 31.0,
                          "box_low": 254000.0, "box_high": 273250.0, "box_window": 20},
    }]},
    "market_news": [], "stock_news": {},
})
assert "고점 -12.4% · 저점 +31.0%" in lvl_page, "레벨 컨텍스트가 페이지에 없다"


# --- 시세 신선도 ------------------------------------------------------------

import datetime as _dt  # noqa: E402

NOW = _dt.datetime(2026, 9, 21, 0, 0, tzinfo=_dt.timezone.utc)

# 임계(24시간) 안쪽은 정상, 넘으면 지연
assert is_stale(NOW.timestamp() - 3600, NOW) is False
assert is_stale(NOW.timestamp() - 23 * 3600, NOW) is False
assert is_stale(NOW.timestamp() - 25 * 3600, NOW) is True
# 정확히 임계면 지연이 아니다 (초과부터 지연)
assert is_stale(NOW.timestamp() - 24 * 3600, NOW) is False
# 시세 시각이 미래여도 지연은 아니다
assert is_stale(NOW.timestamp() + 3600, NOW) is False
# 시각을 못 받았으면 모르는 것 — 지연으로 단정하지 않는다
for bad in (None, "어제", True, {}):
    assert is_stale(bad, NOW) is False, bad

# 재시도 — requests를 가짜로 끼워 넣어 백오프 순서까지 확인한다.
# fetch_chart가 호출 시점에 import하므로 sys.modules에 심어두면 잡힌다.
import types  # noqa: E402

import collect_stock  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_requests(outcomes):
    """outcomes를 순서대로 돌려준다. 예외면 raise, 아니면 그 값을 json()으로."""
    calls = []

    def get(url, **kw):
        calls.append(kw.get("params"))
        result = outcomes[len(calls) - 1]
        if isinstance(result, Exception):
            raise result
        return _Resp(result)

    module = types.ModuleType("requests")
    module.get = get
    return module, calls


def _with_fake(outcomes, fn):
    """가짜 requests + sleep으로 fn()을 돌리고 (반환값, 호출수, 대기시간)."""
    saved_req = sys.modules.get("requests")
    saved_sleep = collect_stock.time.sleep
    delays = []
    sys.modules["requests"], calls = _fake_requests(outcomes)
    collect_stock.time.sleep = delays.append
    try:
        return fn(), calls, delays
    finally:
        collect_stock.time.sleep = saved_sleep
        if saved_req is None:
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = saved_req


PAYLOAD = {"chart": {"result": [{"meta": {"regularMarketPrice": 100,
                                          "chartPreviousClose": 90,
                                          "regularMarketTime": 1789967099}}]}}

# 두 번 실패하고 세 번째에 성공 — 백오프는 1초, 2초
out, calls, delays = _with_fake(
    [OSError("dns"), OSError("dns"), PAYLOAD],
    lambda: collect_stock.fetch_quote("005930.KS"),
)
assert out["price"] == 100 and len(calls) == 3, (out, calls)
assert delays == [1.0, 2.0], delays

# FETCH_RETRIES회를 다 쓰고도 실패하면 마지막 예외를 올린다 (호출부가 safe로 감싼다)
try:
    _with_fake(
        [OSError("dns")] * (collect_stock.FETCH_RETRIES + 1),
        lambda: collect_stock.fetch_quote("005930.KS"),
    )
except OSError:
    pass
else:
    raise AssertionError("전부 실패하면 예외가 올라와야 한다")

# 백오프는 1 → 2 → 4초
_, _, delays = _with_fake(
    [OSError("dns")] * 3 + [PAYLOAD],
    lambda: collect_stock.fetch_quote("005930.KS"),
)
assert delays == [1.0, 2.0, 4.0], delays

# 시계열 수집도 같은 재시도를 거치고, close가 null인 날은 통째로 빠진다
hist_payload = {"chart": {"result": [{
    "meta": {"exchangeTimezoneName": "Asia/Seoul"},
    "timestamp": [1757984400, 1758243600, 1758330000],
    "indicators": {"quote": [{"close": [100.0, None, 110.0]}]},
}]}}
hist, calls, delays = _with_fake(
    [OSError("dns"), hist_payload],
    lambda: collect_stock.fetch_history("005930.KS"),
)
assert [h["close"] for h in hist] == [100.0, 110.0], hist
assert all(len(h["date"]) == 10 for h in hist), hist
assert calls[-1]["range"] == collect_stock.HISTORY_RANGE, calls
assert delays == [1.0], delays


assert quote_time_iso(0) == "1970-01-01T00:00:00Z"
assert quote_time_iso(1789967099) == "2026-09-21T05:04:59Z"   # KST 14:04 장중
for bad in (None, "x", True):
    assert quote_time_iso(bad) is None, bad

# 지연 종목은 가격 옆에 ⚠️, 정상 종목에는 아무것도 붙지 않는다
assert render_stale(False) == "" and render_stale(None) == ""
assert "⚠️" in render_stale(True) and "데이터 지연" in render_stale(True)

stale_page = render("2026-09-21", {
    "quotes": {"indices": [], "stocks": [
        {"symbol": "005930.KS", "name": "삼성전자", "price": 1, "change": 0,
         "change_pct": 0, "currency": "KRW", "stale": True},
        {"symbol": "000660.KS", "name": "SK하이닉스", "price": 1, "change": 0,
         "change_pct": 0, "currency": "KRW", "stale": False},
    ]},
    "market_news": [], "stock_news": {},
})
assert stale_page.count("⚠️") == 1, stale_page.count("⚠️")
assert stale_page.index("삼성전자") < stale_page.index("⚠️") < stale_page.index("SK하이닉스")

# stale 키가 아예 없는 예전 inbox도 그대로 렌더된다 (하위 호환)
old_page = render("2026-09-21", {
    "quotes": {"indices": [], "stocks": [{"symbol": "005930.KS", "name": "삼성전자",
                                          "price": 1, "change": 0, "change_pct": 0}]},
    "market_news": [], "stock_news": {},
})
assert "⚠️" not in old_page


# --- 애널리스트 컨센서스 ----------------------------------------------------

CONSENSUS = {
    "price_targets": {"current": 274000.0, "high": 725000.0, "low": 290000.0,
                      "mean": 475850.1, "median": 465000.0},
    "recommendations": [
        {"period": "0m", "strongBuy": 11, "buy": 24, "hold": 1, "sell": 0, "strongSell": 0},
        {"period": "-1m", "strongBuy": 11, "buy": 25, "hold": 1, "sell": 0, "strongSell": 0},
    ],
}

# 컨센서스는 수집 결과에서만 온다 — 시세 원본에 같은 이름이 있어도 버린다
merged_c = with_level_context(
    [{"symbol": "005930.KS", "name": "삼성전자", "consensus": {"fake": 1}}],
    {}, {"005930.KS": CONSENSUS},
)
assert merged_c[0]["consensus"] == CONSENSUS, merged_c[0]
# 컨센서스가 없는 종목은 키를 만들지 않는다
assert "consensus" not in with_level_context([{"symbol": "X", "name": "X"}], {}, {})[0]
assert "consensus" not in with_level_context([{"symbol": "X", "name": "X"}], {}, None)[0]

# 표기: 목표가 평균 + 현재가 대비 괴리율 + 투자의견 분포 (매수 = strongBuy + buy)
line_c = fmt_consensus(CONSENSUS, "KRW", 274000.0)
assert line_c == "목표주가 평균 475,850 (괴리 +73.7%) · 매수 35 · 보유 1 · 매도 0", line_c
# 현재가가 없으면 괴리율을 만들지 않는다
assert "괴리" not in fmt_consensus(CONSENSUS, "KRW", None)
assert "괴리" not in fmt_consensus(CONSENSUS, "KRW", 0)
# 한쪽만 있어도 있는 것만 찍는다
assert fmt_consensus({"recommendations": CONSENSUS["recommendations"]}, "KRW", 1) == \
    "매수 35 · 보유 1 · 매도 0"
assert fmt_consensus({"price_targets": {"mean": 100.0}}, "KRW", None) == "목표주가 평균 100"
for bad in (None, {}, "x", {"price_targets": {}, "recommendations": []}):
    assert fmt_consensus(bad, "KRW", 100) == "", bad


# --- 증권사 리포트 ----------------------------------------------------------

HK_ROW = (
    '<tr>'
    '<td class="first txt_number">2026-07-31</td>'
    '<td class="text_l"><a href="/analysis/downpdf?report_idx=651325">'
    '삼성전자(005930) 무시할 실적이 아니다 </a>'
    '<div class="layerPop"><div id="content_651325">중복 제목</div></div></td>'
    '<td class="text_r txt_number">460,000</td>'
    '<td>\n매수      </td>'
    '<td>김운호</td>'
    '<td>IBK투자증권</td>'
    '<td><a href="/chart/view_frame?report_type=CO&business_code=005930">차트</a></td>'
    '</tr>'
)

parsed = parse_reports(HK_ROW)
assert len(parsed) == 1, parsed
r0 = parsed[0]
assert r0["code"] == "005930" and r0["report_idx"] == "651325"
# 제목 칸의 마우스오버 팝업(중복 제목)은 떨어져 나가야 한다
assert r0["title"] == "삼성전자(005930) 무시할 실적이 아니다", r0["title"]
assert r0["target_price"] == "460,000" and r0["opinion"] == "매수"
assert r0["analyst"] == "김운호" and r0["broker"] == "IBK투자증권"
assert r0["url"].endswith("report_idx=651325")

# 헤더 행·종목코드 없는 행은 버린다
assert parse_reports("<tr><th>작성일</th><th>제목</th></tr>") == []
assert parse_reports("<tr><td>x</td></tr>") == []
assert parse_reports('<tr><td class="first txt_number">2026-07-31</td><td>a</td>'
                     '<td>b</td><td>c</td><td>d</td><td>e</td></tr>') == []

# 누적 — 같은 report_idx는 두 번 들어가지 않는다
st = {"reports": {}}
assert merge_reports(st, parsed, {"005930"}) == 1
assert merge_reports(st, parsed, {"005930"}) == 0
assert len(st["reports"]["005930"]) == 1
# code는 묶음 키가 되므로 항목에서 빠진다
assert "code" not in st["reports"]["005930"][0]
# watchlist에 없는 종목은 누적하지 않는다
assert merge_reports(st, parsed, {"000660"}) == 0

# 오래된 리포트는 잘라내되 종목당 최소 몇 건은 남긴다
# (실적 시즌 사이에는 몇 달째 새 리포트가 없다)
old_state = {"reports": {"005930": [{"date": "2020-01-01", "report_idx": str(i)}
                                    for i in range(5)]}}
prune_reports(old_state, "2026-09-21")
assert 0 < len(old_state["reports"]["005930"]) <= 3, old_state

assert research_ticker("005930.KS") == "005930"

# symbol → 종목명 묶음. 리포트 없는 종목은 키를 만들지 않는다
WL = [{"symbol": "005930.KS", "name": "삼성전자"}, {"symbol": "000660.KS", "name": "SK하이닉스"}]
g = group_reports({"005930.KS": [dict(r0, url="http://x.test/1")]}, WL)
assert list(g) == ["삼성전자"], g
# http(s)가 아닌 URL·제목 없는 항목은 drop (뉴스와 같은 방어)
assert group_reports({"005930.KS": [dict(r0, url="javascript:alert(1)")]}, WL) == {}
assert group_reports({"005930.KS": [dict(r0, title="")]}, WL) == {}
assert group_reports({"005930.KS": "not a list"}, WL) == {}
assert group_reports({}, WL) == {}

# 렌더 — 제목·목표가·투자의견·애널리스트·증권사·PDF 링크
rep_html = render_reports(g["삼성전자"])
assert "무시할 실적이 아니다" in rep_html and "IBK투자증권" in rep_html
assert "김운호" in rep_html and "목표 460,000" in rep_html
assert 'href="http://x.test/1"' in rep_html
# 없으면 목록 자체를 만들지 않는다 ("없음"을 띄우는 것이 오히려 소음이다)
assert render_reports([]) == "" and render_reports(None) == ""
assert render_reports(["x", 1]) == ""
# 제목도 이스케이프된다
evil_rep = render_reports([{"title": "<img src=x>", "url": "http://x.test/1"}])
assert "<img src=x>" not in evil_rep and "&lt;img src=x&gt;" in evil_rep

# 페이지에 컨센서스 줄과 리포트가 함께 실린다
res_page = render("2026-09-21", {
    "quotes": {"indices": [], "stocks": [{
        "symbol": "005930.KS", "name": "삼성전자", "price": 274000.0,
        "change": 0, "change_pct": 0, "currency": "KRW", "consensus": CONSENSUS,
    }]},
    "research_reports": g, "market_news": [], "stock_news": {},
})
assert "목표주가 평균 475,850 (괴리 +73.7%)" in res_page
assert "무시할 실적이 아니다" in res_page

# research_reports 키가 없는 예전 archive도 그대로 렌더된다 (하위 호환)
assert "목표주가" not in render(
    "2026-09-21", {"quotes": {}, "market_news": [], "stock_news": {}},
)


# --- 이벤트 캘린더 ----------------------------------------------------------

EVENTS = [
    {"date": "2026-09-21", "label": "오늘 이벤트"},
    {"date": "2026-09-28", "label": "일주일 뒤"},
    {"date": "2026-10-05", "label": "딱 14일 뒤"},
    {"date": "2026-10-06", "label": "15일 뒤 — 범위 밖"},
    {"date": "2026-09-20", "label": "어제 — 이미 지났다"},
]

up = upcoming_events(EVENTS, "2026-09-21")
assert [e["label"] for e in up] == ["오늘 이벤트", "일주일 뒤", "딱 14일 뒤"], up
assert [e["d_day"] for e in up] == [0, 7, 14], up      # D-day 오름차순

# 지난 이벤트는 주간 섹션에서만 쓴다
past = past_events(EVENTS, "2026-09-21", 7)
assert [e["label"] for e in past] == ["어제 — 이미 지났다"], past
assert past[0]["days_ago"] == 1
# 오늘 이벤트는 '지난 것'이 아니다 (다가오는 목록에 이미 있다)
assert all(e["label"] != "오늘 이벤트" for e in past)

assert upcoming_events([], "2026-09-21") == []

# D-day 표기
assert fmt_dday(0) == "D-DAY" and fmt_dday(3) == "D-3"
assert fmt_dday(None) == "" and fmt_dday("x") == ""

# 렌더 — "📅 D-n label"
cal = render_calendar(up)
assert "D-DAY" in cal and "D-7" in cal and "D-14" in cal, cal
assert cal.index("D-DAY") < cal.index("D-7") < cal.index("D-14")
assert "📅" in cal

# 이벤트가 없으면 섹션 자체를 만들지 않는다
assert render_calendar([]) == "" and render_calendar([{"label": "x"}]) == ""

# 라벨도 이스케이프된다 (calendar.json은 사용자가 직접 쓰는 파일이다)
evil_cal = render_calendar([{"date": "2026-09-21", "label": "<img src=x>", "d_day": 0}])
assert "<img src=x>" not in evil_cal and "&lt;img src=x&gt;" in evil_cal

# 캘린더는 지수보다 위, 즉 페이지 최상단에 온다
cal_page = render("2026-09-21", {
    "calendar": up,
    "quotes": {"indices": [{"symbol": "^KS11", "name": "코스피", "price": 1,
                            "change": 0, "change_pct": 0}], "stocks": []},
    "market_news": [], "stock_news": {},
})
# CSS에도 .idx-price가 있으므로 본문에만 나오는 지수 이름으로 앵커를 잡는다
assert cal_page.index("다가오는 일정") < cal_page.index("코스피"), "캘린더가 상단이어야 한다"

# calendar 키가 없는 예전 archive도 그대로 렌더된다 (하위 호환)
assert "다가오는 일정" not in render(
    "2026-09-21", {"quotes": {}, "market_news": [], "stock_news": {}},
)


# --- 주간 심화 섹션 ---------------------------------------------------------

# 2026-09-21은 월요일, 22는 화요일
assert WEEKLY_DAY == "Monday", WEEKLY_DAY
assert is_weekly_day("2026-09-21") is True
assert is_weekly_day("2026-09-22") is False

WK_HISTORY = [
    {"date": "2026-09-07", "close": 100.0},
    {"date": "2026-09-11", "close": 80.0},
    {"date": "2026-09-15", "close": 90.0},
    {"date": "2026-09-18", "close": 95.0},
    {"date": "2026-09-21", "close": 110.0},
]
WK_STOCKS = [{"symbol": "005930.KS", "name": "삼성전자", "currency": "KRW"}]
WK_HISTORIES = {"005930.KS": WK_HISTORY}

# WEEKLY_DAY가 아니면 섹션 자체를 만들지 않는다
assert build_weekly(WK_STOCKS, WK_HISTORIES, "2026-09-22", []) is None

wk = build_weekly(WK_STOCKS, WK_HISTORIES, "2026-09-21", EVENTS)
assert wk["day"] == "Monday"
row = wk["stocks"][0]
assert row["name"] == "삼성전자" and row["symbol"] == "005930.KS"
assert round(row["change_pct"], 4) == 37.5          # 80 → 110
assert row["week_high"] == 110.0 and row["week_low"] == 90.0
assert round(row["recovery_delta"], 4) == 37.5      # 0% → 37.5%
assert [e["label"] for e in wk["past_events"]] == ["어제 — 이미 지났다"]

# 시계열이 없으면 그 종목만 빠진다 (섹션은 지난 이벤트로 유지)
only_events = build_weekly([{"symbol": "X", "name": "X"}], {}, "2026-09-21", EVENTS)
assert only_events["stocks"] == [] and only_events["past_events"]

# 실을 것이 하나도 없으면 None
assert build_weekly([], {}, "2026-09-21", []) is None
assert build_weekly(WK_STOCKS, {}, "2026-09-21", []) is None

# 포맷 — 부호를 항상 붙이고 없는 값은 —
assert fmt_pct(3.456) == ("+3.46%", "up")
assert fmt_pct(-3.456) == ("-3.46%", "down")
assert fmt_pct(0) == ("+0.00%", "flat")
assert fmt_pct(2.0, unit="%p")[0] == "+2.00%p"
for bad in (None, "x", True):
    assert fmt_pct(bad) == ("—", "flat"), bad

# 렌더
wk_html = render_weekly(wk)
assert "주간 심화" in wk_html and "+37.50%" in wk_html
assert "저점 대비 회복률" in wk_html and "+37.50%p" in wk_html
assert render_weekly(None) == "" and render_weekly({}) == ""
assert render_weekly({"stocks": [], "past_events": []}) == ""

# 종목 이름·이벤트 라벨 이스케이프
evil_wk = render_weekly({
    "stocks": [{"name": "<img src=x>", "change_pct": 1.0}],
    "past_events": [{"label": "<b>e</b>", "days_ago": 1}],
})
assert "<img src=x>" not in evil_wk and "&lt;img src=x&gt;" in evil_wk
assert "<b>e</b>" not in evil_wk

# 페이지에서는 관심종목 아래, 시장 뉴스 위
wk_page = render("2026-09-21", {
    "quotes": {"indices": [], "stocks": [{"symbol": "005930.KS", "name": "삼성전자",
                                          "price": 1, "change": 0, "change_pct": 0}]},
    "weekly": wk, "market_news": [], "stock_news": {},
})
assert wk_page.index("<h2>관심종목") < wk_page.index("<h2>주간 심화") < wk_page.index("<h2>시장 주요 뉴스")

# weekly 키가 없는 평일 archive는 섹션 없이 렌더된다 (하위 호환)
assert "주간 심화" not in render(
    "2026-09-22", {"quotes": {}, "market_news": [], "stock_news": {}},
)


# --- 목표주가 추이 ----------------------------------------------------------

TREND = {"days": {
    "2026-09-07": {"005930.KS": {"mean": 440000.0}},
    "2026-09-11": {"005930.KS": {"mean": 460000.0}},   # 경계(09-14) 이전 마지막
    "2026-09-18": {"005930.KS": {"mean": 470000.0}},
    "2026-09-21": {"005930.KS": {"mean": 475850.1}},
}}

ch = consensus_change(TREND, "005930.KS", "2026-09-21")
assert ch["from_date"] == "2026-09-11" and ch["to_date"] == "2026-09-21", ch
assert ch["from_mean"] == 460000.0 and ch["to_mean"] == 475850.1
assert round(ch["change_pct"], 4) == round((475850.1 - 460000) / 460000 * 100, 4)

# 수집이 빠진 날이 있어도 경계 이전의 가장 최근 기록을 쓴다
gap = {"days": {"2026-09-01": {"X": {"mean": 100.0}},
                "2026-09-21": {"X": {"mean": 120.0}}}}
assert round(consensus_change(gap, "X", "2026-09-21")["change_pct"], 4) == 20.0

# 경계 이전 기록이 없으면 None — 비교 대상이 없는 것을 0%로 쓰면 거짓말이다
assert consensus_change({"days": {"2026-09-21": {"X": {"mean": 1.0}}}}, "X", "2026-09-21") is None
# 같은 날 하나뿐이어도 None
assert consensus_change({"days": {"2026-09-01": {"X": {"mean": 1.0}}}}, "X", "2026-09-01") is None
# 다른 종목·빈 state·깨진 입력에도 죽지 않는다
assert consensus_change(TREND, "000660.KS", "2026-09-21") is None
assert consensus_change({}, "X", "2026-09-21") is None
assert consensus_change({"days": "nope"}, "X", "2026-09-21") is None
assert consensus_change(None, "X", "2026-09-21") is None
assert consensus_change(TREND, "005930.KS", "not-a-date") is None
# mean이 숫자가 아니면 그 날짜는 없는 것으로 본다
bad_mean = {"days": {"2026-09-01": {"X": {"mean": "N/A"}},
                     "2026-09-21": {"X": {"mean": 120.0}}}}
assert consensus_change(bad_mean, "X", "2026-09-21") is None
# 미래 날짜 기록은 쓰지 않는다
future = dict(TREND["days"]); future["2026-12-31"] = {"005930.KS": {"mean": 9.0}}
assert consensus_change({"days": future}, "005930.KS", "2026-09-21")["to_mean"] == 475850.1

# 수집 단계: 추이에 쌓는 것은 평균·중앙값과 의견 합계뿐
snap = trend_snapshot(CONSENSUS)
assert snap == {"mean": 475850.1, "median": 465000.0,
                "buy": 35, "hold": 1, "sell": 0}, snap
assert trend_snapshot({}) is None
assert trend_snapshot({"price_targets": {"high": 1.0}}) is None   # mean/median 없음

tr = {"days": {}}
assert merge_trend(tr, "2026-09-21", {"005930.KS": CONSENSUS}) == 1
# 같은 날 재실행은 덮어쓴다
assert merge_trend(tr, "2026-09-21", {"005930.KS": CONSENSUS}) == 1
assert len(tr["days"]["2026-09-21"]) == 1
# 쌓을 것이 없는 날은 빈 날짜를 남기지 않는다
assert merge_trend(tr, "2026-09-22", {"X": {}}) == 0
assert "2026-09-22" not in tr["days"], tr

old_tr = {"days": {"2020-01-01": {"X": {"mean": 1.0}}, "2026-09-21": {"X": {"mean": 2.0}}}}
prune_trend(old_tr, "2026-09-21")
assert list(old_tr["days"]) == ["2026-09-21"], old_tr

# 주간 섹션에 실린다
wk_t = build_weekly(WK_STOCKS, WK_HISTORIES, "2026-09-21", [], TREND)
assert wk_t["stocks"][0]["target_change"]["to_mean"] == 475850.1
# 추이가 없으면 키를 만들지 않는다
assert "target_change" not in build_weekly(
    WK_STOCKS, WK_HISTORIES, "2026-09-21", [], {})["stocks"][0]
assert "target_change" not in build_weekly(
    WK_STOCKS, WK_HISTORIES, "2026-09-21", [])["stocks"][0]

tgt_html = render_target_change(ch, "KRW")
assert "460,000 → 475,850" in tgt_html, tgt_html
assert "+3.45%" in tgt_html, tgt_html
assert render_target_change(None, "KRW") == ""
assert render_target_change({"from_mean": "x", "to_mean": 1}, "KRW") == ""

wk_page_t = render("2026-09-21", {
    "quotes": {"indices": [], "stocks": []},
    "weekly": wk_t, "market_news": [], "stock_news": {},
})
assert "목표주가 평균 460,000 → 475,850" in wk_page_t


# --- 렌더링 -----------------------------------------------------------------

# 제목·요약의 HTML은 이스케이프되어야 한다 (RSS 제목은 제3자가 쓴 텍스트다)
html_out = render("2026-09-21", {
    "generated_at": "2026-09-21T00:00:00Z",
    "quotes": {"indices": [], "stocks": []},
    "market_news": [item(title='<img src=x onerror=alert(1)>', url='https://x.test/"')],
    "stock_news": {},
})
assert "<img src=x" not in html_out, "제목이 이스케이프되지 않았다"
assert "&lt;img src=x" in html_out, html_out[:200]
assert 'href="https://x.test/&quot;"' in html_out, "URL이 속성에서 이스케이프되지 않았다"

# 데이터가 비어도 페이지는 생성된다 (링크가 404가 되는 것보다 낫다)
empty = render("2026-09-21", {"quotes": {}, "market_news": [], "stock_news": {}})
assert "신규 기사 없음" in empty
assert empty.strip().endswith("</html>")

# --- 투자자 수급 수집 -------------------------------------------------------

# Yahoo 티커에서 KRX 6자리 코드만 떼어낸다
assert ticker_of("005930.KS") == "005930"
assert ticker_of("000660.KQ") == "000660"
assert ticker_of("005930") == "005930"

# 투자자 × 매수/매도/순매수. 기관은 합계 행을 쓰고 개별 업권은 무시
frame = {
    "매수": {"기관합계": 1000, "개인": 50, "외국인": 800, "금융투자": 1},
    "매도": {"기관합계": 700, "개인": 150, "외국인": 300, "보험": 2},
    "순매수": {"기관합계": 300, "개인": -100, "외국인": 500, "전체": 0},
}
assert pick_investor_values(frame) == {
    "외국인": {"매수": 800, "매도": 300, "순매수": 500},
    "개인": {"매수": 50, "매도": 150, "순매수": -100},
    "기관": {"매수": 1000, "매도": 700, "순매수": 300},
}, pick_investor_values(frame)

# 일부 지표만 와도 있는 것만 담는다 (KRX 응답이 줄어드는 경우)
assert pick_investor_values({"순매수": {"외국인": 5}}) == {"외국인": {"순매수": 5}}
# 쓸 값이 하나도 없으면 None — 빈 레코드를 state에 남기지 않는다
assert pick_investor_values({"순매수": {"금융투자": 1}}) is None
assert pick_investor_values({}) is None
# 값이 정수가 아니면 그 칸만 버린다
assert pick_investor_values(
    {"순매수": {"외국인": "N/A", "개인": 3}}
) == {"개인": {"순매수": 3}}

# 같은 날짜·같은 대상 재실행은 덮어쓴다
st = {"days": {}}
merge_day(st, "2026-09-18", "삼성전자", {"외국인": {"순매수": 1}})
merge_day(st, "2026-09-18", "삼성전자", {"외국인": {"순매수": 2}})
merge_day(st, "2026-09-18", "SK하이닉스", {"외국인": {"순매수": 9}})
assert st["days"]["2026-09-18"] == {
    "삼성전자": {"외국인": {"순매수": 2}},
    "SK하이닉스": {"외국인": {"순매수": 9}},
}, st

# 오래된 날짜는 잘라낸다 (최신 쪽을 남긴다)
st = {"days": {f"2026-01-{d:02d}": {} for d in range(1, 11)}}
prune(st, keep=3)
assert sorted(st["days"]) == ["2026-01-08", "2026-01-09", "2026-01-10"], sorted(st["days"])


# --- 포맷 / 스케일 ----------------------------------------------------------

assert fmt_billion(123_456_000_000) == ("+1,235억", "up")
assert fmt_billion(-50_000_000_000) == ("-500억", "down")
assert fmt_billion(0) == ("+0억", "flat")
assert fmt_billion(1_500_000_000_000)[0] == "+1.50조"   # 1만억 넘으면 조 단위
assert fmt_billion(None) == ("—", "flat")               # 수집 없던 날
assert fmt_daylabel("2026-09-17") == "09/17"

# 축은 항상 0을 포함한다 (0을 자르면 추세가 과장된다)
lo, hi = nice_bounds([100.0, 200.0])
assert lo <= 0 <= hi, (lo, hi)
lo, hi = nice_bounds([-500.0, -100.0])
assert lo <= 0 <= hi, (lo, hi)
# 값이 모두 같아도 납작해지지 않는다
lo, hi = nice_bounds([0.0, 0.0])
assert lo < hi, (lo, hi)


# --- 차트 렌더 --------------------------------------------------------------

DATES5 = ["2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-19"]

# 값이 하나도 없으면 차트를 만들지 않는다
assert render_chart("순매수", DATES5, {"외국인": [None] * 5}) == ""

chart = render_chart("순매수", DATES5, {
    "외국인": [1e11, 2e11, -1e11, 3e11, 1e11],
    "개인": [-1e11, -2e11, 1e11, -3e11, -1e11],
})
assert "<polyline" in chart and 'class="ln s1"' in chart
assert chart.count("<circle") == 10, "점 5개 × 2계열"
assert "<title>" in chart, "점마다 호버 값이 있어야 한다"

# 결측이 있으면 선이 끊긴다 — 없는 값을 이어 그리면 거짓말이 된다
gapped = render_chart("순매수", DATES5, {"외국인": [1e11, 2e11, None, 4e11, 5e11]})
assert gapped.count("<polyline") == 2, "결측 앞뒤로 선이 나뉘어야 한다"
assert gapped.count("<circle") == 4, "결측일에는 점이 없어야 한다"

# 한쪽 끝이 단독 점이면 그쪽은 선이 생기지 않는다 (점 하나로는 선을 못 긋는다)
edge = render_chart("순매수", DATES5, {"외국인": [1e11, None, 3e11, 4e11, 5e11]})
assert edge.count("<polyline") == 1 and edge.count("<circle") == 4

# 양쪽이 끊긴 단독 점은 선을 만들지 않는다
lone = render_chart("순매수", DATES5, {"외국인": [None, 1e11, None, None, None]})
assert "<polyline" not in lone and lone.count("<circle") == 1


# --- 투자자 섹션 ------------------------------------------------------------

# 데이터가 없으면 섹션 자체를 만들지 않는다
assert render_investor({}) == ""
assert render_investor({"dates": [], "series": {}}) == ""

sec = render_investor({
    "dates": DATES5,
    "series": {"삼성전자": {
        "순매수": {"외국인": [1e11] * 5, "개인": [-1e11] * 5, "기관": [0] * 5},
        "매수": {"외국인": [5e11] * 5},
        "매도": {"외국인": [4e11] * 5},
    }},
})
# 지표 3개가 각각 그래프 하나
assert sec.count("<figure") == 3, sec.count("<figure")
for m in ("순매수", "매수", "매도"):
    assert f"<figcaption>{m}" in sec
# 3계열이면 범례가 있어야 한다 (색만으로 구분시키지 않는다)
assert sec.count('class="lg"') == 3
# 값으로도 읽을 수 있어야 한다 (대비 WARN 해소)
assert "<details" in sec and "값으로 보기" in sec

# 대상 이름도 이스케이프된다 (watchlist는 사용자가 직접 쓰는 파일이다)
evil = render_investor({
    "dates": DATES5,
    "series": {"<img src=x>": {"순매수": {"외국인": [1e11] * 5}}},
})
assert "<img src=x>" not in evil and "&lt;img src=x&gt;" in evil

# 투자자 섹션은 지수 바로 아래, 관심종목보다 위에 온다
page = render("2026-09-21", {
    "quotes": {"indices": [{"symbol": "^KS11", "name": "코스피", "price": 1,
                            "change": 0, "change_pct": 0}],
               "stocks": [{"symbol": "005930.KS", "name": "삼성전자", "price": 1,
                           "change": 0, "change_pct": 0, "currency": "KRW"}]},
    "investor_trend": {"dates": DATES5,
                       "series": {"삼성전자": {"순매수": {"외국인": [1e11] * 5}}}},
    "market_news": [], "stock_news": {},
})
# meta description에도 "관심종목"이 들어가므로 본문 헤딩으로 앵커를 잡는다
assert page.index("<h2>투자자 수급") < page.index("<h2>관심종목"), "배치 순서가 틀렸다"
assert page.index("idx-price") < page.index("<h2>투자자 수급"), "지수가 먼저 와야 한다"

# --- 드로어 / 날짜 탐색 -----------------------------------------------------

# 월별 묶음은 최신 달이 먼저, 달 안에서도 최신 날짜가 먼저
groups = group_by_month(["2026-08-28", "2026-09-21", "2026-09-18", "2026-07-01"])
assert [ym for ym, _ in groups] == ["2026-09", "2026-08", "2026-07"], groups
assert groups[0][1] == ["2026-09-21", "2026-09-18"], groups[0]

assert month_label("2026-09") == "2026년 9월"
assert month_label("2026-01") == "2026년 1월"
assert month_label("이상한값") == "이상한값"     # 형식이 깨져도 죽지 않는다

DRAWER_DATES = ["2026-08-28", "2026-09-18", "2026-09-21"]

# 루트 문서는 prefix 없이, 날짜 문서는 ../ 로 링크한다
# (Pages가 /AINews/ 하위에 배포되므로 절대경로를 쓰면 깨진다)
root_drawer = render_drawer("2026-09-21", DRAWER_DATES, "")
assert 'href="2026-08-28/"' in root_drawer
assert "../" not in root_drawer

day_drawer = render_drawer("2026-09-18", DRAWER_DATES, "../")
assert 'href="../2026-08-28/"' in day_drawer

# 보고 있는 날짜만 현재로 표시된다
assert day_drawer.count('aria-current="page"') == 1
assert '<a href="../2026-09-18/" aria-current="page"' in day_drawer

# 현재 날짜가 속한 달만 펼쳐 둔다
assert day_drawer.count("<details open>") == 1, "펼쳐진 달은 하나여야 한다"
assert "<details open><summary>2026년 9월" in day_drawer

# 날짜가 하나뿐이어도 드로어는 만들어진다
solo = render_drawer("2026-09-21", ["2026-09-21"], "")
assert "2026년 9월" in solo


# --- 이전/다음 -------------------------------------------------------------

def page_for(date, dates):
    return render(date, {"quotes": {}, "market_news": [], "stock_news": {}},
                  dates, prefix="../")

# 가운데 날짜는 양쪽 다, 양 끝은 한쪽만
mid = page_for("2026-09-18", DRAWER_DATES)
assert 'href="../2026-08-28/">‹ 이전' in mid and 'href="../2026-09-21/">다음 ›' in mid
first = page_for("2026-08-28", DRAWER_DATES)
assert "‹ 이전" not in first and "다음 ›" in first
last = page_for("2026-09-21", DRAWER_DATES)
assert "‹ 이전" in last and "다음 ›" not in last
# 날짜가 하나뿐이면 페이저 자체가 없다 (".pager" CSS 규칙은 늘 인라인되므로
# 문자열이 아니라 실제 마크업으로 확인한다)
assert '<div class="pager">' not in page_for("2026-09-21", ["2026-09-21"])
assert '<div class="pager">' in mid

print("ok")
