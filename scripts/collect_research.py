#!/usr/bin/env python3
"""
collect_research.py — 애널리스트 컨센서스 + 증권사 리포트 목록 수집.

두 소스를 모은다.

  1. Yahoo Finance (yfinance) — 관심종목의 목표주가 컨센서스와 투자의견 분포.
     collect_stock.py가 쓰는 chart API와 달리 쿠키+crumb 인증이 필요해
     직접 호출하면 401이 난다. yfinance가 그 과정을 대신한다.

  2. 한경 컨센서스 — 증권사 리포트 목록 (제목·목표가·투자의견·애널리스트·
     증권사·PDF 링크). 관심종목 리포트는 실적 시즌에 몰려서 나온다.
     실측: 60일 600건 중 관심종목은 4건. 그래서 매 실행 최근 며칠만 긁어
     state/research_reports.json 에 **누적**하고, 리포트는 거기서 읽는다.
     (collect_investor.py의 누적 방식과 같다.)

컨센서스는 그날 스냅샷이라 "목표가가 올라갔는지"를 알 수 없다. 하루치씩
state/consensus_trend.json 에 쌓아두고, 주간 섹션이 거기서 전주 값을 읽는다.

출력:
  · inbox/YYYY-MM-DD-research.json
  · state/research_reports.json 갱신
  · state/consensus_trend.json 갱신

이 스크립트는 실패해도 종료 코드 0이다. 주식 리포트 전체를 막지 않는다 —
컨센서스·리포트 섹션만 빠지고 시세·뉴스는 그대로 나간다.
"""
from __future__ import annotations

import datetime
import html
import json
import os
import pathlib
import re
import sys
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
WATCHLIST_PATH = REPO_DIR / "config" / "watchlist.json"
STATE_PATH = REPO_DIR / "state" / "research_reports.json"
TREND_PATH = REPO_DIR / "state" / "consensus_trend.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TIMEOUT = 30

# --- 조정 가능한 기본값 -----------------------------------------------------
RESEARCH_LOOKBACK_DAYS = 7    # adjustable — 매 실행 조회할 리포트 발행 기간
BACKFILL_DAYS = 180           # adjustable — state가 비었을 때 1회만 거슬러 올라가는 기간
MAX_PAGES = 20                # 백필 페이지 상한 (안전장치)
REPORTS_PER_STOCK = 3         # adjustable — 리포트에 싣는 종목당 최근 건수
REPORT_KEEP_DAYS = 180        # adjustable — 리포트를 state에 남겨두는 기간
TREND_KEEP_DAYS = 180         # adjustable — 컨센서스 추이를 남겨두는 기간
PAGE_SIZE = 100               # 한 번에 받는 행 수. 7일치가 1요청에 들어온다.

CONSENSUS_URL = "http://consensus.hankyung.com/analysis/list"
PDF_URL = "http://consensus.hankyung.com/analysis/downpdf?report_idx={idx}"

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
IDX_RE = re.compile(r"downpdf\?report_idx=(\d+)")
CODE_RE = re.compile(r"business_code=(\d+)")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def log(msg: str) -> None:
    print(f"[collect_research] {msg}", file=sys.stderr)


def safe(label: str, fn, default):
    try:
        return fn()
    except Exception as e:
        log(f"WARN {label}: {type(e).__name__}: {e}")
        return default


