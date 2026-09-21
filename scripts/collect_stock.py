#!/usr/bin/env python3
"""
collect_stock.py — 주식 리포트용 결정론적 수집 스크립트.

국내 증시/경제 RSS + Yahoo Finance 시세를 모아
inbox/YYYY-MM-DD-stock-raw.json 으로 저장한다.
AI 판단(선정·한국어 요약)은 curate 단계의 Claude가 담당한다.

관심종목은 config/watchlist.json 에서 읽는다.

출력 스키마:
  {
    "date": "YYYY-MM-DD",
    "collected_at": ISO UTC,
    "watchlist": [{"symbol", "name", "aliases"}],
    "quotes": {
      "indices": [{"symbol","name","price","change","change_pct","prev_close",...}],
      "stocks":  [{... 동일 + "volume", "quote_time", "stale",
                   "history": [{"date","close"}]}]
    },
    "market_news": [
      {"title","url","source","published","summary","matched": ["삼성전자", ...]}
    ]
  }
"""
from __future__ import annotations

import datetime
import json
import pathlib
import os
import sys
import time
from zoneinfo import ZoneInfo

# feedparser·requests는 실제 수집에만 필요하다. 모듈 최상단에서 끌어오면
# 순수 함수(is_stale 등)를 테스트하는 데까지 설치를 요구하게 되므로,
# collect_investor.py의 pykrx와 같은 방식으로 호출 시점에 임포트한다.

UA = "PotionBot-News/1.0 (+https://github.com/DevP0tion/AINews)"
TIMEOUT = 15
KST = ZoneInfo("Asia/Seoul")

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
WATCHLIST_PATH = REPO_DIR / "config" / "watchlist.json"

# (라벨, RSS URL). 라벨은 리포트에 출처로 그대로 노출된다.
FEEDS = [
    ("매일경제 증권", "https://www.mk.co.kr/rss/50200011/"),
    ("연합뉴스 경제", "https://www.yna.co.kr/rss/economy.xml"),
    ("한국경제 금융", "https://www.hankyung.com/feed/finance"),
    ("한국경제 경제", "https://www.hankyung.com/feed/economy"),
    ("전자신문 증권", "https://rss.etnews.com/Section902.xml"),
]

# 피드당 채택 상한 — 연합뉴스처럼 120건짜리 피드가 전체를 잠식하지 않게 한다.
PER_FEED_LIMIT = 25
# 발행 시각이 이보다 오래된 기사는 버린다 (직전 실행 이후 분량 + 여유).
FRESH_HOURS = 30
SUMMARY_MAX = 400

QUOTE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# 관심종목 종가 시계열 수집 기간. 레벨 컨텍스트(기간 고저·박스권) 계산에만 쓴다.
HISTORY_RANGE = "6mo"   # adjustable

# Yahoo 호출 재시도. runner에서 간헐적 DNS 실패가 실측된 적이 있어,
# 한 번 실패했다고 그날 시세를 통째로 비우지 않게 한다.
FETCH_RETRIES = 3       # adjustable — 최초 시도 뒤 재시도 횟수 (총 4회 시도)
FETCH_BACKOFF = 1.0     # adjustable — 백오프 기준 초. 1 → 2 → 4초

# 시세 시각이 수집 시각보다 이만큼 오래되면 "지연"으로 본다.
# 판정만 하고 파이프라인은 세우지 않는다 — 지연된 값이라도 없는 것보다 낫다.
STALE_THRESHOLD_HOURS = 24   # adjustable


def log(msg: str) -> None:
    print(f"[collect_stock] {msg}", file=sys.stderr)


def safe(label: str, fn, default):
    try:
        return fn()
    except Exception as e:
        log(f"WARN {label}: {e}")
        return default


def load_watchlist() -> dict:
    if not WATCHLIST_PATH.exists():
        log(f"WARN: {WATCHLIST_PATH} 없음 — 관심종목 없이 진행")
        return {"stocks": [], "indices": []}
    data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    stocks, indices = [], []
    for s in data.get("stocks", []):
        symbol, name = s.get("symbol"), s.get("name")
        if not symbol or not name:
            log(f"WARN: symbol/name 없는 watchlist 항목 skip: {s}")
            continue
        aliases = [a for a in s.get("aliases", []) if isinstance(a, str) and a.strip()]
        stocks.append({"symbol": symbol, "name": name, "aliases": aliases or [name]})
    for i in data.get("indices", []):
        if i.get("symbol") and i.get("name"):
            indices.append({"symbol": i["symbol"], "name": i["name"]})
    return {"stocks": stocks, "indices": indices}


# ---------------------------------------------------------------------------
# 시세
# ---------------------------------------------------------------------------


