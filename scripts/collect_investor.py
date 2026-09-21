#!/usr/bin/env python3
"""
collect_investor.py — 투자자별(외국인/개인/기관) 순매수를 KRX에서 하루치 수집.

매 실행마다 **직전 영업일 하루치**만 가져와 state/investor_trend.json에 누적한다.
리포트는 이 누적분에서 최근 5영업일을 읽어 선 그래프로 보여준다 (stock_report.py).

대상은 watchlist의 **종목만**이다. 시장(코스피/코스닥) 집계는 받지 않는다.
투자자 구분(외국인/개인/기관)마다 매수·매도·순매수 세 값을 저장한다 —
"순매수 +100억"이 1조 매수/9,900억 매도인지 100억 매수뿐인지 구분하기 위해서다.

인증:
  KRX 정보데이터시스템이 로그인을 요구하도록 바뀌어 pykrx가 KRX_ID/KRX_PW
  환경변수로 세션을 만든다. 둘 중 하나라도 없으면 **조용히 건너뛴다** —
  투자자 동향만 빠지고 시세·뉴스 수집은 그대로 진행되어야 하기 때문이다.

이 스크립트는 실패해도 종료 코드 0이다. 주식 리포트 전체를 막지 않는다.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
WATCHLIST_PATH = REPO_DIR / "config" / "watchlist.json"
STATE_PATH = REPO_DIR / "state" / "investor_trend.json"

# 누적 보관 일수. 리포트는 최근 5영업일만 쓰지만, 연휴·장애로 수집이 밀릴 때를
# 감당하려면 여유가 필요하다. 파일이 무한정 커지는 것도 막는다.
KEEP_DAYS = 40

# KRX가 돌려주는 투자자구분 라벨 → 리포트에서 쓰는 이름.
# 기관은 개별 업권(금융투자·보험·투신…)의 합계 행을 쓴다.
INVESTOR_LABELS = {
    "외국인": ("외국인",),
    "개인": ("개인",),
    "기관": ("기관합계", "기관"),
}

# KRX가 돌려주는 컬럼 → 저장 키. 셋 다 있어야 하는 것은 아니다.
MEASURES = ("매수", "매도", "순매수")


def log(msg: str) -> None:
    print(f"[collect_investor] {msg}", file=sys.stderr)


# --- 순수 함수 (네트워크 없음 — 테스트 대상) --------------------------------


def ticker_of(symbol: str) -> str:
    """Yahoo 티커에서 KRX 6자리 코드만 떼어낸다. 005930.KS → 005930"""
    return symbol.split(".")[0]


def pick_investor_values(frame: dict) -> dict | None:
    """{컬럼: {투자자구분: 값}} 에서 외국인/개인/기관 × 매수/매도/순매수를 뽑는다.

    KRX가 라벨을 바꾸거나 일부 행·열이 빠져도 있는 것만 담는다.
    쓸 값이 하나도 없으면 None — 빈 레코드를 state에 남기지 않는다.

    반환: {"외국인": {"매수": n, "매도": n, "순매수": n}, ...}
    """
    out: dict[str, dict] = {}
    for name, candidates in INVESTOR_LABELS.items():
        values = {}
        for measure in MEASURES:
            rows = frame.get(measure)
            if not isinstance(rows, dict):
                continue
            for label in candidates:
                if label not in rows:
                    continue
                try:
                    values[measure] = int(rows[label])
                except (TypeError, ValueError):
                    log(f"WARN: {measure}/{label} 값이 정수가 아님: {rows[label]!r}")
                break
        if values:
            out[name] = values
    return out or None


def merge_day(state: dict, date: str, key: str, values: dict) -> dict:
    """state에 하루치를 병합. 같은 날짜·같은 대상이면 덮어쓴다 (재실행 대비)."""
    days = state.setdefault("days", {})
    days.setdefault(date, {})[key] = values
    return state


def prune(state: dict, keep: int = KEEP_DAYS) -> dict:
    """오래된 날짜를 잘라낸다."""
    days = state.get("days", {})
    if len(days) <= keep:
        return state
    for old in sorted(days)[:-keep]:
        del days[old]
    return state


def load_state(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"days": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log(f"WARN: {path} 파싱 실패: {e} — 새로 시작")
        return {"days": {}}
    if not isinstance(data.get("days"), dict):
        log("WARN: state 구조가 예상과 다름 — 새로 시작")
        return {"days": {}}
    return data


def load_watchlist() -> list[dict]:
    if not WATCHLIST_PATH.exists():
        return []
    try:
        data = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log(f"WARN: watchlist 파싱 실패: {e}")
        return []
    return [
        s for s in data.get("stocks", [])
        if s.get("symbol") and s.get("name")
    ]


# --- 수집 (네트워크) --------------------------------------------------------


def fetch_one(stock_api, date: str, ticker: str) -> dict | None:
    """하루치 투자자별 거래대금에서 매수·매도·순매수를 추출."""
    df = stock_api.get_market_trading_value_by_investor(date, date, ticker)
    if df is None or df.empty:
        log(f"WARN: {ticker} 응답이 비어 있음 (휴장일이거나 조회 실패)")
        return None
    present = [m for m in MEASURES if m in df.columns]
    if not present:
        log(f"WARN: {ticker} 응답에 매수/매도/순매수 컬럼이 없음: {list(df.columns)}")
        return None
    return pick_investor_values({m: df[m].to_dict() for m in present})


def main() -> None:
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        log("KRX_ID/KRX_PW 미설정 — 투자자 동향 수집을 건너뜁니다")
        return

    try:
        from pykrx import stock as stock_api
    except ImportError as e:
        log(f"pykrx 임포트 실패 — 건너뜁니다: {e}")
        return

    today = os.environ.get("TARGET_DATE") or datetime.datetime.now(KST).strftime("%Y-%m-%d")
    try:
        # KRX는 장 마감 후 확정된다. 06:30 실행 시점엔 직전 영업일이 최신이다.
        target = stock_api.get_nearest_business_day_in_a_week(
            date=today.replace("-", ""), prev=True,
        )
    except Exception as e:
        log(f"영업일 조회 실패 — 건너뜁니다: {e}")
        return
    if not target:
        log("영업일을 판정하지 못했습니다 — 건너뜁니다")
        return

    date_key = f"{target[:4]}-{target[4:6]}-{target[6:8]}"
    log(f"대상 영업일: {date_key}")

    targets = [(s["name"], ticker_of(s["symbol"])) for s in load_watchlist()]
    if not targets:
        log("watchlist가 비어 있습니다 — 건너뜁니다")
        return

    state = load_state(STATE_PATH)
    collected = 0
    for key, ticker in targets:
        try:
            values = fetch_one(stock_api, target, ticker)
        except Exception as e:
            log(f"WARN {key}({ticker}): {type(e).__name__}: {e}")
            continue
        if not values:
            continue
        merge_day(state, date_key, key, values)
        collected += 1
        summary = ", ".join(
            f"{who} {vals.get('순매수', 0):+,}" for who, vals in values.items()
        )
        log(f"  {key}: 순매수 {summary}")

    if not collected:
        log("수집된 항목이 없습니다 — state를 건드리지 않습니다")
        return

    prune(state)
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    log(f"저장 완료: {STATE_PATH.relative_to(REPO_DIR)} "
        f"({collected}건 / 누적 {len(state['days'])}일)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 어떤 예외도 주식 리포트 전체를 막지 않는다
        log(f"ERROR: 예기치 못한 실패 — 건너뜁니다: {type(e).__name__}: {e}")
