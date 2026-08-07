#!/usr/bin/env python3
"""중복 판정 키 산출 self-check. 실행: python3 scripts/test_daily_report.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from daily_report import claude_item_keys  # noqa: E402

GH_URL = "https://github.com/anthropics/anthropic-sdk-python/releases/tag/v9.9.9"

# id가 있으면 title 문구가 달라져도 같은 키
a = claude_item_keys({"id": "gh::anthropic-sdk-python::v9.9.9", "category": "SDK",
                      "title": "python sdk v9.9.9", "url": GH_URL})
b = claude_item_keys({"id": "gh::anthropic-sdk-python::v9.9.9", "category": "SDK",
                      "title": "python sdk v9.9.9 릴리스", "url": GH_URL})
assert a == b, (a, b)
assert a[0] == "gh::anthropic-sdk-python::v9.9.9"
assert a[1] == f"url::{GH_URL}", a

# gh::/news::는 url 보조 키 있음 (tracking param 제거 후 일치)
c = claude_item_keys({"id": "gh::anthropic-sdk-python::9.9.9", "category": "SDK",
                      "title": "x", "url": GH_URL + "?utm_source=x"})
assert set(a) & set(c), (a, c)

# rn::은 overview url을 공유하므로 보조 url 키를 만들지 않는다
rn = claude_item_keys({"id": "rn::2026-08-06::opus-5-ga", "category": "모델/API",
                       "title": "x", "url": "https://docs.claude.com/en/release_notes/overview"})
assert rn == ["rn::2026-08-06::opus-5-ga"], rn

# id 없으면 기존 {category}::{title.lower()} fallback
legacy = claude_item_keys({"category": "SDK", "title": "Legacy Item",
                           "url": "https://example.com/a"})
assert legacy == ["SDK::legacy item"], legacy

print("ok")
