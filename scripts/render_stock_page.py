#!/usr/bin/env python3
"""
render_stock_page.py — 주식 리포트 archive JSON을 GitHub Pages용 HTML 한 장으로 렌더링.

입력: 환경변수 REPORT_DATE (YYYY-MM-DD)
읽기: archive/YYYY/MM/YYYY-MM-DD-stock.json
출력: site/index.html  (Pages 아티팩트로 업로드되는 디렉터리)

의존성 없는 순수 문자열 렌더링 — 템플릿 엔진도 브라우저도 쓰지 않는다.
등락 색상은 국내 관례를 따른다 (상승 빨강 / 하락 파랑).
"""
from __future__ import annotations

import datetime
import html
import json
import os
import pathlib
import sys
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = REPO_DIR / "site"

SITE_TITLE = "PotionBot 주식 리포트"


def log(msg: str) -> None:
    print(f"[render_stock_page] {msg}", file=sys.stderr)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def fmt_price(value, currency: str | None, *, is_stock: bool = False) -> str:
    """원화 주가는 호가 단위가 1원이라 정수로, 지수·환율은 소수 2자리로 찍는다.

    통화만으로는 구분할 수 없다 — 코스피·원달러도 currency가 KRW라서
    통화로 판정하면 6,998.16이 6,998로 잘린다. 호출부가 종류를 알려준다.
    """
    if value is None:
        return "—"
    if is_stock and currency == "KRW":
        return f"{float(value):,.0f}"
    return f"{float(value):,.2f}"


def fmt_change(change, pct) -> tuple[str, str]:
    """(표시 문자열, CSS 클래스)"""
    if pct is None and change is None:
        return "—", "flat"
    pct = float(pct or 0)
    cls = "up" if pct > 0 else "down" if pct < 0 else "flat"
    arrow = "▲" if pct > 0 else "▼" if pct < 0 else "―"
    parts = [arrow]
    if change is not None:
        parts.append(f"{abs(float(change)):,.2f}".rstrip("0").rstrip("."))
    parts.append(f"({pct:+.2f}%)")
    return " ".join(parts), cls


def fmt_volume(value) -> str:
    if value is None:
        return "—"
    v = float(value)
    if v >= 100_000_000:
        return f"{v / 100_000_000:.2f}억"
    if v >= 10_000:
        return f"{v / 10_000:.1f}만"
    return f"{v:,.0f}"


def fmt_billion(value) -> tuple[str, str]:
    """순매수 금액(원)을 억 단위로. (표시 문자열, CSS 클래스)

    수집이 없던 날은 None으로 들어온다 — 0과 구분해서 —로 표시한다.
    """
    if value is None:
        return "—", "flat"
    eok = float(value) / 100_000_000
    cls = "up" if eok > 0 else "down" if eok < 0 else "flat"
    if abs(eok) >= 10000:
        return f"{eok / 10000:+,.2f}조", cls
    return f"{eok:+,.0f}억", cls


def fmt_daylabel(date: str) -> str:
    """2026-09-17 → 09/17"""
    return date[5:].replace("-", "/") if len(date) == 10 else date


def render_investor(trend: dict) -> str:
    """투자자 수급 — 대상별로 [투자자 × 영업일] 표. 맨 오른쪽에 기간 합계."""
    dates = trend.get("dates") or []
    series = trend.get("series") or {}
    if not dates or not series:
        return ""

    head = "".join(f"<th>{esc(fmt_daylabel(d))}</th>" for d in dates)
    blocks = []
    for key, by_investor in series.items():
        rows = []
        for name, values in by_investor.items():
            cells = []
            for v in values:
                text, cls = fmt_billion(v)
                cells.append(f'<td class="{cls}">{esc(text)}</td>')
            present = [v for v in values if v is not None]
            total_text, total_cls = fmt_billion(sum(present) if present else None)
            rows.append(
                f'<tr><th class="inv-who">{esc(name)}</th>{"".join(cells)}'
                f'<td class="total {total_cls}">{esc(total_text)}</td></tr>'
            )
        blocks.append(
            f'<div class="inv-block"><h3>{esc(key)}</h3>'
            f'<div class="inv-scroll"><table class="inv">'
            f'<thead><tr><th></th>{head}<th class="total">합계</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></div>'
        )

    return (
        f'<section class="card"><h2>투자자 수급 · 순매수</h2>'
        f'{"".join(blocks)}'
        f'<p class="note">양수(빨강)는 순매수, 음수(파랑)는 순매도. '
        f'수집이 없던 날은 —.</p></section>'
    )


