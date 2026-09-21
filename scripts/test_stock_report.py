#!/usr/bin/env python3
"""주식 리포트 검증·중복·렌더링 self-check. 실행: python3 scripts/test_stock_report.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from stock_report import (  # noqa: E402
    filter_new, group_by_stock, normalize_url, validate_input, with_level_context,
)
from render_stock_page import (  # noqa: E402
    fmt_billion, fmt_change, fmt_daylabel, fmt_level_context, fmt_price,
    fmt_volume, nice_bounds, render, render_chart, render_investor, render_stale,
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

# inbox의 history로 계산해서 붙이고, history 자체는 archive에 싣지 않는다
merged = with_level_context([{
    "symbol": "005930.KS", "name": "삼성전자", "price": 90.0, "currency": "KRW",
    "history": [{"date": "2026-09-18", "close": 80.0},
                {"date": "2026-09-21", "close": 90.0}],
}])
assert "history" not in merged[0], merged[0]
assert merged[0]["price"] == 90.0, "기존 시세 필드는 그대로 보존"
assert merged[0]["level_context"]["period_high"] == 90.0, merged[0]

# 시계열이 없으면 level_context 키 자체를 만들지 않는다 (빈 값으로 채우지 않는다)
assert "level_context" not in with_level_context([{"symbol": "X", "name": "X"}])[0]
assert with_level_context([None, "x"]) == []

# curated JSON이 level_context를 흉내 내도 반영되지 않는다 — 계산값만 쓴다
faked = with_level_context([{"symbol": "X", "name": "X", "level_context": {"period_high": 1}}])
assert "level_context" not in faked[0], faked[0]

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

print("ok")
