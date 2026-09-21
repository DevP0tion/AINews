#!/usr/bin/env python3
"""주식 리포트 검증·중복·렌더링 self-check. 실행: python3 scripts/test_stock_report.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from stock_report import (  # noqa: E402
    filter_new, group_by_stock, normalize_url, validate_input,
)
from render_stock_page import (  # noqa: E402
    fmt_billion, fmt_change, fmt_daylabel, fmt_price, fmt_volume,
    render, render_investor,
)
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

# 기관은 합계 행을 쓴다. 개별 업권 행은 무시
rows = {"금융투자": 1, "보험": 2, "기관합계": 300, "개인": -100,
        "외국인": 500, "기타외국인": 7, "전체": 0}
assert pick_investor_values(rows) == {"외국인": 500, "개인": -100, "기관": 300}

# 라벨이 일부 빠져도 있는 것만 담는다
assert pick_investor_values({"외국인": 5}) == {"외국인": 5}
# 셋 다 없으면 None — 빈 레코드를 state에 남기지 않는다
assert pick_investor_values({"금융투자": 1, "전체": 2}) is None
assert pick_investor_values({}) is None
# 값이 정수가 아니면 그 항목만 버린다
assert pick_investor_values({"외국인": "N/A", "개인": 3}) == {"개인": 3}

# 같은 날짜·같은 대상 재실행은 덮어쓴다
st = {"days": {}}
merge_day(st, "2026-09-18", "KOSPI", {"외국인": 1})
merge_day(st, "2026-09-18", "KOSPI", {"외국인": 2})
merge_day(st, "2026-09-18", "KOSDAQ", {"외국인": 9})
assert st["days"]["2026-09-18"] == {"KOSPI": {"외국인": 2}, "KOSDAQ": {"외국인": 9}}, st

# 오래된 날짜는 잘라낸다 (최신 쪽을 남긴다)
st = {"days": {f"2026-01-{d:02d}": {} for d in range(1, 11)}}
prune(st, keep=3)
assert sorted(st["days"]) == ["2026-01-08", "2026-01-09", "2026-01-10"], sorted(st["days"])


# --- 투자자 수급 포맷/렌더 --------------------------------------------------

assert fmt_billion(123_456_000_000) == ("+1,235억", "up")
assert fmt_billion(-50_000_000_000) == ("-500억", "down")
assert fmt_billion(0) == ("+0억", "flat")
assert fmt_billion(1_500_000_000_000)[0] == "+1.50조"   # 1만억 넘으면 조 단위
assert fmt_billion(None) == ("—", "flat")               # 수집 없던 날
assert fmt_daylabel("2026-09-17") == "09/17"

# 데이터가 없으면 섹션 자체를 만들지 않는다
assert render_investor({}) == ""
assert render_investor({"dates": [], "series": {}}) == ""

# 결측일(None)이 섞여도 합계는 있는 값만 더한다
html_inv = render_investor({
    "dates": ["2026-09-17", "2026-09-18"],
    "series": {"KOSPI": {"외국인": [100_000_000_000, None]}},
})
assert "+1,000억" in html_inv and "—" in html_inv, html_inv
assert html_inv.count("+1,000억") == 2, "합계가 있는 값만으로 계산돼야 한다"

# 대상 이름도 이스케이프된다 (watchlist는 사용자가 직접 쓰는 파일이다)
evil = render_investor({
    "dates": ["2026-09-17"],
    "series": {"<img src=x>": {"외국인": [1]}},
})
assert "<img src=x>" not in evil and "&lt;img src=x&gt;" in evil

# 투자자 섹션은 지수 바로 아래, 관심종목보다 위에 온다
page = render("2026-09-21", {
    "quotes": {"indices": [{"symbol": "^KS11", "name": "코스피", "price": 1,
                            "change": 0, "change_pct": 0}],
               "stocks": [{"symbol": "005930.KS", "name": "삼성전자", "price": 1,
                           "change": 0, "change_pct": 0, "currency": "KRW"}]},
    "investor_trend": {"dates": ["2026-09-17"],
                       "series": {"코스피": {"외국인": [1]}}},
    "market_news": [], "stock_news": {},
})
# meta description에도 "관심종목"이 들어가므로 본문 헤딩으로 앵커를 잡는다
assert page.index("<h2>투자자 수급") < page.index("<h2>관심종목"), "배치 순서가 틀렸다"
assert page.index("idx-price") < page.index("<h2>투자자 수급"), "지수가 먼저 와야 한다"

print("ok")
