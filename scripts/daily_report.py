#!/usr/bin/env python3
"""
daily_report.py — Routine 환경에서 실행되는 처리 스크립트.

입력: Routine이 생성한 처리 결과 JSON (stdin 또는 첫 인자)
  {
    "news": [{"title", "summary", "url"}],
    "claude_updates": [{"id", "category", "title", "content", "url", "special"}]
  }

중복 판정 (Claude 업데이트):
  - `id`가 있으면 그 값이 대표 키. curate 단계가 원본에서 따온 안정적 값
    (gh::{repo}::{tag} / news::{url} / rn::{date}::{slug})을 넣으므로,
    매일 재작성되는 title 문구가 달라져도 같은 릴리즈는 같은 키가 된다.
  - `id`가 없으면 기존 `{category}::{title.lower()}`로 fallback (하위 호환).
  - id가 gh::/news:: 프리픽스인 항목은 `url::{normalize_url(url)}`을 보조 키로
    함께 등록·대조한다. rn:: 항목은 여러 건이 같은 overview URL을 공유하므로 제외.

동작:
  1. state/seen_urls.json, state/seen_claude.json 로드
  2. URL 정규화 + 중복 필터
  3. archive/YYYY/MM/YYYY-MM-DD.{json,md} 생성
  4. state 갱신
  5. 파일 변경을 로컬 디스크에 기록 (git commit/push는 Routine이 Bash로 직접 수행)

이 스크립트는 git/Discord/네트워크를 만지지 않는다. 순수한 파일 처리.
"""
from __future__ import annotations

import datetime
import argparse
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
SEEN_URLS_PATH = STATE_DIR / "seen_urls.json"
SEEN_CLAUDE_PATH = STATE_DIR / "seen_claude.json"

KST = ZoneInfo("Asia/Seoul")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "fbclid", "gclid", "ref", "ref_src",
    "mc_cid", "mc_eid", "_ga", "_gl", "igshid", "yclid", "msclkid",
    "spm", "share_source", "share_medium",
}


def log(msg: str) -> None:
    print(f"[daily_report] {msg}", file=sys.stderr)


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


def claude_item_key(category: str, title: str) -> str:
    return f"{category.strip()}::{title.strip().lower()}"


URL_KEY_PREFIXES = ("gh::", "news::")


def claude_item_keys(c: dict) -> list[str]:
    """항목의 중복 판정 키 목록. [0]이 대표 키."""
    item_id = (c.get("id") or "").strip()
    keys = [item_id] if item_id else [claude_item_key(c["category"], c["title"])]
    url = (c.get("url") or "").strip()
    if url and item_id.startswith(URL_KEY_PREFIXES):
        keys.append(f"url::{normalize_url(url)}")
    return keys


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


def strip_private(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def filter_new(collected: dict, seen_urls: set, seen_claude: set):
    news_new, news_dup = [], []
    for a in collected.get("news", []):
        if not a.get("url"):
            log(f"WARN: URL 없는 뉴스 skip: {a.get('title', '?')}")
            continue
        n = normalize_url(a["url"])
        a["_normalized_url"] = n
        (news_dup if n in seen_urls else news_new).append(a)

    claude_new, claude_dup = [], []
    for c in collected.get("claude_updates", []):
        if not c.get("category") or not c.get("title"):
            log(f"WARN: category/title 누락 Claude 항목 skip: {c}")
            continue
        keys = claude_item_keys(c)
        c["_keys"] = keys
        is_dup = any(k in seen_claude for k in keys)
        (claude_dup if is_dup else claude_new).append(c)

    specials = [c["title"] for c in claude_new if c.get("special")]
    return news_new, news_dup, claude_new, claude_dup, specials


def render_markdown(date, news_new, claude_new, specials, dup_counts) -> str:
    lines = [f"# AI/IT Daily — {date}", ""]
    lines.append(
        f"> 신규: 뉴스 {len(news_new)}건 · Claude {len(claude_new)}건  "
    )
    lines.append(
        f"> 중복 제외: 뉴스 {dup_counts['news']}건 · Claude {dup_counts['claude']}건"
    )
    lines.append("")

    lines.append("## AI/IT 뉴스")
    if not news_new:
        lines.append("_금일 신규 기사 없음._")
    else:
        for a in news_new:
            lines.append(f"### {a['title']}")
            lines.append(a.get("summary", "").strip())
            lines.append(f"[원문]({a['url']})")
            lines.append("")
    lines.append("")

    lines.append("## Claude/Anthropic 업데이트")
    if not claude_new:
        lines.append("_금일 신규 업데이트 없음._")
    else:
        by_cat: dict[str, list] = {}
        for c in claude_new:
            by_cat.setdefault(c["category"], []).append(c)
        for cat, items in by_cat.items():
            lines.append(f"### {cat}")
            for c in items:
                marker = " ⚠️" if c.get("special") else ""
                lines.append(f"- **{c['title']}**{marker}")
                if c.get("content"):
                    lines.append(f"  {c['content']}")
                if c.get("url"):
                    lines.append(f"  [출처]({c['url']})")
            lines.append("")

    if specials:
        lines.append("## 특이사항")
        for s in specials:
            lines.append(f"- {s}")
        lines.append("")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="처리 결과 JSON을 archive/state에 반영")
    p.add_argument("input", nargs="?", default=None,
                   help="처리 결과 JSON 경로 (생략 시 stdin)")
    p.add_argument("--date", default=None,
                   help="리포트 대상 날짜 YYYY-MM-DD "
                        "(생략 시 TARGET_DATE env → KST 오늘)")
    return p.parse_args()


