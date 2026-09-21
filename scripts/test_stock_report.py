#!/usr/bin/env python3
"""주식 리포트 검증·중복·렌더링 self-check. 실행: python3 scripts/test_stock_report.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from stock_report import (  # noqa: E402
    filter_new, group_by_stock, normalize_url, validate_input,
)
from render_stock_page import fmt_change, fmt_price, fmt_volume, render  # noqa: E402

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

print("ok")
