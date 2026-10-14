#!/usr/bin/env python3
"""
collect_data.py — GitHub Actions에서 실행되는 데이터 수집 스크립트.

결정론적 소스만 fetch해서 inbox/YYYY-MM-DD-raw.json에 저장한다.
AI 판단이 필요한 부분(한국어 요약, top 선정, specials 판정)은 Routine이 담당.

출력 스키마:
  {
    "date": "YYYY-MM-DD",
    "collected_at": ISO UTC,
    "anthropic_news": [{"title", "url", "summary", "published", "section",
                        "first_seen"(section=page만)}],
    "claude_release_notes_md": "raw markdown",
    "claude_apps_release_notes": [{"date", "heading", "text"}],
    "github_releases": {
      "claude_code": [{"title", "url", "body", "published"}],
      "sdk_python": [...],
      "sdk_typescript": [...],
    },
    "hn_ai_stories": [{"title", "url", "score", "source"}],
    "arxiv_recent": [{"title", "url", "summary", "published"}]
  }

부수 효과: state/seen_sitemap.json 갱신 (루트 claude-* 페이지의 최초 발견일).
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import sys
from html.parser import HTMLParser
from xml.etree import ElementTree

UA = "PotionBot-News/1.0 (+https://github.com/DevP0tion/AINews)"
TIMEOUT = 15

AI_KEYWORDS = [
    "ai ", "a.i.", "llm", "gpt", "claude", "anthropic", "openai",
    "gemini", "diffusion", "transformer", "neural", "machine learning",
    "deep learning", "rag", "agent", "mcp", "hugging face",
]


def log(msg: str) -> None:
    print(f"[collect] {msg}", file=sys.stderr)


# requests·feedparser는 쓰는 함수 안에서 임포트한다 — 파서만 검증하는
# self-check(test_collect_data.py)가 외부 의존성 없이 돌도록.
def http_get_json(url: str, **kwargs):
    import requests

    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, **kwargs)
    r.raise_for_status()
    return r.json()


def http_get_text(url: str, **kwargs) -> str:
    import requests

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


ANTHROPIC_BASE = "https://www.anthropic.com/"
# sitemap에서 글로 취급하는 섹션과 섹션별 상한 (lastmod 내림차순).
# research는 글이 잦지만 리포트 대상은 오늘~어제분뿐이라 상위 몇 건이면 충분하다.
SITEMAP_SECTIONS = {"news": 10, "engineering": 5, "research": 5}
# 모델 발표 페이지(/claude-opus-5-5 등)는 루트 경로에 있고 sitemap에 lastmod가 없다.
# 날짜로는 새 글을 알 수 없으므로 처음 본 날짜를 state에 기록해 판정한다.
ROOT_PAGE_PREFIX = "claude-"
SEEN_PAGES_PATH = pathlib.Path("state/seen_sitemap.json")


def parse_sitemap(xml: str) -> list[tuple[str, str]]:
    """sitemap.xml → [(loc, lastmod)]. lastmod가 없으면 빈 문자열."""
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    root = ElementTree.fromstring(xml)
    out = []
    for url_el in root.findall("sm:url", ns):
        loc = (url_el.findtext("sm:loc", "", ns) or "").strip()
        lastmod = (url_el.findtext("sm:lastmod", "", ns) or "").strip()
        if loc:
            out.append((loc, lastmod))
    return out


def slug_title(slug: str) -> str:
    s = slug.replace("-", " ")
    return s[:1].upper() + s[1:]


def select_section_posts(entries, limits=SITEMAP_SECTIONS) -> list[dict]:
    """/news/, /engineering/, /research/ 바로 아래 글을 섹션별 lastmod 내림차순 상위 N건.

    인덱스 페이지(/news/ 등)는 항상 최신 lastmod라 1위를 먹으므로 제외하고,
    두 단계 이상 깊은 경로(/research/team/*)는 팀 소개 페이지라 제외한다.
    """
    by_section: dict[str, list] = {s: [] for s in limits}
    for loc, lastmod in entries:
        if not loc.startswith(ANTHROPIC_BASE):
            continue
        parts = loc[len(ANTHROPIC_BASE):].strip("/").split("/")
        if len(parts) != 2 or parts[0] not in by_section or not parts[1]:
            continue
        by_section[parts[0]].append({
            "title": slug_title(parts[1]),
            "url": loc,
            "summary": "",
            "published": lastmod,
            "section": parts[0],
        })

    out = []
    for section, items in by_section.items():
        items.sort(key=lambda it: it["published"], reverse=True)
        out.extend(items[:limits[section]])
    out.sort(key=lambda it: it["published"], reverse=True)
    return out


def detect_new_root_pages(entries, seen: dict, target_date: str,
                          baseline: bool = False) -> list[dict]:
    """루트 경로 claude-* 페이지 중 최초 발견일이 오늘~어제인 것.

    seen({url: 최초 발견일 | None})을 in-place로 갱신한다. 처음 보는 URL은
    target_date로 기록하고, baseline이면 None(추적 시작 전부터 있던 페이지)으로 기록한다.
    하루 뒤까지 다시 내보내므로 curate가 실패한 날 재실행해도 빠지지 않는다
    (같은 페이지가 이틀 연속 나와도 daily_report의 news:: 키 중복 판정이 거른다).
    """
    today = datetime.date.fromisoformat(target_date)
    window = {target_date, (today - datetime.timedelta(days=1)).isoformat()}

    current = []
    for loc, _ in entries:
        if not loc.startswith(ANTHROPIC_BASE):
            continue
        slug = loc[len(ANTHROPIC_BASE):].strip("/")
        if "/" in slug or not slug.startswith(ROOT_PAGE_PREFIX):
            continue
        seen.setdefault(loc, None if baseline else target_date)
        current.append((loc, slug))

    return [
        {
            "title": slug_title(slug),
            "url": loc,
            "summary": "",
            "published": "",
            "first_seen": seen[loc],
            "section": "page",
        }
        for loc, slug in current
        if seen[loc] in window
    ]


def load_seen_pages(path: pathlib.Path) -> dict | None:
    """state 파일이 없거나 깨졌으면 None — 호출부가 기준선을 새로 만든다."""
    if not path.exists():
        return None
    try:
        pages = json.loads(path.read_text(encoding="utf-8")).get("pages")
    except (json.JSONDecodeError, AttributeError) as e:
        log(f"WARN seen_sitemap: {path} 파싱 실패: {e} — 기준선 재생성")
        return None
    return pages if isinstance(pages, dict) else None


def fetch_anthropic_news(target_date: str, seen_path: pathlib.Path = SEEN_PAGES_PATH):
    """anthropic.com sitemap.xml에서 Claude 섹션 후보를 고른다.

    anthropic.com은 RSS를 제공하지 않는다(/news/rss.xml, /rss.xml 모두 404).
    sitemap.xml(HTTP 200)의 <loc>+<lastmod> 쌍이 유일하게 안정적인 소스다.
      - section=news|engineering|research: lastmod 내림차순 (select_section_posts)
      - section=page: 루트 claude-* 페이지 중 새로 생긴 것 (detect_new_root_pages)
    title은 slug에서 유도한 근사값이고 summary는 비어 있다 — 정확한 제목·발행일은
    curate 단계에서 Claude가 WebFetch로 원문을 확인한다.
    실패하면 WARN 로그 후 빈 배열이고 state도 건드리지 않는다 (job은 실패시키지 않음).
    """
    try:
        entries = parse_sitemap(http_get_text(ANTHROPIC_BASE + "sitemap.xml"))
    except Exception as e:
        log(f"WARN anthropic_news: sitemap fetch/파싱 실패: {e}")
        return []

    posts = select_section_posts(entries)
    if not any(p["section"] == "news" for p in posts):
        log("WARN anthropic_news: sitemap에 /news/ 항목 0건 — 소스 URL 점검 필요")

    seen = load_seen_pages(seen_path)
    if seen is None:
        seen = {}
        detect_new_root_pages(entries, seen, target_date, baseline=True)
        log(f"seen_sitemap: 기준선 생성 ({len(seen)}건) — 이번 실행은 새 페이지 판정 생략")
        pages = []
    else:
        pages = detect_new_root_pages(entries, seen, target_date)
        if pages:
            log(f"seen_sitemap: 새 루트 페이지 {[p['url'] for p in pages]}")

    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(
        json.dumps({"pages": dict(sorted(seen.items()))}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return pages + posts


CLAUDE_APPS_RN_URL = "https://support.claude.com/en/articles/12138966-release-notes"
APPS_RN_SECTIONS = 5        # 최신 날짜 섹션 몇 개를 넘길지 (리포트 대상은 오늘~어제분뿐)
APPS_RN_SECTION_MAX = 3000  # 섹션 하나의 본문 상한 (자)
MONTHS = {
    m: i for i, m in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"], start=1)
}


def parse_us_date(text: str) -> str | None:
    """'October 7, 2026' → '2026-10-07'. 형식이 다르면 None (locale에 의존하지 않는다)."""
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text.strip())
    if not m or m.group(1).lower() not in MONTHS:
        return None
    try:
        return datetime.date(int(m.group(3)), MONTHS[m.group(1).lower()],
                             int(m.group(2))).isoformat()
    except ValueError:
        return None


class _AppsReleaseNotesParser(HTMLParser):
    """support.claude.com 릴리즈 노트 본문(<article>)을 날짜 헤딩(<h3>) 단위로 자른다.

    Intercom 마크업: <h2>월</h2> <h3>날짜</h3> <p>…</p> <ul><li><div><p>…</p></div></li></ul>
    링크는 '텍스트 (href)'로 남겨 curate 단계가 출처 URL로 쓸 수 있게 한다.
    """
    BLOCKS = {"p", "li", "h2", "h3"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_article = False
        self.li_depth = 0
        self.tag: str | None = None
        self.buf: list[str] = []
        self.href: str | None = None
        self.sections: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == "article":
            self.in_article = True
            return
        if not self.in_article:
            return
        if tag in self.BLOCKS:
            self._flush()
            self.tag = tag
        if tag == "li":
            self.li_depth += 1
        elif tag == "a":
            self.href = dict(attrs).get("href")

    def handle_endtag(self, tag):
        if tag == "article":
            self._flush()
            self.in_article = False
            return
        if not self.in_article:
            return
        if tag in self.BLOCKS:
            self._flush()
        if tag == "li":
            self.li_depth = max(0, self.li_depth - 1)
        elif tag == "a" and self.href:
            if self.tag:
                self.buf.append(f" ({self.href})")
            self.href = None

    def handle_data(self, data):
        if self.in_article and self.tag:
            self.buf.append(data)

    def _flush(self):
        text = " ".join("".join(self.buf).split())
        tag, self.tag, self.buf = self.tag, None, []
        if not text or tag == "h2":  # h2는 월 구분 헤딩이라 버린다
            return
        if tag == "h3":
            self.sections.append({"heading": text, "lines": []})
        elif self.sections:
            bullet = "- " if self.li_depth > 0 else ""
            self.sections[-1]["lines"].append(bullet + text)


def parse_apps_release_notes(html: str, limit: int = APPS_RN_SECTIONS) -> list[dict]:
    """릴리즈 노트 HTML → 최신 limit개 날짜 섹션 [{"date", "heading", "text"}]."""
    p = _AppsReleaseNotesParser()
    p.feed(html)
    p.close()
    out = []
    for s in p.sections:
        date = parse_us_date(s["heading"])
        if not date:
            log(f"WARN apps_release_notes: 날짜로 읽히지 않는 헤딩 skip: {s['heading']!r}")
            continue
        out.append({
            "date": date,
            "heading": s["heading"],
            "text": "\n".join(s["lines"])[:APPS_RN_SECTION_MAX],
        })
    out.sort(key=lambda s: s["date"], reverse=True)
    return out[:limit]


def fetch_claude_apps_release_notes() -> list[dict]:
    """Claude 앱(웹·데스크톱·모바일) 릴리즈 노트. 0건이면 WARN 후 빈 배열."""
    sections = parse_apps_release_notes(http_get_text(CLAUDE_APPS_RN_URL))
    if not sections:
        log("WARN apps_release_notes: 날짜 섹션 0건 — 페이지 구조 변경 여부 점검 필요")
    return sections


def fetch_claude_release_notes() -> str:
    """Claude Platform 릴리즈 노트 원본 markdown. 주 URL 실패 시 HTML 페이지 raw.

    docs.claude.com/en/release-notes/* 는 platform.claude.com/docs/* 로 301된다.
    리다이렉트에 기대지 않도록 이동한 주소를 직접 쓴다.
    """
    urls = [
        "https://platform.claude.com/docs/en/release-notes/overview.md",
        "https://platform.claude.com/docs/en/release-notes/overview",  # fallback: HTML
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
    import requests

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
    import feedparser

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
        "anthropic_news": safe(
            "anthropic_news", lambda: fetch_anthropic_news(target_date), [],
        ),
        "claude_release_notes_md": safe("release_notes", fetch_claude_release_notes, ""),
        "claude_apps_release_notes": safe(
            "apps_release_notes", fetch_claude_apps_release_notes, [],
        ),
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
        f"apps_release_notes={len(data['claude_apps_release_notes'])}, "
        f"gh_releases={sum(len(v) for v in data['github_releases'].values())}, "
        f"hn={len(data['hn_ai_stories'])}, arxiv={len(data['arxiv_recent'])}"
    )


if __name__ == "__main__":
    main()
