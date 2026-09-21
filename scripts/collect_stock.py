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
      "stocks":  [{... 동일 + "volume"}]
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
from zoneinfo import ZoneInfo

import feedparser
import requests

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


def fetch_quote(symbol: str) -> dict | None:
    """Yahoo Finance chart API에서 현재가/전일종가/변동을 뽑는다.

    range=1d 일 때 meta.chartPreviousClose 가 직전 거래일 종가다.
    장중이면 regularMarketPrice 가 현재가, 장 마감 후면 당일 종가가 된다.
    """
    r = requests.get(
        QUOTE_URL.format(symbol=symbol),
        headers={"User-Agent": UA},
        params={"range": "1d", "interval": "1d"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    result = (r.json().get("chart") or {}).get("result") or []
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
    }


def fetch_quotes(entries: list[dict]) -> list[dict]:
    """watchlist 항목에 시세를 붙인다. 개별 실패는 해당 항목만 drop."""
    out = []
    for e in entries:
        q = safe(f"quote/{e['symbol']}", lambda: fetch_quote(e["symbol"]), None)
        if q:
            out.append({**q, "name": e["name"]})
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
        "collected_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "watchlist": stocks,
        "quotes": {
            "indices": fetch_quotes(indices),
            "stocks": fetch_quotes(stocks),
        },
        "market_news": deduped,
    }

    out_path = REPO_DIR / "inbox" / f"{target_date}-stock-raw.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    matched = sum(1 for it in deduped if it["matched"])
    log(f"저장 완료: {out_path.relative_to(REPO_DIR)}")
    log(
        f"요약: news={len(deduped)} (종목 매칭 {matched}), "
        f"indices={len(data['quotes']['indices'])}, "
        f"stocks={len(data['quotes']['stocks'])}"
    )


if __name__ == "__main__":
    main()