def load_json(path: pathlib.Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            log(f"WARN: {path.name} 파싱 실패: {e} — default 사용")
    return default


def save_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def ticker_of(symbol: str) -> str:
    """Yahoo 티커에서 KRX 6자리 코드만. 005930.KS → 005930"""
    return symbol.split(".")[0]


def load_watchlist() -> list[dict]:
    data = load_json(WATCHLIST_PATH, {})
    out = []
    for s in data.get("stocks", []) if isinstance(data, dict) else []:
        if s.get("symbol") and s.get("name"):
            out.append({"symbol": s["symbol"], "name": s["name"]})
    return out


# ---------------------------------------------------------------------------
# 1. Yahoo 컨센서스
# ---------------------------------------------------------------------------


def _num(value):
    """숫자만 통과. bool·NaN·문자열은 None."""
    import math
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if math.isfinite(f) else None


def fetch_consensus(symbol: str) -> dict | None:
    """목표주가 컨센서스와 투자의견 분포.

    국내 종목은 upgrades_downgrades(개별 증권사 상향·하향 이력)가 404다.
    해외 종목에만 있어서 아예 받지 않는다.
    """
    import yfinance as yf

    t = yf.Ticker(symbol)
    out = {}

    targets = safe(f"targets/{symbol}", lambda: t.analyst_price_targets, None)
    if isinstance(targets, dict) and targets:
        cleaned = {k: _num(v) for k, v in targets.items()}
        cleaned = {k: v for k, v in cleaned.items() if v is not None}
        if cleaned:
            out["price_targets"] = cleaned

    recs = safe(f"recommendations/{symbol}", lambda: t.recommendations, None)
    if recs is not None and len(recs):
        rows = []
        for rec in recs.to_dict("records"):
            row = {"period": str(rec.get("period", ""))}
            for k in ("strongBuy", "buy", "hold", "sell", "strongSell"):
                v = _num(rec.get(k))
                if v is not None:
                    row[k] = int(v)
            if len(row) > 1:
                rows.append(row)
        if rows:
            out["recommendations"] = rows

    return out or None


def trend_snapshot(consensus: dict) -> dict | None:
    """추이에 쌓을 최소 필드만 추린다.

    목표가 전 구간(high/low/median)과 의견 분포를 매일 저장하면 state가 금방
    커진다. 변화를 읽는 데 필요한 평균·중앙값과 의견 합계만 남긴다.
    """
    out = {}
    targets = consensus.get("price_targets") or {}
    for key in ("mean", "median"):
        v = _num(targets.get(key))
        if v is not None:
            out[key] = v

    recs = consensus.get("recommendations") or []
    latest = recs[0] if recs and isinstance(recs[0], dict) else None
    if latest:
        def n(key):
            v = _num(latest.get(key))
            return int(v) if v is not None else 0
        buy = n("strongBuy") + n("buy")
        hold = n("hold")
        sell = n("sell") + n("strongSell")
        if buy or hold or sell:
            out["buy"], out["hold"], out["sell"] = buy, hold, sell
    return out or None


def merge_trend(trend: dict, date: str, consensus: dict) -> int:
    """하루치 컨센서스를 추이에 넣는다. 같은 날 재실행은 덮어쓴다."""
    days = trend.setdefault("days", {})
    day = days.setdefault(date, {})
    added = 0
    for symbol, data in consensus.items():
        snap = trend_snapshot(data)
        if snap:
            day[symbol] = snap
            added += 1
    if not day:
        days.pop(date, None)
    return added


def prune_trend(trend: dict, today: str, keep_days: int = TREND_KEEP_DAYS) -> None:
    cutoff = (datetime.date.fromisoformat(today)
              - datetime.timedelta(days=keep_days)).isoformat()
    days = trend.get("days") or {}
    trend["days"] = {d: v for d, v in days.items() if d >= cutoff}


# ---------------------------------------------------------------------------
# 2. 한경 컨센서스
# ---------------------------------------------------------------------------


def cell_text(raw: str) -> str:
    return " ".join(html.unescape(TAG_RE.sub(" ", raw)).split())


def parse_reports(page: str) -> list[dict]:
    """리포트 목록 표를 행 단위로 뜯는다.

    열 순서: 작성일 / 제목 / 적정가격 / 투자의견 / 작성자 / 제공출처 / …
    종목코드는 차트 링크의 business_code에서 꺼낸다 — 제목의 "(005930)"보다
    안정적이다 (제목 표기가 매번 같다는 보장이 없다).
    """
    out = []
    for row in ROW_RE.findall(page):
        cells = CELL_RE.findall(row)
        if len(cells) < 6:
            continue          # 헤더 행이나 레이아웃용 행
        date = cell_text(cells[0])
        if not DATE_RE.match(date):
            continue
        code = CODE_RE.search(row)
        idx = IDX_RE.search(row)
        if not code or not idx:
            continue          # 종목·원문을 특정할 수 없으면 버린다
        # 제목 칸에는 마우스오버 팝업 div가 같이 들어 있어 본문만 잘라낸다
        title = cell_text(cells[1].split("<div")[0])
        if not title:
            continue
        out.append({
            "code": code.group(1),
            "report_idx": idx.group(1),
            "date": date,
            "title": title,
            "target_price": cell_text(cells[2]) or None,
            "opinion": cell_text(cells[3]) or None,
            "analyst": cell_text(cells[4]) or None,
            "broker": cell_text(cells[5]) or None,
            "url": PDF_URL.format(idx=idx.group(1)),
        })
    return out


def fetch_reports(today: str, days: int = RESEARCH_LOOKBACK_DAYS,
                  max_pages: int = 1) -> list[dict]:
    """최근 days일 발행분. 평소에는 1요청으로 끝난다 (7일치 ≈ 60건 < PAGE_SIZE).

    max_pages를 올리면 새 행이 안 나올 때까지 페이지를 넘긴다 — 첫 실행 백필용.
    """
    import requests

    end = datetime.date.fromisoformat(today)
    start = end - datetime.timedelta(days=days)
    seen, out = set(), []
    for page in range(1, max_pages + 1):
        r = requests.get(
            CONSENSUS_URL,
            params={
                "sdate": start.isoformat(),
                "edate": end.isoformat(),
                "report_type": "CO",        # 기업 리포트 (산업·시황 제외)
                "pagenum": PAGE_SIZE,
                "now_page": page,
            },
            headers={"User-Agent": UA},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        rows = parse_reports(r.text)
        new_rows = [e for e in rows if e["report_idx"] not in seen]
        if not new_rows:
            break     # 마지막 페이지를 넘으면 같은 내용이 되돌아온다
        seen.update(e["report_idx"] for e in new_rows)
        out.extend(new_rows)
    return out


def merge_reports(state: dict, fresh: list[dict], codes: set[str]) -> int:
    """관심종목 리포트만 state에 누적. report_idx 기준으로 중복 제거."""
    store = state.setdefault("reports", {})
    added = 0
    for item in fresh:
        code = item["code"]
        if code not in codes:
            continue
        bucket = store.setdefault(code, [])
        if any(e.get("report_idx") == item["report_idx"] for e in bucket):
            continue
        bucket.append({k: v for k, v in item.items() if k != "code"})
        added += 1
    for code, bucket in store.items():
        bucket.sort(key=lambda e: (e.get("date", ""), e.get("report_idx", "")), reverse=True)
    return added


def prune_reports(state: dict, today: str, keep_days: int = REPORT_KEEP_DAYS) -> None:
    """오래된 리포트를 잘라낸다. 종목당 최소 REPORTS_PER_STOCK건은 남긴다 —
    실적 시즌 사이에는 몇 달째 새 리포트가 없는 종목이 생긴다."""
    cutoff = (datetime.date.fromisoformat(today)
              - datetime.timedelta(days=keep_days)).isoformat()
    for code, bucket in (state.get("reports") or {}).items():
        kept = [e for e in bucket if e.get("date", "") >= cutoff]
        state["reports"][code] = kept if kept else bucket[:REPORTS_PER_STOCK]


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
    if not watchlist:
        log("watchlist 종목 없음 — 수집할 것이 없다")
    codes = {ticker_of(s["symbol"]): s["symbol"] for s in watchlist}

    # --- 1. Yahoo 컨센서스 ---
    consensus = {}
    for s in watchlist:
        data = safe(f"consensus/{s['symbol']}", lambda: fetch_consensus(s["symbol"]), None)
        if data:
            consensus[s["symbol"]] = data
            targets = data.get("price_targets") or {}
            log(f"  컨센서스 {s['name']}: 목표가 평균 {targets.get('mean')}, "
                f"의견 {len(data.get('recommendations') or [])}기간")
        else:
            log(f"  컨센서스 {s['name']}: 없음")

    trend = load_json(TREND_PATH, {"days": {}})
    if not isinstance(trend, dict) or not isinstance(trend.get("days"), dict):
        log("WARN: 컨센서스 추이 state 형식이 다름 — 새로 만든다")
        trend = {"days": {}}
    merged = merge_trend(trend, target_date, consensus)
    prune_trend(trend, target_date)
    save_json(TREND_PATH, trend)
    log(f"  컨센서스 추이: {merged}종목 기록, 누적 {len(trend['days'])}일")

    # --- 2. 한경 리포트 (누적) ---
    state = load_json(STATE_PATH, {"reports": {}})
    if not isinstance(state, dict) or not isinstance(state.get("reports"), dict):
        log("WARN: state 형식이 다름 — 새로 만든다")
        state = {"reports": {}}

    # 누적분이 하나도 없으면(첫 실행) 한 번만 거슬러 올라간다. 관심종목 리포트는
    # 실적 시즌에 몰려 나와서, 7일 창만 보면 몇 달째 빈 섹션이 된다.
    first_run = not (state.get("reports") or {})
    days = BACKFILL_DAYS if first_run else RESEARCH_LOOKBACK_DAYS
    pages = MAX_PAGES if first_run else 1
    if first_run:
        log(f"  누적분 없음 — {BACKFILL_DAYS}일 백필 (최대 {MAX_PAGES}페이지)")
    fresh = safe("hankyung", lambda: fetch_reports(target_date, days, pages), [])
    log(f"  한경 컨센서스: 최근 {days}일 {len(fresh)}건 조회")
    added = merge_reports(state, fresh, set(codes))
    prune_reports(state, target_date)
    log(f"  관심종목 신규 {added}건 누적")
    save_json(STATE_PATH, state)

    reports = {}
    for code, symbol in codes.items():
        bucket = (state.get("reports") or {}).get(code) or []
        if bucket:
            reports[symbol] = bucket[:REPORTS_PER_STOCK]

    out_path = REPO_DIR / "inbox" / f"{target_date}-research.json"
    save_json(out_path, {
        "date": target_date,
        "collected_at": datetime.datetime.now(datetime.timezone.utc)
                                 .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "consensus": consensus,
        "reports": reports,
    })
    log(f"저장 완료: {out_path.relative_to(REPO_DIR)}")
    log(f"요약: 컨센서스 {len(consensus)}종목, "
        f"리포트 {sum(len(v) for v in reports.values())}건")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 이 스크립트가 주식 리포트 전체를 막지 않는다
        log(f"ERROR: {type(e).__name__}: {e} — 컨센서스·리포트 섹션 없이 진행")