def resolve_report_date(arg_date: str | None) -> str:
    raw = (arg_date
           or os.environ.get("TARGET_DATE")
           or datetime.datetime.now(KST).strftime("%Y-%m-%d")).strip()
    if not DATE_RE.match(raw):
        log(f"ERROR: 잘못된 날짜 형식 {raw!r} — YYYY-MM-DD 필요")
        raise SystemExit(1)
    return raw


def load_collected(input_path: str | None) -> dict:
    if input_path:
        return json.loads(pathlib.Path(input_path).read_text(encoding="utf-8"))
    data = sys.stdin.read()
    if not data.strip():
        log("ERROR: 수집 결과 입력 없음")
        raise SystemExit(1)
    return json.loads(data)


def main() -> None:
    args = parse_args()
    today = resolve_report_date(args.date)   # 벽시계가 아닌 '대상 날짜'
    log(f"대상 날짜 (KST): {today}")
    log(f"Repo: {REPO_DIR}")

    collected = load_collected(args.input)
    log(
        f"입력: 뉴스 {len(collected.get('news', []))}건, "
        f"Claude {len(collected.get('claude_updates', []))}건"
    )

    seen_urls = set(load_json(SEEN_URLS_PATH, {"urls": []}).get("urls", []))
    seen_claude = set(load_json(SEEN_CLAUDE_PATH, {"items": []}).get("items", []))
    log(f"기존 state: URL {len(seen_urls)}건, Claude 항목 {len(seen_claude)}건")

    news_new, news_dup, claude_new, claude_dup, specials = filter_new(
        collected, seen_urls, seen_claude,
    )
    dup_counts = {"news": len(news_dup), "claude": len(claude_dup)}
    log(
        f"필터 후: 신규 뉴스 {len(news_new)}건 (중복 {len(news_dup)}), "
        f"신규 Claude {len(claude_new)}건 (중복 {len(claude_dup)}), "
        f"특이사항 {len(specials)}건"
    )

    # archive (빈 입력이어도 "그날 확인했음" 기록용으로 생성)
    year, month = today[:4], today[5:7]
    day_dir = ARCHIVE_DIR / year / month
    day_dir.mkdir(parents=True, exist_ok=True)
    json_path = day_dir / f"{today}.json"
    md_path = day_dir / f"{today}.md"

    save_json(json_path, {
        "date": today,
        "news": [strip_private(a) for a in news_new],
        "claude_updates": [strip_private(c) for c in claude_new],
        "specials": specials,
        "duplicate_counts": dup_counts,
    })
    md_path.write_text(
        render_markdown(today, news_new, claude_new, specials, dup_counts),
        encoding="utf-8",
    )
    log(f"archive: {json_path.relative_to(REPO_DIR)}")
    log(f"archive: {md_path.relative_to(REPO_DIR)}")

    # state 갱신
    for a in news_new:
        seen_urls.add(a["_normalized_url"])
    for c in claude_new:
        seen_claude.update(c["_keys"])
    save_json(SEEN_URLS_PATH, {"urls": sorted(seen_urls)})
    save_json(SEEN_CLAUDE_PATH, {"items": sorted(seen_claude)})

    # stdout으로 결과 요약 (Routine이 후속 동작에 활용)
    print(json.dumps({
        "date": today,
        "new_news_count": len(news_new),
        "new_claude_count": len(claude_new),
        "specials_count": len(specials),
        "archive_json": str(json_path.relative_to(REPO_DIR)),
        "archive_md": str(md_path.relative_to(REPO_DIR)),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
