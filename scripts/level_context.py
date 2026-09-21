#!/usr/bin/env python3
"""
level_context.py — 종가 시계열에서 "지금 어느 레벨인가"를 계산하는 순수 함수 모음.

I/O·네트워크·시계(時計) 접근이 없다. 입력은 종가 목록이나
`[{"date","close"}]` 시계열이고, 출력은 그대로 archive JSON에 실린다.

리포트에 실리는 숫자는 수집 원본(inbox) 아니면 여기서만 나온다.
curate 단계 LLM이 적어 보낸 숫자는 어느 경로로도 쓰지 않는다.
"""
from __future__ import annotations

import datetime
import math

# --- 조정 가능한 기본값 -----------------------------------------------------
BOX_WINDOW = 20   # adjustable — 박스 상·하단을 잡는 거래일 수
WEEK_DAYS = 7     # adjustable — 주간 통계가 "한 주"로 보는 달력 일수


def _clean(values) -> list[float]:
    """None·NaN·inf·bool·비숫자를 걷어낸 float 목록.

    Yahoo chart API는 거래정지·휴장 구간의 종가를 null로 준다.
    bool은 int의 서브클래스라 따로 막지 않으면 True가 1.0으로 섞인다.
    """
    out = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        f = float(v)
        if math.isfinite(f):
            out.append(f)
    return out


def _pct(value: float, base) -> float | None:
    """base 대비 value의 변화율(%). base가 0 이하면 None (나눌 수 없다)."""
    if base is None or base <= 0:
        return None
    return (value - base) / base * 100.0


def closes_of(history) -> list[float]:
    """`[{"date","close"}]` → 종가 목록. 결측 항목은 건너뛴다."""
    if not isinstance(history, list):
        return []
    return _clean(e.get("close") for e in history if isinstance(e, dict))


def compute_level_context(closes, box_window: int = BOX_WINDOW) -> dict | None:
    """기간 고저 대비 위치와 최근 박스권.

    반환값의 `box_window`는 **실제로 쓴 표본 수**다. 시계열이 요청한 창보다
    짧으면 있는 만큼만 쓰므로, 표기할 때 20일이라고 단정하면 안 된다.

    쓸 수 있는 종가가 하나도 없으면 None — 호출부가 섹션을 생략한다.
    """
    values = _clean(closes)
    if not values:
        return None

    last = values[-1]
    period_high, period_low = max(values), min(values)
    window = values[-box_window:] if box_window and box_window > 0 else values

    return {
        "last_close": last,
        "period_high": period_high,
        "period_low": period_low,
        "pct_from_high": _pct(last, period_high),
        "pct_from_low": _pct(last, period_low),
        "box_high": max(window),
        "box_low": min(window),
        "box_window": len(window),
        "samples": len(values),
    }


def _sorted_rows(history, today: str) -> list[dict]:
    """날짜·종가가 온전하고 today 이하인 항목만 날짜 오름차순으로."""
    rows = []
    for e in history or []:
        if not isinstance(e, dict):
            continue
        date = e.get("date")
        if not isinstance(date, str) or not _clean([e.get("close")]):
            continue
        if date > today:
            continue  # 미래 날짜는 버린다 (거래소 타임존 차이로 하루 앞선 행이 섞일 수 있다)
        rows.append({"date": date, "close": float(e["close"])})
    rows.sort(key=lambda e: e["date"])
    return rows


def compute_weekly_stats(
    history,
    today: str,
    *,
    days: int = WEEK_DAYS,
    box_window: int = BOX_WINDOW,
) -> dict | None:
    """한 주 치 변동률·고저와 level_context의 전주 대비 변화.

    today 기준 days일 전을 경계로 시계열을 두 토막 낸다. 경계 이전 구간만으로
    level_context를 다시 계산해 "전주 값"을 만들기 때문에 과거 archive를 읽지
    않는다 — 같은 history면 언제 돌려도 같은 값이 나온다.

    `recovery_delta`는 저점 대비 회복률(%)의 주간 증감이므로 단위는 %p다.
    경계 이전 데이터가 없으면(상장 직후 등) None으로 남는다.
    """
    try:
        cutoff = (
            datetime.date.fromisoformat(today) - datetime.timedelta(days=days)
        ).isoformat()
    except (TypeError, ValueError):
        return None

    rows = _sorted_rows(history, today)
    if not rows:
        return None

    prior = [e for e in rows if e["date"] <= cutoff]
    week = [e for e in rows if e["date"] > cutoff]
    if not week:
        return None

    week_closes = [e["close"] for e in week]
    # 주간 변동률의 기준은 경계 직전 종가. 그 주가 시계열의 시작이면 주중 첫 종가.
    base = prior[-1]["close"] if prior else week_closes[0]
    last = week_closes[-1]

    current = compute_level_context([e["close"] for e in rows], box_window)
    previous = compute_level_context([e["close"] for e in prior], box_window) if prior else None

    recovery_delta = None
    if current and previous:
        now_low, was_low = current["pct_from_low"], previous["pct_from_low"]
        if now_low is not None and was_low is not None:
            recovery_delta = now_low - was_low

    return {
        "from": week[0]["date"],
        "to": week[-1]["date"],
        "sessions": len(week),
        "base_close": base,
        "last_close": last,
        "change_pct": _pct(last, base),
        "week_high": max(week_closes),
        "week_low": min(week_closes),
        "pct_from_low": current["pct_from_low"] if current else None,
        "prev_pct_from_low": previous["pct_from_low"] if previous else None,
        "recovery_delta": recovery_delta,
    }