def render_indices(indices: list[dict]) -> str:
    if not indices:
        return ""
    cells = []
    for q in indices:
        text, cls = fmt_change(q.get("change"), q.get("change_pct"))
        cells.append(
            f'<div class="idx"><span class="idx-name">{esc(q.get("name", q["symbol"]))}</span>'
            f'<span class="idx-price">{esc(fmt_price(q.get("price"), q.get("currency")))}</span>'
            f'<span class="chg {cls}">{esc(text)}</span></div>'
        )
    return f'<section class="indices">{"".join(cells)}</section>'


def render_news_list(items: list[dict], compact: bool = False) -> str:
    if not items:
        return '<p class="empty">신규 기사 없음</p>'
    out = []
    for a in items:
        source = f'<span class="src">{esc(a["source"])}</span>' if a.get("source") else ""
        summary = f'<p class="summary">{esc(a["summary"])}</p>' if a.get("summary") and not compact else ""
        if compact and a.get("summary"):
            summary = f'<p class="summary compact">{esc(a["summary"])}</p>'
        out.append(
            f'<li><a href="{esc(a["url"])}" target="_blank" rel="noopener noreferrer">'
            f'{esc(a["title"])}</a>{source}{summary}</li>'
        )
    return f'<ul class="news">{"".join(out)}</ul>'


