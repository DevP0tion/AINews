#!/usr/bin/env python3
"""중복 판정 키 산출 self-check. 실행: python3 scripts/test_daily_report.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from daily_report import (  # noqa: E402
    claude_item_keys, filter_new, is_stale_rereport, seen_sections,
)

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

# app::(앱 릴리즈 노트)도 같은 이유로 보조 url 키가 없다
app = claude_item_keys({"id": "app::2026-10-07::monthly-api-credits", "category": "제품",
                        "title": "x",
                        "url": "https://support.claude.com/en/articles/12138966-release-notes"})
assert app == ["app::2026-10-07::monthly-api-credits"], app

# id 없으면 기존 {category}::{title.lower()} fallback
legacy = claude_item_keys({"category": "SDK", "title": "Legacy Item",
                           "url": "https://example.com/a"})
assert legacy == ["SDK::legacy item"], legacy

# --- 지난 섹션 재등장 (키워드만 바뀐 rn::/app:: 재보고) -----------------------

seen = {"rn::2026-10-21::sdk-and-cli-changes", "app::2026-10-07::monthly-api-credits",
        "gh::claude-code::v2.1.300"}
sections = seen_sections(seen)
assert sections == {"rn::2026-10-21::", "app::2026-10-07::"}, sections

# 그저께 섹션 + 이미 보고한 섹션 → 재등장 (2026-10-23 실제 사례)
assert is_stale_rereport("rn::2026-10-21::sdk-cli-releases", "2026-10-23", sections)
assert is_stale_rereport("app::2026-10-07::credits-max-team", "2026-10-09", sections)
# 어제 섹션은 정상 범위 — 막지 않는다
assert not is_stale_rereport("rn::2026-10-21::sdk-cli-releases", "2026-10-22", sections)
# 처음 보는 섹션은 늦게 올라온 항목일 수 있다 — 막지 않는다
assert not is_stale_rereport("rn::2026-09-23::cache-diagnostics", "2026-09-25", sections)
# rn:: 섹션과 app:: 섹션은 따로 본다
assert not is_stale_rereport("app::2026-10-21::x", "2026-10-23", sections)
# 날짜가 없거나 깨진 id, gh::/news:: 는 대상이 아니다
assert not is_stale_rereport("rn::latest::x", "2026-10-23", sections)
assert not is_stale_rereport("rn::2026-13-45::x", "2026-10-23", {"rn::2026-13-45::"})
assert not is_stale_rereport("gh::claude-code::v2.1.301", "2026-10-23", sections)


def rn_item(item_id):
    return {"id": item_id, "category": "SDK", "title": item_id, "url": "https://x.test/a"}


collected = {"news": [], "claude_updates": [
    rn_item("rn::2026-10-21::sdk-cli-releases"),       # 재등장 → 중복
    rn_item("rn::2026-10-22::claude-models-on-gcp"),   # 어제 섹션 → 신규
    rn_item("rn::2026-10-20::late-item"),              # 처음 보는 지난 섹션 → 신규
]}
_, _, new, dup, _ = filter_new(collected, set(), set(seen), "2026-10-23")
assert [c["id"] for c in dup] == ["rn::2026-10-21::sdk-cli-releases"], dup
assert [c["id"] for c in new] == ["rn::2026-10-22::claude-models-on-gcp",
                                   "rn::2026-10-20::late-item"], new

# today 없이 호출하면 섹션 판정은 하지 않는다 (id 대조만)
_, _, new, dup, _ = filter_new(collected, set(), set(seen))
assert not dup and len(new) == 3

print("ok")