def fetch_chart(label: str, symbol: str, params: dict) -> dict:
    """Yahoo chart API 호출 + 지수 백오프 재시도. 전부 실패하면 마지막 예외를 올린다.

    호출부는 safe()로 감싸져 있어 여기서 예외가 나가도 그 항목만 빠진다.
    """
    import requests

    url = QUOTE_URL.format(symbol=symbol)
    last: Exception | None = None
    for attempt in range(FETCH_RETRIES + 1):
        try:
            r = requests.get(
                url, headers={"User-Agent": UA}, params=params, timeout=TIMEOUT,
            )
            r.raise_for_status()
            if attempt:
                log(f"  {label} {symbol}: {attempt}회 재시도 후 성공")
            return r.json()
        except Exception as e:      # DNS·타임아웃·5xx·JSON 파싱 전부 재시도 대상
            last = e
            if attempt == FETCH_RETRIES:
                break
            delay = FETCH_BACKOFF * (2 ** attempt)
            log(f"WARN {label} {symbol}: {e} — {delay:g}초 후 재시도 "
                f"({attempt + 1}/{FETCH_RETRIES})")
            time.sleep(delay)
    raise last if last else RuntimeError(f"{label} {symbol}: 알 수 없는 실패")


def quote_time_iso(epoch) -> str | None:
    """regularMarketTime(epoch 초) → ISO UTC. 값이 없거나 숫자가 아니면 None."""
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        return None
    return (
        datetime.datetime.fromtimestamp(float(epoch), datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def is_stale(epoch, collected_at: datetime.datetime) -> bool:
    """시세 시각이 수집 시각보다 STALE_THRESHOLD_HOURS 넘게 오래됐는가.

    시각을 못 받은 경우는 False — 모르는 것을 지연으로 단정하지 않는다.
    """
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        return False
    market_at = datetime.datetime.fromtimestamp(float(epoch), datetime.timezone.utc)
    return (collected_at - market_at).total_seconds() > STALE_THRESHOLD_HOURS * 3600


def fetch_quote(symbol: str) -> dict | None:
    """Yahoo Finance chart API에서 현재가/전일종가/변동을 뽑는다.

    range=1d 일 때 meta.chartPreviousClose 가 직전 거래일 종가다.
    장중이면 regularMarketPrice 가 현재가, 장 마감 후면 당일 종가가 된다.
    """
    payload = fetch_chart("quote", symbol, {"range": "1d", "interval": "1d"})
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        log(f"WARN quote {symbol}: 빈 result")
        return None
    m = result[0].get("meta") or {}
    price = m.get("regularMarketPrice")
    prev = m.get("chartPreviousClose") or m.get("previousClose")
    if price is None:
        log(f"WARN quote {symbol}: regularMarketPrice 없음")
        return None
    change = m.get("fulldayChange")
    change_pct = m.get("regularMarketChangePercent")
    if change is None and prev is not None:
        change = price - prev
    if change_pct is None and prev:
        change_pct = (price - prev) / prev * 100
    return {
        "symbol": symbol,
        "price": price,
        "prev_close": prev,
        "change": change,
        "change_pct": change_pct,
        "volume": m.get("regularMarketVolume"),
        "currency": m.get("currency"),
        "market_time": m.get("regularMarketTime"),
        # market_time과 같은 값을 사람이 읽을 수 있게. 기존 필드는 그대로 둔다.
        "quote_time": quote_time_iso(m.get("regularMarketTime")),
    }


def fetch_history(symbol: str) -> list[dict]:
    """일봉 종가 시계열 [{"date","close"}] (오래된 → 최신).

    timestamp와 indicators.quote[0].close 는 같은 길이의 평행 배열이고,
    휴장·거래정지 구간의 close 는 null로 온다. null은 날짜째로 버린다 —
    앞 값으로 메우면 없던 거래일을 만들어내는 셈이다.

    날짜는 거래소 타임존 기준. UTC로 찍으면 KRX 종가가 전날로 밀린다.
    """
    payload = fetch_chart(
        "history", symbol, {"range": HISTORY_RANGE, "interval": "1d"},
    )
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        log(f"WARN history {symbol}: 빈 result")
        return []
    res = result[0]
    stamps = res.get("timestamp") or []
    quote = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    closes = quote.get("close") or []

    tz_name = (res.get("meta") or {}).get("exchangeTimezoneName")
    try:
        tz = ZoneInfo(tz_name) if tz_name else datetime.timezone.utc
    except Exception:
        log(f"WARN history {symbol}: 알 수 없는 타임존 {tz_name!r} — UTC로 처리")
        tz = datetime.timezone.utc

    out = []
    for ts, close in zip(stamps, closes):
        if close is None or not isinstance(ts, (int, float)):
            continue
        date = datetime.datetime.fromtimestamp(float(ts), tz).strftime("%Y-%m-%d")
        out.append({"date": date, "close": float(close)})
    return out


def fetch_quotes(
    entries: list[dict],
    collected_at: datetime.datetime,
    *,
    with_history: bool = False,
    mark_stale: bool = False,
) -> list[dict]:
    """watchlist 항목에 시세를 붙인다. 개별 실패는 해당 항목만 drop.

    with_history=True면 종가 시계열도 함께 받는다 (관심종목 전용 — 지수는
    레벨 컨텍스트를 표시하지 않으므로 호출 횟수를 늘리지 않는다).
    시계열 수집만 실패하면 시세는 그대로 살리고 history만 빈 배열로 둔다.

    mark_stale=True면 지연 여부를 판정해 붙인다. 지수에는 붙이지 않는다 —
    해외 지수는 주말·휴일이면 정상적으로 며칠 전 종가라 오탐만 난다.
    """
    out = []
    for e in entries:
        symbol = e["symbol"]
        q = safe(f"quote/{symbol}", lambda: fetch_quote(symbol), None)
        if not q:
            continue
        item = {**q, "name": e["name"]}
        if mark_stale:
            stale = is_stale(q.get("market_time"), collected_at)
            item["stale"] = stale
            if stale:
                log(f"WARN: {e['name']}({symbol}) 시세 지연 — "
                    f"시세 {q.get('quote_time')} / 수집 "
                    f"{collected_at.strftime('%Y-%m-%dT%H:%M:%SZ')} "
                    f"(임계 {STALE_THRESHOLD_HOURS}시간)")
        if with_history:
            history = safe(f"history/{symbol}", lambda: fetch_history(symbol), [])
            log(f"  history {symbol}: {len(history)}일")
            item["history"] = history
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 뉴스
# ---------------------------------------------------------------------------


def entry_published(entry) -> datetime.datetime | None:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return None
    return datetime.datetime(*parsed[:6], tzinfo=datetime.timezone.utc)


def clean_summary(entry) -> str:
    raw = getattr(entry, "summary", "") or ""
    # description에 HTML이 섞여 오는 피드가 있어 태그만 걷어낸다
    text, in_tag = [], False
    for ch in raw:
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif not in_tag:
            text.append(ch)
    return " ".join("".join(text).split())[:SUMMARY_MAX]


def match_symbols(text: str, stocks: list[dict]) -> list[str]:
    lowered = text.lower()
    return [
        s["name"] for s in stocks
        if any(a.lower() in lowered for a in s["aliases"])
    ]


def fetch_feed(label: str, url: str, stocks: list[dict], cutoff) -> list[dict]:
    import feedparser
    import requests

    # feedparser.parse(url)은 timeout이 없어 원격이 응답을 끌면 job이 hang된다.
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    out = []
    for e in feed.entries:
        title = (getattr(e, "title", "") or "").strip()
        link = (getattr(e, "link", "") or "").strip()
        if not title or not link:
            continue
        pub = entry_published(e)
        if pub and pub < cutoff:
            continue
        summary = clean_summary(e)
        out.append({
            "title": title,
            "url": link,
            "source": label,
            "published": pub.strftime("%Y-%m-%dT%H:%M:%SZ") if pub else "",
            "summary": summary,
            "matched": match_symbols(f"{title} {summary}", stocks),
        })
        if len(out) >= PER_FEED_LIMIT:
            break
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    target_date = (
        os.environ.get("TARGET_DATE")
        or datetime.datetime.now(KST).strftime("%Y-%m-%d")
    )
    log(f"대상 날짜: {target_date}")

    # 시세 지연 판정의 기준 시각. 수집 시작 시점으로 고정해 둔다 —
    # 종목마다 다른 시각과 비교하면 같은 응답이 실행 순서에 따라 갈린다.
    collected_at = datetime.datetime.now(datetime.timezone.utc)

    watchlist = load_watchlist()
    stocks, indices = watchlist["stocks"], watchlist["indices"]
    log(f"watchlist: 종목 {len(stocks)}개, 지수 {len(indices)}개")

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=FRESH_HOURS)
    market_news = []
    for label, url in FEEDS:
        items = safe(f"feed/{label}", lambda: fetch_feed(label, url, stocks, cutoff), [])
        log(f"  {label}: {len(items)}건")
        market_news.extend(items)

    # 같은 기사가 여러 피드에 걸리는 경우 URL 기준 1건만 남긴다
    seen, deduped = set(), []
    for item in market_news:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        deduped.append(item)

    deduped.sort(key=lambda it: it["published"], reverse=True)

    data = {
        "date": target_date,
        "collected_at": collected_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "watchlist": stocks,
        "quotes": {
            "indices": fetch_quotes(indices, collected_at),
            "stocks": fetch_quotes(
                stocks, collected_at, with_history=True, mark_stale=True,
            ),
        },
        "market_news": deduped,
    }

    out_path = REPO_DIR / "inbox" / f"{target_date}-stock-raw.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    stale_count = sum(1 for q in data["quotes"]["stocks"] if q.get("stale"))
    if stale_count:
        log(f"WARN: 시세 지연 종목 {stale_count}건 — 리포트에 ⚠️로 표기된다")
    matched = sum(1 for it in deduped if it["matched"])
    log(f"저장 완료: {out_path.relative_to(REPO_DIR)}")
    log(
        f"요약: news={len(deduped)} (종목 매칭 {matched}), "
        f"indices={len(data['quotes']['indices'])}, "
        f"stocks={len(data['quotes']['stocks'])}"
    )


if __name__ == "__main__":
    main()
