#!/usr/bin/env python3
"""Discord payload self-check. 실행: python3 scripts/test_send_discord.py"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from send_discord import build_payload, sdk_page_url  # noqa: E402

os.environ.pop("SDK_PAGE_URL", None)
os.environ["GITHUB_REPOSITORY"] = "DevP0tion/AINews"
assert sdk_page_url() == "https://devp0tion.github.io/AINews/sdk/"

report = {
    "news": [],
    "claude_updates": [
        {"category": "SDK", "title": "anthropic-sdk-python v9.9.9 릴리스",
         "content": "x", "url": "https://github.com/a/b", "special": False},
        {"category": "SDK", "title": "sdk breaking", "content": "y", "special": True},
        {"category": "SDK", "title": "sdk breaking 2", "content": "y", "special": True},
        {"category": "모델/API", "title": "api breaking", "content": "w", "special": True},
        {"category": "제품", "title": "Claude Code v9", "content": "z", "special": False},
    ],
    "specials": ["sdk breaking", "sdk breaking 2", "api breaking"],
}

claude = build_payload("2026-10-22", report)["embeds"][1]
text = "".join(f["name"] + f["value"] for f in claude["fields"])

# SDK 블록은 채팅에서 빠지고 나머지 카테고리는 그대로
assert all(f["name"] != "[Claude] SDK" for f in claude["fields"]), claude["fields"]
assert "anthropic-sdk-python" not in text
assert any(f["name"] == "[Claude] 제품" for f in claude["fields"])

# special SDK 항목은 특이사항에서 제목 대신 건수 한 줄 + SDK 페이지 링크로 접힌다
specials = next(f["value"] for f in claude["fields"] if f["name"] == "[Claude] 특이사항")
assert "sdk breaking" not in specials, specials
assert "• api breaking" in specials, "SDK 외 특이사항은 그대로"
assert ("SDK 업데이트 2건 ⚠️ — [SDK 페이지](https://devp0tion.github.io/AINews/sdk/)"
        in specials), specials
assert report["specials"] == ["sdk breaking", "sdk breaking 2", "api breaking"], "입력 불변"

# SDK special만 있는 날도 특이사항 한 줄은 남는다
sdk_only_special = build_payload("2026-10-22", {
    "claude_updates": report["claude_updates"][1:2], "specials": ["sdk breaking"],
})["embeds"][1]
assert [f["name"] for f in sdk_only_special["fields"]] == ["[Claude] 특이사항"]
assert "SDK 업데이트 1건" in sdk_only_special["fields"][0]["value"]

# 저장소 정보가 없으면(로컬 실행) 링크 없이 문구만
del os.environ["GITHUB_REPOSITORY"]
local = build_payload("2026-10-22", report)["embeds"][1]
assert any("— SDK 페이지에서 확인" in f["value"] for f in local["fields"])

# SDK만 있는 날은 "업데이트 없음"
only_sdk = build_payload("2026-10-22", {"claude_updates": report["claude_updates"][:1]})
assert only_sdk["embeds"][1]["fields"] == []
assert "없음" in only_sdk["embeds"][1]["description"]

print("ok")
