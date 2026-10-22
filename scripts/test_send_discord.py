#!/usr/bin/env python3
"""Discord payload self-check. 실행: python3 scripts/test_send_discord.py"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from send_discord import build_payload  # noqa: E402

report = {
    "news": [],
    "claude_updates": [
        {"category": "SDK", "title": "anthropic-sdk-python v9.9.9 릴리스",
         "content": "x", "url": "https://github.com/a/b", "special": False},
        {"category": "SDK", "title": "sdk breaking", "content": "y", "special": True},
        {"category": "제품", "title": "Claude Code v9", "content": "z", "special": False},
    ],
    "specials": ["sdk breaking"],
}

claude = build_payload("2026-10-22", report)["embeds"][1]
text = "".join(f["name"] + f["value"] for f in claude["fields"])

# SDK 블록은 채팅에서 빠지고 나머지 카테고리는 그대로
assert all(f["name"] != "[Claude] SDK" for f in claude["fields"]), claude["fields"]
assert "anthropic-sdk-python" not in text
assert any(f["name"] == "[Claude] 제품" for f in claude["fields"])

# special SDK 항목은 특이사항에 남는다 (⚠️ 신호는 채팅에 유지)
assert any(f["name"] == "[Claude] 특이사항" and "sdk breaking" in f["value"]
           for f in claude["fields"])

# SDK만 있는 날은 "업데이트 없음"
only_sdk = build_payload("2026-10-22", {"claude_updates": report["claude_updates"][:1]})
assert only_sdk["embeds"][1]["fields"] == []
assert "없음" in only_sdk["embeds"][1]["description"]

print("ok")
