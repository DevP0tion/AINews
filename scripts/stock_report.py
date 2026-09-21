#!/usr/bin/env python3
"""
stock_report.py — curate 단계의 주식 산출물을 검증·중복제거해서 archive에 반영.

입력:
  · /tmp/processed_stock.json (첫 인자) — Claude가 고른 뉴스 + 한국어 요약
      {"market_news": [{"title","summary","url","source"}],
       "stock_news":  [{"stock","title","summary","url","source"}]}
  · inbox/YYYY-MM-DD-stock-raw.json — 시세 원본

시세 숫자는 **inbox에서 직접** 읽는다. Claude 산출물의 숫자는 쓰지 않는다
(LLM이 옮겨 적는 과정에서 값이 바뀌면 리포트가 조용히 틀리기 때문).

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

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_REPO = SCRIPT_DIR.parent
REPO_DIR = pathlib.Path(os.environ.get("AINEWS_REPO", str(DEFAULT_REPO))).resolve()

STATE_DIR = REPO_DIR / "state"
ARCHIVE_DIR = REPO_DIR / "archive"
SEEN_STOCK_PATH = STATE_DIR / "seen_stock_urls.json"

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
    quotes = inbox.get("quotes") or {"indices": [], "stocks": []}
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

    year, month = today[:4], today[5:7]
    out_path = ARCHIVE_DIR / year / month / f"{today}-stock.json"
    save_json(out_path, {
        "date": today,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "quotes": quotes,
        "market_news": [strip_private(a) for a in market_new],
        "stock_news": {
            name: [strip_private(a) for a in items]
            for name, items in grouped.items()
        },
        "duplicate_counts": {"market": market_dup, "stock": stock_dup},
    })
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