def render_stocks(quotes: list[dict], stock_news: dict) -> str:
    if not quotes:
        return ""
    blocks = []
    for q in quotes:
        name = q.get("name", q["symbol"])
        text, cls = fmt_change(q.get("change"), q.get("change_pct"))
        news = stock_news.get(name, [])
        blocks.append(
            f'<article class="stock">'
            f'<header class="stock-head">'
            f'<div class="stock-id"><h3>{esc(name)}</h3>'
            f'<span class="ticker">{esc(q["symbol"])}</span></div>'
            f'<div class="stock-num"><span class="price">{esc(fmt_price(q.get("price"), q.get("currency"), is_stock=True))}</span>'
            f'<span class="chg {cls}">{esc(text)}</span></div>'
            f'</header>'
            f'<div class="meta">전일종가 {esc(fmt_price(q.get("prev_close"), q.get("currency"), is_stock=True))}'
            f' · 거래량 {esc(fmt_volume(q.get("volume")))}</div>'
            f'{render_news_list(news, compact=True)}'
            f'</article>'
        )
    return f'<section class="card"><h2>관심종목</h2>{"".join(blocks)}</section>'


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#14171a;--muted:#67707a;--line:#e3e6ea;
--up:#d92d20;--down:#1d63d1;--flat:#67707a;--link:#0b5ed7;}
@media (prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#171b21;--fg:#e8eaed;
--muted:#9aa4b0;--line:#252b33;--up:#ff6b5e;--down:#5b9bff;--flat:#9aa4b0;--link:#6aa9ff;}}
*{box-sizing:border-box}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);
font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard",
"Malgun Gothic","Noto Sans KR",system-ui,sans-serif;line-height:1.55;
-webkit-text-size-adjust:100%}
.wrap{max-width:720px;margin:0 auto}
header.top{margin:8px 0 20px}
h1{font-size:1.35rem;margin:0 0 4px}
.stamp{color:var(--muted);font-size:.82rem}
.indices{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-bottom:16px}
@media(min-width:560px){.indices{grid-template-columns:repeat(4,1fr)}}
.idx{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:10px 12px;display:flex;flex-direction:column;gap:2px}
.idx-name{font-size:.78rem;color:var(--muted)}
.idx-price{font-size:1.05rem;font-weight:600;font-variant-numeric:tabular-nums}
.chg{font-size:.8rem;font-variant-numeric:tabular-nums}
.up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--flat)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:16px;margin-bottom:16px}
.card>h2{font-size:1rem;margin:0 0 12px;color:var(--muted);
letter-spacing:.02em;text-transform:uppercase}
.stock{padding:12px 0;border-top:1px solid var(--line)}
.stock:first-of-type{border-top:0;padding-top:0}
.stock-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
.stock-id{display:flex;align-items:baseline;gap:6px;min-width:0}
.stock-id h3{font-size:1.02rem;margin:0}
.ticker{font-size:.72rem;color:var(--muted)}
.stock-num{text-align:right;white-space:nowrap}
.price{font-size:1.05rem;font-weight:600;font-variant-numeric:tabular-nums;
margin-right:6px}
.meta{font-size:.76rem;color:var(--muted);margin:2px 0 8px;
font-variant-numeric:tabular-nums}
.inv-block{padding:12px 0;border-top:1px solid var(--line)}
.inv-block:first-of-type{border-top:0;padding-top:0}
.inv-block h3{font-size:.95rem;margin:0 0 8px}
.inv-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table.inv{border-collapse:collapse;width:100%;font-size:.8rem;
font-variant-numeric:tabular-nums;white-space:nowrap}
table.inv th,table.inv td{padding:5px 8px;text-align:right}
table.inv thead th{font-weight:500;color:var(--muted);font-size:.72rem;
border-bottom:1px solid var(--line)}
table.inv th.inv-who{text-align:left;font-weight:500;color:var(--fg)}
table.inv tbody tr+tr th,table.inv tbody tr+tr td{border-top:1px dashed var(--line)}
table.inv .total{font-weight:600;border-left:1px solid var(--line)}
.note{margin:10px 0 0;font-size:.72rem;color:var(--muted)}
ul.news{list-style:none;margin:0;padding:0}
ul.news li{padding:8px 0;border-top:1px dashed var(--line)}
ul.news li:first-child{border-top:0}
ul.news a{color:var(--link);text-decoration:none;font-weight:500;
word-break:keep-all;overflow-wrap:anywhere}
ul.news a:hover{text-decoration:underline}
.src{display:inline-block;margin-left:6px;font-size:.7rem;color:var(--muted);
white-space:nowrap}
.summary{margin:4px 0 0;font-size:.86rem;color:var(--fg);opacity:.85;
word-break:keep-all}
.summary.compact{font-size:.8rem}
.empty{color:var(--muted);font-size:.85rem;margin:4px 0 0}
footer{color:var(--muted);font-size:.75rem;text-align:center;margin:24px 0 8px}
footer a{color:var(--muted)}
""".strip()


def render(date: str, report: dict) -> str:
    quotes = report.get("quotes") or {}
    generated = report.get("generated_at", "")
    try:
        stamp = (
            datetime.datetime.strptime(generated, "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=datetime.timezone.utc)
            .astimezone(KST)
            .strftime("%Y-%m-%d %H:%M KST")
        )
    except ValueError:
        stamp = date

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(SITE_TITLE)} — {esc(date)}</title>
<meta name="description" content="{esc(date)} 국내 증시 브리핑 · 관심종목 시세와 주요 뉴스">
<meta property="og:title" content="{esc(SITE_TITLE)} — {esc(date)}">
<meta property="og:description" content="관심종목 시세와 국내 증시 주요 뉴스 요약">
<meta property="og:type" content="article">
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header class="top">
<h1>{esc(SITE_TITLE)}</h1>
<div class="stamp">{esc(date)} · 생성 {esc(stamp)}</div>
</header>
{render_indices(quotes.get("indices") or [])}
{render_investor(report.get("investor_trend") or {})}
{render_stocks(quotes.get("stocks") or [], report.get("stock_news") or {})}
<section class="card">
<h2>시장 주요 뉴스</h2>
{render_news_list(report.get("market_news") or [])}
</section>
<footer>
PotionBot News · 자동 수집 ·
<a href="https://github.com/DevP0tion/AINews" target="_blank" rel="noopener noreferrer">GitHub</a>
</footer>
</div>
</body>
</html>
"""


def main() -> None:
    date = os.environ.get("REPORT_DATE")
    if not date:
        log("ERROR: REPORT_DATE 입력 없음")
        raise SystemExit(4)

    year, month = date[:4], date[5:7]
    report_path = REPO_DIR / "archive" / year / month / f"{date}-stock.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        log(
            f"리포트 로드: 시장 {len(report.get('market_news', []))}건, "
            f"종목 {sum(len(v) for v in (report.get('stock_news') or {}).values())}건"
        )
    else:
        # 빈 페이지라도 배포한다 — 링크가 404가 되는 것보다 낫다
        log(f"리포트 파일 없음 — 빈 페이지 생성: {report_path}")
        report = {"quotes": {}, "market_news": [], "stock_news": {}}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "index.html"
    out.write_text(render(date, report), encoding="utf-8")
    log(f"생성 완료: {out.relative_to(REPO_DIR)} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
