#!/usr/bin/env python3
"""
stock_report.py — curate 단계의 주식 산출물을 검증·중복제거해서 archive에 반영.

입력:
  · /tmp/processed_stock.json (첫 인자) — Claude가 고른 뉴스 + 한국어 요약
      {"market_news": [{"title","summary","url","source"}],
       "stock_news":  [{"stock","title","summary","url","source"}]}
  · inbox/YYYY-MM-DD-stock-raw.json — 시세 원본
  · inbox/YYYY-MM-DD-stock-history.json — 종가 시계열 (없으면 레벨 컨텍스트·
    주간 섹션만 빠지고 나머지는 그대로 나간다)
  · inbox/YYYY-MM-DD-research.json — 애널리스트 컨센서스 + 증권사 리포트 목록
    (없으면 해당 섹션만 빠진다)

시세 숫자는 **inbox에서 직접** 읽는다. Claude 산출물의 숫자는 쓰지 않는다
(LLM이 옮겨 적는 과정에서 값이 바뀌면 리포트가 조용히 틀리기 때문).
레벨 컨텍스트(기간 고저 대비 위치·박스권)도 마찬가지로 inbox의 종가 시계열에서
직접 계산한다 — curated JSON에 같은 이름의 필드가 있어도 읽지 않는다.

이벤트 캘린더는 config/calendar.json에서 읽는다. 날짜가 "TBD"인 항목은
표시 대상에서 빠진다 — 없는 날짜를 만들어내느니 안 보여주는 쪽이 낫다.

출력:
  · archive/YYYY/MM/YYYY-MM-DD-stock.json
  · state/seen_stock_urls.json 갱신 (AI 뉴스의 seen_urls.json과 분리)

git/네트워크는 만지지 않는다. 순수한 파일 처리.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from level_context import (  # noqa: E402
    WEEK_DAYS, closes_of, compute_level_context, compute_weekly_stats,
)

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_REPO = SCRIPT_DIR.parent
REPO_DIR = pathlib.Path(os.environ.get("AINEWS_REPO", str(DEFAULT_REPO))).resolve()

STATE_DIR = REPO_DIR / "state"
ARCHIVE_DIR = REPO_DIR / "archive"
SEEN_STOCK_PATH = STATE_DIR / "seen_stock_urls.json"
INVESTOR_PATH = STATE_DIR / "investor_trend.json"
CALENDAR_PATH = REPO_DIR / "config" / "calendar.json"

# 오늘 기준 며칠 앞까지의 이벤트를 리포트 상단에 띄울지
CALENDAR_LOOKAHEAD_DAYS = 14   # adjustable

# 주간 심화 섹션을 붙이는 요일 (KST 기준, 리포트 대상 날짜로 판정)
WEEKLY_DAY = "Monday"          # adjustable
# %A는 로케일을 타므로 (runner 로케일이 C가 아니면 한국어가 나온다) 고정 목록을 쓴다
WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday")

# 리포트에 싣는 투자자 동향 일수 (collect_investor.py는 더 길게 누적한다)
INVESTOR_DAYS = 5

KST = ZoneInfo("Asia/Seoul")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "fbclid", "gclid", "ref", "ref_src",
    "mc_cid", "mc_eid", "_ga", "_gl", "igshid", "yclid", "msclkid",
    "spm", "share_source", "share_medium",
}

MARKET_MAX = 10
PER_STOCK_MAX = 3
TITLE_MAX = 300
SUMMARY_MAX = 1000
SOURCE_MAX = 60


def log(msg: str) -> None:
    print(f"[stock_report] {msg}", file=sys.stderr)


def normalize_url(url: str) -> str:
    p = urlparse(url.strip())
    scheme = (p.scheme or "https").lower()
    netloc = p.netloc.lower()
    path = p.path.rstrip("/") or "/"
    pairs = [
        (k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    return urlunparse((scheme, netloc, path, "", urlencode(pairs), ""))


def clean_text(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s[:limit] if s else None


def clean_http_url(value) -> str | None:
    """http/https만 통과. javascript:·data:·프로토콜 상대 URL은 None."""
    if not isinstance(value, str):
        return None
    u = value.strip()
    p = urlparse(u)
    return u if p.scheme in ("http", "https") and p.netloc else None


def load_json(path: pathlib.Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            log(f"WARN: {path} 파싱 실패: {e} — default 사용")
    return default


def save_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# --- 입력 검증 --------------------------------------------------------------
# curate 단계의 Claude는 제3자가 쓴 RSS 제목·요약을 읽는다. 주입이 성공해도
# 피해가 여기서 멈추도록 타입·길이·URL 스킴을 결정론적으로 강제한다.


def clean_item(raw, valid_names: set | None) -> dict | None:
    if not isinstance(raw, dict):
        log("WARN: 항목이 object가 아님 — drop")
        return None
    title = clean_text(raw.get("title"), TITLE_MAX)
    if not title:
        log(f"WARN: title 누락/비문자열 — drop: {raw.get('url')!r}")
        return None
    url = clean_http_url(raw.get("url"))
    if not url:
        log(f"WARN: url이 http(s)가 아님 — drop: {title!r} / {raw.get('url')!r}")
        return None
    item = {
        "title": title,
        "summary": clean_text(raw.get("summary"), SUMMARY_MAX) or "",
        "url": url,
        "source": clean_text(raw.get("source"), SOURCE_MAX) or "",
    }
    if valid_names is not None:
        stock = clean_text(raw.get("stock"), TITLE_MAX)
        if stock not in valid_names:
            log(f"WARN: watchlist에 없는 stock {stock!r} — drop: {title!r}")
            return None
        item["stock"] = stock
    return item


def validate_input(raw, valid_names: set) -> dict:
    if not isinstance(raw, dict):
        log("WARN: 입력이 JSON object가 아님 — 전량 drop")
        return {"market_news": [], "stock_news": []}

    market = []
    for r in (raw.get("market_news") if isinstance(raw.get("market_news"), list) else []):
        item = clean_item(r, None)
        if not item:
            continue
        if len(market) >= MARKET_MAX:
            log(f"WARN: market_news 최대 {MARKET_MAX}건 초과 — drop: {item['title']!r}")
            continue
        market.append(item)

    stock = []
    for r in (raw.get("stock_news") if isinstance(raw.get("stock_news"), list) else []):
        item = clean_item(r, valid_names)
        if item:
            stock.append(item)

    return {"market_news": market, "stock_news": stock}


# --- 처리 -------------------------------------------------------------------


def filter_new(items: list[dict], seen: set) -> tuple[list[dict], int]:
    """이미 전송한 URL을 걷어낸다. 같은 실행 안에서의 중복은 허용
    (시장 전반 뉴스와 종목 뉴스가 겹치는 것은 자연스럽다)."""
    fresh, dup = [], 0
    for item in items:
        n = normalize_url(item["url"])
        if n in seen:
            dup += 1
            continue
        item["_normalized_url"] = n
        fresh.append(item)
    return fresh, dup


def group_by_stock(items: list[dict], watchlist: list[dict]) -> dict[str, list]:
    """watchlist 순서를 유지한 종목별 뉴스 묶음. 뉴스가 없는 종목도 빈 배열로 남긴다."""
    grouped: dict[str, list] = {s["name"]: [] for s in watchlist}
    for item in items:
        bucket = grouped.get(item["stock"])
        if bucket is None:
            continue
        if len(bucket) >= PER_STOCK_MAX:
            log(f"WARN: {item['stock']} 최대 {PER_STOCK_MAX}건 초과 — drop: {item['title']!r}")
            continue
        bucket.append({k: v for k, v in item.items() if k not in ("stock",)})
    return grouped


def strip_private(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def load_histories(today: str) -> dict[str, list]:
    """inbox의 종가 시계열 파일을 {symbol: [{"date","close"}]}로 읽는다.

    파일이 없어도 에러가 아니다 — 레벨 컨텍스트와 주간 섹션만 빠진다.
    (시계열은 뉴스 선정에 쓰지 않으므로 curate가 읽는 raw 파일과 분리돼 있다.)
    """
    path = REPO_DIR / "inbox" / f"{today}-stock-history.json"
    raw = load_json(path, None)
    if raw is None:
        log(f"종가 시계열 없음: {path.name} — 레벨 컨텍스트·주간 섹션 생략")
        return {}
    series = raw.get("history") if isinstance(raw, dict) else None
    if not isinstance(series, dict):
        log(f"WARN: {path.name}의 history가 object가 아님 — 무시")
        return {}
    return {k: v for k, v in series.items() if isinstance(v, list)}


def load_research(today: str) -> dict:
    """inbox의 컨센서스·리포트 파일. 없으면 빈 dict — 해당 섹션만 빠진다."""
    path = REPO_DIR / "inbox" / f"{today}-research.json"
    raw = load_json(path, None)
    if raw is None:
        log(f"컨센서스·리포트 없음: {path.name} — 해당 섹션 생략")
        return {}
    if not isinstance(raw, dict):
        log(f"WARN: {path.name}이 object가 아님 — 무시")
        return {}
    consensus = raw.get("consensus")
    reports = raw.get("reports")
    return {
        "consensus": consensus if isinstance(consensus, dict) else {},
        "reports": reports if isinstance(reports, dict) else {},
    }


def group_reports(reports: dict, watchlist: list[dict]) -> dict[str, list]:
    """symbol로 들어온 리포트를 watchlist 순서의 종목명 묶음으로 바꾼다.

    stock_news와 같은 모양이라 렌더 쪽에서 똑같이 다룰 수 있다.
    리포트가 없는 종목은 키를 만들지 않는다 (뉴스와 달리 빈 칸이 정상이다 —
    관심종목 리포트는 실적 시즌에만 나온다).
    """
    out = {}
    for s in watchlist:
        items = reports.get(s["symbol"])
        if not isinstance(items, list) or not items:
            continue
        cleaned = []
        for r in items:
            if not isinstance(r, dict):
                continue
            title = clean_text(r.get("title"), TITLE_MAX)
            url = clean_http_url(r.get("url"))
            if not title or not url:
                continue
            cleaned.append({
                "date": clean_text(r.get("date"), 10) or "",
                "title": title,
                "url": url,
                "target_price": clean_text(r.get("target_price"), 32),
                "opinion": clean_text(r.get("opinion"), 32),
                "analyst": clean_text(r.get("analyst"), SOURCE_MAX),
                "broker": clean_text(r.get("broker"), SOURCE_MAX),
            })
        if cleaned:
            out[s["name"]] = cleaned
    return out


def with_level_context(stocks: list[dict], histories: dict[str, list],
                       consensus: dict | None = None) -> list[dict]:
    """종가 시계열로 종목별 level_context를 계산하고, 컨센서스를 붙인다.

    history 자체는 archive에 싣지 않는다 (6개월 × 종목 수를 매일 커밋하면
    저장소만 불어난다). 계산 결과만 남기고 원본은 inbox에 그대로 둔다.
    """
    consensus = consensus or {}
    out = []
    for q in stocks:
        if not isinstance(q, dict):
            continue
        # 두 필드 모두 수집·계산 결과로만 존재한다 — 원본에 같은 이름이 있어도 버린다
        item = {
            k: v for k, v in q.items()
            if k not in ("history", "level_context", "consensus")
        }
        ctx = compute_level_context(closes_of(histories.get(q.get("symbol"))))
        if ctx:
            item["level_context"] = ctx
        else:
            log(f"WARN: {q.get('name', q.get('symbol'))} 종가 시계열 없음 — level_context 생략")
        c = consensus.get(q.get("symbol"))
        if isinstance(c, dict) and c:
            item["consensus"] = c
        out.append(item)
    return out


# --- 이벤트 캘린더 ----------------------------------------------------------


def load_calendar() -> list[dict]:
    """config/calendar.json에서 **날짜가 확정된** 이벤트만 읽는다.

    "TBD"처럼 날짜가 비어 있는 항목은 여기서 걸러진다. 임의로 날짜를
    만들어 붙이지 않는다 — 틀린 D-day는 아예 없느니만 못하다.
    """
    raw = load_json(CALENDAR_PATH, [])
    if isinstance(raw, dict):            # {"events": [...]} 형태도 받아준다
        raw = raw.get("events") or []
    if not isinstance(raw, list):
        log(f"WARN: {CALENDAR_PATH.name}이 배열이 아님 — 캘린더 생략")
        return []

    out, pending = [], 0
    for e in raw:
        if not isinstance(e, dict) or "_comment" in e:
            continue
        label = clean_text(e.get("label"), TITLE_MAX)
        if not label:
            log(f"WARN: label 없는 캘린더 항목 skip: {e}")
            continue
        date = (e.get("date") or "").strip() if isinstance(e.get("date"), str) else ""
        if not DATE_RE.match(date):
            pending += 1
            continue
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            log(f"WARN: 존재하지 않는 날짜 {date!r} — skip: {label!r}")
            continue
        item = {"date": date, "label": label}
        ticker = clean_text(e.get("ticker"), SOURCE_MAX)
        if ticker:
            item["ticker"] = ticker
        out.append(item)

    if pending:
        log(f"캘린더: 날짜 미정(TBD) {pending}건은 표시하지 않는다")
    return out


def upcoming_events(events: list[dict], today: str,
                    lookahead: int = CALENDAR_LOOKAHEAD_DAYS) -> list[dict]:
    """오늘~lookahead일 이내 이벤트를 D-day 오름차순으로. 지난 이벤트는 뺀다."""
    base = datetime.date.fromisoformat(today)
    out = []
    for e in events:
        delta = (datetime.date.fromisoformat(e["date"]) - base).days
        if 0 <= delta <= lookahead:
            out.append({**e, "d_day": delta})
    out.sort(key=lambda e: (e["d_day"], e["label"]))
    return out


def past_events(events: list[dict], today: str, days: int) -> list[dict]:
    """최근 days일 안에 지나간 이벤트를 최신순으로 (주간 섹션용)."""
    base = datetime.date.fromisoformat(today)
    out = []
    for e in events:
        delta = (base - datetime.date.fromisoformat(e["date"])).days
        if 0 < delta <= days:
            out.append({**e, "days_ago": delta})
    out.sort(key=lambda e: (e["days_ago"], e["label"]))
    return out


# --- 주간 심화 섹션 ---------------------------------------------------------


def is_weekly_day(today: str) -> bool:
    """리포트 대상 날짜(KST)가 WEEKLY_DAY인가."""
    return WEEKDAY_NAMES[datetime.date.fromisoformat(today).weekday()] == WEEKLY_DAY


def build_weekly(stocks: list[dict], histories: dict[str, list],
                 today: str, events: list[dict]) -> dict | None:
    """주간 변동률·고저와 레벨 컨텍스트의 전주 대비 변화. 전부 계산치다.

    WEEKLY_DAY가 아니면 None — 섹션 자체가 붙지 않는다.
    """
    if not is_weekly_day(today):
        return None

    rows = []
    for q in stocks:
        if not isinstance(q, dict):
            continue
        stats = compute_weekly_stats(histories.get(q.get("symbol")), today)
        if not stats:
            continue
        rows.append({
            "name": q.get("name") or q.get("symbol"),
            "symbol": q.get("symbol"),
            "currency": q.get("currency"),
            **stats,
        })

    past = past_events(events, today, WEEK_DAYS)
    if not rows and not past:
        log("주간 섹션: 실을 내용이 없음 — 생략")
        return None

    log(f"주간 섹션: 종목 {len(rows)}건, 지난 이벤트 {len(past)}건")
    return {
        "day": WEEKLY_DAY,
        "days": WEEK_DAYS,
        "stocks": rows,
        "past_events": past,
    }


def recent_investor_trend(days: int = INVESTOR_DAYS) -> dict:
    """누적 state에서 최근 N영업일을 잘라 리포트용 구조로 만든다.

    collect_investor.py가 하루치씩 쌓아둔 것을 [대상 → 지표 → 투자자] 로 뒤집는다.
    파일이 없거나 비어 있으면 빈 dict — 투자자 수급 섹션만 빠진다.

    예전 스키마(투자자별로 순매수 숫자 하나만 저장하던 형태)도 읽는다.
    그 날짜는 순매수만 채워지고 매수·매도는 결측으로 남는다.

    반환:
      {"dates": ["2026-09-17", ...],                  # 오래된 → 최신
       "series": {"삼성전자": {"순매수": {"외국인": [n, ...]}}}}
                                                       # dates와 같은 길이, 결측은 None
    """
    state = load_json(INVESTOR_PATH, {"days": {}})
    all_days = state.get("days") or {}
    if not isinstance(all_days, dict) or not all_days:
        return {}

    dates = sorted(all_days)[-days:]

    def cell(date: str, key: str, who: str, measure: str):
        """하루치에서 한 칸을 꺼낸다. 없거나 형식이 다르면 None."""
        values = ((all_days.get(date) or {}).get(key) or {}).get(who)
        if isinstance(values, dict):
            v = values.get(measure)
        elif measure == "순매수":
            v = values          # 예전 스키마: 숫자 하나 = 순매수
        else:
            v = None
        return v if isinstance(v, (int, float)) else None

    keys = []
    for d in dates:
        for key in (all_days.get(d) or {}):
            if key not in keys:
                keys.append(key)

    series = {}
    for key in keys:
        by_measure = {}
        for measure in ("순매수", "매수", "매도"):
            by_investor = {}
            for who in ("외국인", "개인", "기관"):
                values = [cell(d, key, who, measure) for d in dates]
                if any(v is not None for v in values):
                    by_investor[who] = values
            if by_investor:
                by_measure[measure] = by_investor
        if by_measure:
            series[key] = by_measure

    return {"dates": dates, "series": series} if series else {}


def resolve_report_date(arg_date: str | None) -> str:
    raw = (arg_date
           or os.environ.get("TARGET_DATE")
           or datetime.datetime.now(KST).strftime("%Y-%m-%d")).strip()
    if not DATE_RE.match(raw):
        log(f"ERROR: 잘못된 날짜 형식 {raw!r} — YYYY-MM-DD 필요")
        raise SystemExit(1)
    return raw


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="주식 큐레이션 결과를 archive/state에 반영")
    p.add_argument("input", nargs="?", default=None,
                   help="처리 결과 JSON 경로 (생략 시 stdin)")
    p.add_argument("--date", default=None,
                   help="리포트 대상 날짜 YYYY-MM-DD (생략 시 TARGET_DATE env → KST 오늘)")
    return p.parse_args()


def load_input(input_path: str | None) -> dict:
    if input_path:
        return json.loads(pathlib.Path(input_path).read_text(encoding="utf-8"))
    data = sys.stdin.read()
    if not data.strip():
        log("ERROR: 입력 없음")
        raise SystemExit(1)
    return json.loads(data)


def main() -> None:
    args = parse_args()
    today = resolve_report_date(args.date)
    log(f"대상 날짜 (KST): {today}")

    inbox_path = REPO_DIR / "inbox" / f"{today}-stock-raw.json"
    inbox = load_json(inbox_path, None)
    if inbox is None:
        log(f"ERROR: 시세 원본이 없습니다: {inbox_path}")
        raise SystemExit(1)

    watchlist = inbox.get("watchlist") or []
    raw_quotes = inbox.get("quotes") or {"indices": [], "stocks": []}
    histories = load_histories(today)
    research = load_research(today)
    quotes = {
        "indices": raw_quotes.get("indices") or [],
        "stocks": with_level_context(
            raw_quotes.get("stocks") or [], histories, research.get("consensus"),
        ),
    }
    research_reports = group_reports(research.get("reports") or {}, watchlist)
    if research_reports:
        log(f"증권사 리포트: {sum(len(v) for v in research_reports.values())}건 "
            f"({len(research_reports)}개 종목)")
    valid_names = {s["name"] for s in watchlist}

    collected = validate_input(load_input(args.input), valid_names)
    log(
        f"입력(검증 후): 시장 {len(collected['market_news'])}건, "
        f"종목 {len(collected['stock_news'])}건"
    )

    seen = set(load_json(SEEN_STOCK_PATH, {"urls": []}).get("urls", []))
    log(f"기존 state: URL {len(seen)}건")

    market_new, market_dup = filter_new(collected["market_news"], seen)
    stock_new, stock_dup = filter_new(collected["stock_news"], seen)
    grouped = group_by_stock(stock_new, watchlist)
    log(
        f"필터 후: 시장 {len(market_new)}건 (중복 {market_dup}), "
        f"종목 {sum(len(v) for v in grouped.values())}건 (중복 {stock_dup})"
    )

    calendar_all = load_calendar()
    calendar = upcoming_events(calendar_all, today)
    if calendar:
        log(f"캘린더: {CALENDAR_LOOKAHEAD_DAYS}일 이내 이벤트 {len(calendar)}건")
    else:
        log("캘린더: 표시할 이벤트 없음 — 해당 섹션은 생략된다")

    weekly = build_weekly(
        raw_quotes.get("stocks") or [], histories, today, calendar_all,
    )

    year, month = today[:4], today[5:7]
    out_path = ARCHIVE_DIR / year / month / f"{today}-stock.json"
    investor = recent_investor_trend()
    if investor:
        log(f"투자자 동향: {len(investor['dates'])}일 × {len(investor['series'])}개 대상")
    else:
        log("투자자 동향 데이터 없음 — 해당 섹션은 생략된다")

    report = {
        "date": today,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "quotes": quotes,
        "calendar": calendar,
        "research_reports": research_reports,
        "investor_trend": investor,
        "market_news": [strip_private(a) for a in market_new],
        "stock_news": {
            name: [strip_private(a) for a in items]
            for name, items in grouped.items()
        },
        "duplicate_counts": {"market": market_dup, "stock": stock_dup},
    }
    if weekly:
        report["weekly"] = weekly
    save_json(out_path, report)
    log(f"archive: {out_path.relative_to(REPO_DIR)}")

    for item in market_new:
        seen.add(item["_normalized_url"])
    for items in grouped.values():
        for item in items:
            seen.add(normalize_url(item["url"]))
    save_json(SEEN_STOCK_PATH, {"urls": sorted(seen)})

    print(json.dumps({
        "date": today,
        "market_count": len(market_new),
        "stock_count": sum(len(v) for v in grouped.values()),
        "archive_json": str(out_path.relative_to(REPO_DIR)),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
