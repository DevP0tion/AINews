#!/usr/bin/env python3
"""level_context self-check. 실행: python3 scripts/test_level_context.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from level_context import (  # noqa: E402
    BOX_WINDOW, closes_of, compute_level_context, compute_weekly_stats,
)


# --- compute_level_context --------------------------------------------------

# 기본 — 마지막 종가가 기간 고저의 어디쯤인지
ctx = compute_level_context([100, 80, 120, 90])
assert ctx["last_close"] == 90.0
assert ctx["period_high"] == 120.0 and ctx["period_low"] == 80.0
assert round(ctx["pct_from_high"], 4) == -25.0     # 120 → 90
assert round(ctx["pct_from_low"], 4) == 12.5       # 80 → 90
assert ctx["samples"] == 4

# 데이터가 1일뿐이면 고·저·현재가가 모두 같고 변화율은 0
one = compute_level_context([273250.0])
assert one["period_high"] == one["period_low"] == one["last_close"] == 273250.0
assert one["pct_from_high"] == 0.0 and one["pct_from_low"] == 0.0
assert one["box_window"] == 1, one
assert one["box_high"] == one["box_low"] == 273250.0

# 쓸 값이 하나도 없으면 None — 호출부가 섹션을 통째로 생략한다
assert compute_level_context([]) is None
assert compute_level_context([None, None]) is None
assert compute_level_context(["x", True, float("nan"), float("inf")]) is None

# 결측이 섞여도 나머지로 계산한다 (Yahoo는 휴장·거래정지 구간을 null로 준다)
gapped = compute_level_context([100, None, 50, None, 75])
assert gapped["samples"] == 3 and gapped["last_close"] == 75.0
assert gapped["period_high"] == 100.0 and gapped["period_low"] == 50.0

# 시계열이 BOX_WINDOW보다 짧으면 있는 만큼만 쓰고 그 수를 그대로 보고한다
short = compute_level_context(list(range(1, 6)))          # 5일치
assert short["box_window"] == 5, short
assert short["box_high"] == 5.0 and short["box_low"] == 1.0

# 길면 마지막 BOX_WINDOW일만 박스로 잡는다 (기간 고저와 달라야 의미가 있다)
long_series = list(range(1, 101))                          # 1..100 상승
box = compute_level_context(long_series)
assert box["box_window"] == BOX_WINDOW
assert box["box_low"] == float(100 - BOX_WINDOW + 1) and box["box_high"] == 100.0
assert box["period_low"] == 1.0 and box["period_high"] == 100.0

# box_window를 0 이하로 주면 전 구간을 박스로 본다 (나누기 없이 안전하게)
whole = compute_level_context([1, 2, 3], box_window=0)
assert whole["box_window"] == 3 and whole["box_low"] == 1.0

# 종가가 0 이하이면 변화율을 만들지 않는다 (나눌 수 없다)
zeroed = compute_level_context([0, 0])
assert zeroed["pct_from_high"] is None and zeroed["pct_from_low"] is None


# --- closes_of --------------------------------------------------------------

assert closes_of([{"date": "2026-09-18", "close": 10},
                  {"date": "2026-09-21", "close": None},
                  {"date": "2026-09-22", "close": 12}]) == [10.0, 12.0]
assert closes_of(None) == [] and closes_of([]) == []
assert closes_of(["nope", 1, {"close": "x"}]) == []


# --- compute_weekly_stats ---------------------------------------------------

HIST = [
    {"date": "2026-09-07", "close": 100.0},   # 경계 이전
    {"date": "2026-09-11", "close": 80.0},    # 경계 이전 — 전주 기준 저점
    {"date": "2026-09-15", "close": 90.0},    # 주중
    {"date": "2026-09-18", "close": 95.0},    # 주중
    {"date": "2026-09-21", "close": 110.0},   # 주중 마지막
]

w = compute_weekly_stats(HIST, "2026-09-21")   # 경계 = 2026-09-14
assert w["from"] == "2026-09-15" and w["to"] == "2026-09-21", w
assert w["sessions"] == 3
assert w["base_close"] == 80.0                 # 경계 직전 종가
assert w["last_close"] == 110.0
assert round(w["change_pct"], 4) == 37.5
assert w["week_high"] == 110.0 and w["week_low"] == 90.0
# 저점 대비 회복률: 이번 주 (110-80)/80 = 37.5%, 전주 (80-80)/80 = 0%
assert round(w["pct_from_low"], 4) == 37.5
assert round(w["prev_pct_from_low"], 4) == 0.0
assert round(w["recovery_delta"], 4) == 37.5

# 경계 이전 데이터가 없으면 주중 첫 종가가 기준이 되고 전주 비교는 비어 있다
fresh = compute_weekly_stats(
    [{"date": "2026-09-18", "close": 50.0}, {"date": "2026-09-21", "close": 60.0}],
    "2026-09-21",
)
assert fresh["base_close"] == 50.0 and round(fresh["change_pct"], 4) == 20.0
assert fresh["prev_pct_from_low"] is None and fresh["recovery_delta"] is None

# 주중 데이터가 하나도 없으면 None (휴장 주간 등)
assert compute_weekly_stats([{"date": "2026-08-01", "close": 1.0}], "2026-09-21") is None

# 시계열이 아예 없거나 날짜가 깨졌으면 None
assert compute_weekly_stats([], "2026-09-21") is None
assert compute_weekly_stats(None, "2026-09-21") is None
assert compute_weekly_stats(HIST, "not-a-date") is None
assert compute_weekly_stats(HIST, None) is None

# 미래 날짜 행은 버린다 (거래소 타임존 차이로 하루 앞선 행이 섞일 수 있다)
future = compute_weekly_stats(HIST + [{"date": "2026-09-22", "close": 999.0}], "2026-09-21")
assert future["last_close"] == 110.0 and future["week_high"] == 110.0

# 입력 순서가 뒤죽박죽이어도 날짜 기준으로 정렬해서 읽는다
shuffled = compute_weekly_stats(list(reversed(HIST)), "2026-09-21")
assert shuffled == w, (shuffled, w)

print("ok")
