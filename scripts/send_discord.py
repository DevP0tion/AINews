#!/usr/bin/env python3
"""
send_discord.py — finalize Action에서 Discord webhook으로 리포트 전송.

입력: 환경변수 REPORT_DATE (YYYY-MM-DD), DISCORD_WEBHOOK_POTIONBOT_NEWS
읽기: archive/YYYY/MM/YYYY-MM-DD.json
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
MAX_FIELD = 1024
MAX_FIELDS_PER_EMBED = 25

COLOR_NEWS = 5765618
COLOR_CLAUDE = 14271596


def log(msg: str) -> None:
    print(f"[send_discord] {msg}", file=sys.stderr)


def chunk_field(name: str, value: str) -> list[dict]:
    if len(value) <= MAX_FIELD:
        return [{"name": name[:256], "value": value, "inline": False}]
    parts, i, n = [], 0, 0
    while i < len(value):
        end = min(i + MAX_FIELD, len(value))
        if end < len(value):
            nl = value.rfind("\n", i, end)
            if nl > i + 200:
                end = nl
        suffix = "" if n == 0 else f" (cont. {n})"
        parts.append({
            "name": (name + suffix)[:256],
            "value": value[i:end],
            "inline": False,
        })
        i = end
        n += 1
    return parts


def build_payload(date: str, report: dict) -> dict:
    ts = datetime.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    news = report.get("news", [])
    claude_updates = report.get("claude_updates", [])
    specials = report.get("specials", [])

    # News embed
    news_fields: list[dict] = []
    for a in news:
        body = f"{a.get('summary', '').strip()}\n[링크]({a['url']})"
        news_fields.extend(chunk_field(a["title"], body))
    if not news_fields:
        news_fields = [{
            "name": "상태",
            "value": "금일 신규 기사 없음.",
            "inline": False,
        }]

    # Claude embed
    by_cat: dict[str, list] = {}
    for c in claude_updates:
        by_cat.setdefault(c["category"], []).append(c)

    claude_fields: list[dict] = []
    for cat, items in by_cat.items():
        block_lines = []
        for c in items:
            marker = " ⚠️" if c.get("special") else ""
            line = f"• **{c['title']}**{marker}"
            if c.get("content"):
                line += f"\n  {c['content']}"
            if c.get("url"):
                line += f"\n  [출처]({c['url']})"
            block_lines.append(line)
        claude_fields.extend(chunk_field(
            f"[Claude] {cat}",
            "\n\n".join(block_lines),
        ))

    if specials:
        claude_fields.extend(chunk_field(
            "[Claude] 특이사항",
            "\n".join(f"• {s}" for s in specials),
        ))

    return {
        "embeds": [
            {
                "title": f"AI/IT Daily News — {date}",
                "description": "오늘의 AI/IT 주요 소식입니다.",
                "color": COLOR_NEWS,
                "fields": news_fields[:MAX_FIELDS_PER_EMBED],
                "footer": {"text": "PotionBot News · 자동 수집"},
                "timestamp": ts,
            },
            {
                "title": f"Claude/Anthropic Update Report — {date}",
                "description": (
                    "오늘의 Claude/Anthropic 업데이트 현황입니다."
                    if claude_fields else "금일 확인된 Claude/Anthropic 업데이트 없음."
                ),
                "color": COLOR_CLAUDE,
                "fields": claude_fields[:MAX_FIELDS_PER_EMBED],
                "footer": {"text": "PotionBot News · Claude Watch"},
                "timestamp": ts,
            },
        ]
    }


def post_discord(webhook: str, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 204:
                log(f"WARN: HTTP {resp.status} (예상 204)")
                log(resp.read(500).decode("utf-8", errors="ignore"))
    except urllib.error.HTTPError as e:
        log(f"ERROR: HTTP {e.code} — {e.read(500).decode('utf-8', errors='ignore')}")
        raise SystemExit(2)


def main() -> None:
    webhook = os.environ.get("DISCORD_WEBHOOK_POTIONBOT_NEWS")
    if not webhook:
        log("ERROR: secrets.DISCORD_WEBHOOK_POTIONBOT_NEWS 미설정")
        raise SystemExit(3)

    date = os.environ.get("REPORT_DATE")
    if not date:
        log("ERROR: REPORT_DATE 입력 없음")
        raise SystemExit(4)

    year, month = date[:4], date[5:7]
    report_path = REPO_DIR / "archive" / year / month / f"{date}.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        log(
            f"리포트 로드: 뉴스 {len(report.get('news', []))}, "
            f"Claude {len(report.get('claude_updates', []))}, "
            f"특이 {len(report.get('specials', []))}"
        )
    else:
        log(f"리포트 파일 없음 — 빈 리포트로 전송: {report_path}")
        report = {"news": [], "claude_updates": [], "specials": []}

    payload = build_payload(date, report)
    post_discord(webhook, payload)
    log("Discord 전송 완료")


if __name__ == "__main__":
    main()
