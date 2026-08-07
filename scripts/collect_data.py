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
from xml.etree import ElementTree

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


def fetch_anthropic_news(limit: int = 10):
    """Anthropic 뉴스. sitemap.xml에서 /news/ 항목을 lastmod 내림차순으로 최근 limit개.

    anthropic.com은 RSS를 제공하지 않는다(/news/rss.xml, /rss.xml 모두 404).
    sitemap.xml(HTTP 200)의 <loc>+<lastmod> 쌍이 유일하게 안정적인 소스다.
    title은 slug에서 유도한 근사값이고 summary는 비어 있다 — 정확한 제목·발행일은
    curate 단계에서 Claude가 WebFetch로 원문을 확인한다.
    실패하거나 결과가 0건이면 WARN 로그 후 빈 배열 (job은 실패시키지 않음).
    """
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    prefix = "https://www.anthropic.com/news/"

    try:
        xml = http_get_text("https://www.anthropic.com/sitemap.xml")
    except Exception as e:
        log(f"WARN anthropic_news: sitemap fetch 실패: {e}")
        return []

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as e:
        log(f"WARN anthropic_news: sitemap 파싱 실패: {e}")
        return []

    items = []
    for url_el in root.findall("sm:url", ns):
        loc = (url_el.findtext("sm:loc", "", ns) or "").strip()
        if not loc.startswith(prefix):
            continue
        lastmod = (url_el.findtext("sm:lastmod", "", ns) or "").strip()
        slug = loc[len(prefix):].strip("/").replace("-", " ")
        if not slug:
            continue  # /news/ 인덱스 페이지 자체 — 항상 최신 lastmod라 1위를 먹는다
        items.append({
            "title": slug[:1].upper() + slug[1:],
            "url": loc,
            "summary": "",
            "published": lastmod,
        })

    if not items:
        log("WARN anthropic_news: sitemap에 /news/ 항목 0건 — 소스 URL 점검 필요")
        return []

    items.sort(key=lambda it: it["published"], reverse=True)
    return items[:limit]


def fetch_claude_release_notes() -> str:
    """docs.claude.com 릴리즈 노트 원본 markdown. 주 URL 실패 시 HTML 페이지 raw."""
    urls = [
        "https://docs.claude.com/en/release-notes/overview.md",
        "https://docs.claude.com/en/release-notes/overview",  # fallback: HTML
    ]
    for url in urls:
        try:
            text = http_get_text(url)
        except Exception as e:
            # requests 의 HTTPError 문자열에 status code 가 포함된다
            log(f"WARN release_notes {url}: {e}")
            continue
        if len(text) < 500:
            # 404 페이지가 200 으로 위장하는 경우 대비
            log(f"WARN release_notes {url}: 응답이 비정상적으로 짧음 ({len(text)}자)")
            continue
        return text
    log("ERROR: release notes 전체 실패 — 소스 URL 점검 필요")
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
        # feedparser.parse(url)은 timeout이 없어 원격이 응답을 끌면 job이 hang된다.
        # requests로 TIMEOUT 걸어 받은 뒤 본문만 파싱 — HTTP 실패는 safe()가 WARN 처리.
        feed = feedparser.parse(http_get_text(url))
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
