#!/usr/bin/env python3
"""collect_data 파서 self-check (네트워크 없음). 실행: python3 scripts/test_collect_data.py"""
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import collect_data  # noqa: E402
from collect_data import (  # noqa: E402
    detect_new_root_pages, fetch_anthropic_news, parse_apps_release_notes,
    parse_sitemap, parse_us_date, select_section_posts,
)

B = "https://www.anthropic.com/"


def sitemap(rows):
    body = "".join(
        f"<url><loc>{loc}</loc>" + (f"<lastmod>{lm}</lastmod>" if lm else "") + "</url>"
        for loc, lm in rows
    )
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{body}</urlset>")


ROWS = [
    (B, ""),
    (B + "news", "2026-10-14T00:00:00.000Z"),             # 인덱스 — 제외
    (B + "news/old-post", "2026-09-01T00:00:00.000Z"),
    (B + "news/new-post", "2026-10-13T10:00:00.000Z"),
    (B + "engineering/scion", "2026-10-13T16:00:00.000Z"),
    (B + "research/self-replicating-ai", "2026-10-13T00:44:16.000Z"),
    (B + "research/team/alignment", "2026-10-13T20:00:00.000Z"),  # 팀 페이지 — 제외
    (B + "legal/aup", "2026-10-13T21:00:00.000Z"),         # 대상 섹션 아님
    (B + "claude-opus-5-5", ""),                           # 루트 claude-* (lastmod 없음)
    (B + "claude-corps/fellow", ""),                       # 루트 아님 — 제외
    (B + "economic-futures", "2026-10-13T18:53:01.000Z"),  # claude-* 아님 — 제외
    (B + "claude/haiku", "2026-10-07T21:06:02.000Z"),      # /claude/ 제품 페이지 — 제외
]

# --- parse_sitemap ------------------------------------------------------------

entries = parse_sitemap(sitemap(ROWS))
assert entries == [(loc, lm) for loc, lm in ROWS], entries

# --- select_section_posts -----------------------------------------------------

posts = select_section_posts(entries)
assert [p["url"] for p in posts] == [
    B + "engineering/scion",
    B + "news/new-post",
    B + "research/self-replicating-ai",
    B + "news/old-post",
], posts
assert posts[0]["section"] == "engineering" and posts[0]["title"] == "Scion"
assert posts[2]["title"] == "Self replicating ai"

# 섹션별 상한은 섹션마다 따로 적용된다
many = [(B + f"research/r{i}", f"2026-10-{10 + i:02d}") for i in range(5)]
capped = select_section_posts(many + [(B + "news/n", "2026-01-01")],
                              limits={"news": 10, "research": 2})
assert [p["url"] for p in capped] == [B + "research/r4", B + "research/r3", B + "news/n"], capped

# --- detect_new_root_pages ----------------------------------------------------

# 기준선: 추적 시작 전부터 있던 페이지는 None으로 기록하고 내보내지 않는다
seen = {}
assert detect_new_root_pages(entries, seen, "2026-10-14", baseline=True) == []
assert seen == {B + "claude-opus-5-5": None}, seen

# 처음 보는 페이지는 그날 날짜로 기록되고 오늘·내일 실행에서 후보로 나온다
new_entries = entries + [(B + "claude-opus-6", "")]
pages = detect_new_root_pages(new_entries, seen, "2026-10-15")
assert [p["url"] for p in pages] == [B + "claude-opus-6"], pages
assert pages[0]["first_seen"] == "2026-10-15" and pages[0]["section"] == "page"
assert seen[B + "claude-opus-6"] == "2026-10-15"

again = detect_new_root_pages(new_entries, seen, "2026-10-16")
assert [p["url"] for p in again] == [B + "claude-opus-6"], again   # 하루 뒤까지는 다시 나온다
assert detect_new_root_pages(new_entries, seen, "2026-10-17") == []  # 그 뒤로는 안 나온다

# --- fetch_anthropic_news: state 파일 생성·갱신 ---------------------------------

with tempfile.TemporaryDirectory() as d:
    state = pathlib.Path(d) / "state" / "seen_sitemap.json"
    xml = sitemap(ROWS)
    collect_data.http_get_text = lambda url, **kw: xml

    first = fetch_anthropic_news("2026-10-14", seen_path=state)
    assert all(p["section"] != "page" for p in first), first          # 기준선 실행
    assert json.loads(state.read_text())["pages"] == {B + "claude-opus-5-5": None}

    xml = sitemap(ROWS + [(B + "claude-opus-6", "")])
    second = fetch_anthropic_news("2026-10-15", seen_path=state)
    assert second[0]["url"] == B + "claude-opus-6", second             # 새 페이지가 맨 앞
    assert json.loads(state.read_text())["pages"][B + "claude-opus-6"] == "2026-10-15"

    # sitemap을 못 받으면 빈 배열이고 state는 그대로
    before = state.read_text()

    def boom(url, **kw):
        raise RuntimeError("down")

    collect_data.http_get_text = boom
    assert fetch_anthropic_news("2026-10-16", seen_path=state) == []
    assert state.read_text() == before

# --- parse_us_date ------------------------------------------------------------

assert parse_us_date("October 7, 2026") == "2026-10-07"
assert parse_us_date(" September 28, 2026 ") == "2026-09-28"
assert parse_us_date("October 2026") is None
assert parse_us_date("Smarch 1, 2026") is None
assert parse_us_date("February 30, 2026") is None

# --- parse_apps_release_notes -------------------------------------------------

HTML = """
<html><body><h1>Release notes</h1>
<article class="x">
<div><h2 id="a">October 2026</h2></div>
<div><h3 id="b">October 7, 2026</h3></div>
<div><p><b>Claude Haiku 5.5 launch</b></p></div>
<div><p>We just launched Claude Haiku 5.5. See
  <b><a href="https://www.anthropic.com/claude-haiku-5-5">Claude Haiku 5.5</a></b>.</p></div>
<div><p> </p></div>
<div><ul><li><div><p><b>Cowork GA.</b> Now &amp; forever.</p></div></li>
<li><div><p>Second bullet</p></div></li></ul></div>
<div><p>After list</p></div>
<div><h2>September 2026</h2></div>
<div><h3>September 28, 2026</h3></div>
<div><p>Sonnet 5.5</p></div>
<div><h3>Not a date</h3></div>
<div><p>orphan</p></div>
</article>
<h2>Related Articles</h2><p>outside</p>
</body></html>
"""

secs = parse_apps_release_notes(HTML)
assert [s["date"] for s in secs] == ["2026-10-07", "2026-09-28"], secs
assert secs[0]["heading"] == "October 7, 2026"
assert secs[0]["text"].split("\n") == [
    "Claude Haiku 5.5 launch",
    "We just launched Claude Haiku 5.5. See Claude Haiku 5.5 "
    "(https://www.anthropic.com/claude-haiku-5-5).",
    "- Cowork GA. Now & forever.",
    "- Second bullet",
    "After list",
], secs[0]["text"]
assert secs[1]["text"] == "Sonnet 5.5", secs[1]
assert "outside" not in json.dumps(secs) and "orphan" not in json.dumps(secs)

assert len(parse_apps_release_notes(HTML, limit=1)) == 1
assert parse_apps_release_notes("<html><p>no article</p></html>") == []

print("ok")
