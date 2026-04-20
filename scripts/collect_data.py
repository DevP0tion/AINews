#!/usr/bin/env python3
"""
collect_data.py — GitHub Actions에서 실행되는 데이터 수집 스크립트.

결정론적 소스만 fetch해서 inbox/YYYY-MM-DD-raw.json에 저장한다.
AI 판단이 필요한 부분(한국어 요약, top 선정, specials 판정)은 Routine이 담당.

출력 스키마:
  {
    "date": "YYYY-MM-DD",
    "collected_at": ISO UTC,
    "anthropic_news": [{"title", "url", "summary", "published"}],
    "claude_release_notes_md": "raw markdown",
    "github_releases": {
      "claude_code": [{"title", "url", "body", "published"}],
      "sdk_python": [...],
      "sdk_typescript": [...],
    },
    "hn_ai_stories": [{"title", "url", "score", "source"}],
    "arxiv_recent": [{"title", "url", "summary", "published"}]
  }
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys

import feedparser
import requests

UA = "PotionBot-News/1.0 (+https://github.com/DevP0tion/AINews)"
TIMEOUT = 15

AI_KEYWORDS = [
    "ai ", "a.i.", "llm", "gpt", "claude", "anthropic", "openai",
    "gemini", "diffusion", "transformer", "neural", "machine learning",
    "deep learning", "rag", "agent", "mcp", "hugging face",
]


def log(msg: str) -> None:
    print(f"[collect] {msg}", file=sys.stderr)


def http_get_json(url: str, **kwargs):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, **kwargs)
    r.raise_for_status()
    return r.json()


def http_get_text(url: str, **kwargs) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, **kwargs)
    r.raise_for_status()
    return r.text


def safe(label: str, fn, default):
    try:
        return fn()
    except Exception as e:
        log(f"WARN {label}: {e}")
        return default


# ---------------------------------------------------------------------------
# Source fetchers
# ---------------------------------------------------------------------------


def fetch_anthropic_news():
    """Anthropic 뉴스. RSS 우선, 실패 시 빈 배열."""
    feed = feedparser.parse("https://www.anthropic.com/news/rss.xml")
    if not feed.entries:
        return []
    out = []
    for e in feed.entries[:10]:
        out.append({
            "title": getattr(e, "title", ""),
            "url": getattr(e, "link", ""),
            "summary": getattr(e, "summary", ""),
            "published": getattr(e, "published", ""),
        })
    return out


def fetch_claude_release_notes() -> str:
    """docs.claude.com 릴리즈 노트 원본 markdown."""
    try:
        return http_get_text("https://docs.claude.com/en/release_notes/overview.md")
    except Exception:
        # fallback: HTML 페이지 raw
        try:
            return http_get_text("https://docs.claude.com/en/release_notes/overview")
        except Exception:
            return ""


def fetch_github_releases(repo: str, limit: int = 5):
    """GitHub Releases API. token 있으면 헤더 추가."""
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = requests.get(
        f"https://api.github.com/repos/{repo}/releases",
        headers=headers,
        params={"per_page": limit},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    out = []
    for rel in r.json():
        out.append({
            "title": rel.get("name") or rel.get("tag_name"),
            "tag": rel.get("tag_name"),
            "url": rel.get("html_url"),
            "body": rel.get("body", "")[:2000],
            "published": rel.get("published_at"),
            "prerelease": rel.get("prerelease", False),
        })
    return out


def fetch_hn_ai_stories(limit: int = 15):
    """Hacker News top stories 중 AI 관련 필터."""
    top_ids = http_get_json("https://hacker-news.firebaseio.com/v0/topstories.json")[:120]
    out = []
    for sid in top_ids:
        try:
            item = http_get_json(
                f"https://hacker-news.firebaseio.com/v0/item/{sid}.json"
            )
        except Exception:
            continue
        if not item or item.get("type") != "story":
            continue
        title = (item.get("title") or "").lower()
        if not any(kw in title for kw in AI_KEYWORDS):
            continue
        out.append({
            "title": item.get("title"),
            "url": item.get("url") or f"https://news.ycombinator.com/item?id={sid}",
            "score": item.get("score", 0),
            "hn_url": f"https://news.ycombinator.com/item?id={sid}",
            "time": item.get("time"),
        })
        if len(out) >= limit:
            break
    return out


def fetch_arxiv_recent(categories=("cs.LG", "cs.CL"), limit: int = 10):
    """arxiv 최근 논문 (cs.LG, cs.CL)."""
    out = []
    for cat in categories:
        url = (
            f"https://export.arxiv.org/api/query"
            f"?search_query=cat:{cat}&start=0&max_results={limit}"
            f"&sortBy=submittedDate&sortOrder=descending"
        )
        feed = feedparser.parse(url)
        for e in feed.entries[:limit]:
            out.append({
                "title": getattr(e, "title", "").strip().replace("\n", " "),
                "url": getattr(e, "link", ""),
                "summary": getattr(e, "summary", "")[:600].strip().replace("\n", " "),
                "published": getattr(e, "published", ""),
                "category": cat,
            })
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    target_date = os.environ.get("TARGET_DATE") or datetime.datetime.utcnow().strftime("%Y-%m-%d")
    log(f"대상 날짜: {target_date}")

    data = {
        "date": target_date,
        "collected_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "anthropic_news": safe("anthropic_news", fetch_anthropic_news, []),
        "claude_release_notes_md": safe("release_notes", fetch_claude_release_notes, ""),
        "github_releases": {
            "claude_code": safe(
                "gh/claude-code",
                lambda: fetch_github_releases("anthropics/claude-code"),
                [],
            ),
            "sdk_python": safe(
                "gh/sdk-python",
                lambda: fetch_github_releases("anthropics/anthropic-sdk-python"),
                [],
            ),
            "sdk_typescript": safe(
                "gh/sdk-typescript",
                lambda: fetch_github_releases("anthropics/anthropic-sdk-typescript"),
                [],
            ),
        },
        "hn_ai_stories": safe("hn", fetch_hn_ai_stories, []),
        "arxiv_recent": safe("arxiv", fetch_arxiv_recent, []),
    }

    out_path = pathlib.Path(f"inbox/{target_date}-raw.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log(f"저장 완료: {out_path}")
    log(
        f"요약: anthropic_news={len(data['anthropic_news'])}, "
        f"release_notes_chars={len(data['claude_release_notes_md'])}, "
        f"gh_releases={sum(len(v) for v in data['github_releases'].values())}, "
        f"hn={len(data['hn_ai_stories'])}, arxiv={len(data['arxiv_recent'])}"
    )


if __name__ == "__main__":
    main()
