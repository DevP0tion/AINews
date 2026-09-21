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


def fmt_level_context(ctx, currency: str | None) -> str:
    """레벨 컨텍스트 한 줄. 예: "고점 -12.4% · 저점 +31.0% · 20일 박스 254,000~273,250"

    박스 일수는 상수가 아니라 실제로 쓴 표본 수(`box_window`)를 찍는다 —
    상장 직후처럼 시계열이 짧으면 20일이 아니기 때문이다.
    """
    if not isinstance(ctx, dict):
        return ""
    parts = []
    high = ctx.get("pct_from_high")
    low = ctx.get("pct_from_low")
    if isinstance(high, (int, float)):
        parts.append(f"고점 {float(high):+.1f}%")
    if isinstance(low, (int, float)):
        parts.append(f"저점 {float(low):+.1f}%")
    box_low, box_high, window = ctx.get("box_low"), ctx.get("box_high"), ctx.get("box_window")
    if box_low is not None and box_high is not None and window:
        parts.append(
            f"{int(window)}일 박스 "
            f"{fmt_price(box_low, currency, is_stock=True)}~"
            f"{fmt_price(box_high, currency, is_stock=True)}"
        )
    return " · ".join(parts)


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


def fmt_axis(value: float) -> str:
    """축 눈금용 — 부호를 강제하지 않고 짧게."""
    eok = value / 100_000_000
    if abs(eok) >= 10000:
        return f"{eok / 10000:,.1f}조"
    if abs(eok) >= 1:
        return f"{eok:,.0f}억"
    return "0"


def fmt_daylabel(date: str) -> str:
    """2026-09-17 → 09/17"""
    return date[5:].replace("-", "/") if len(date) == 10 else date


# 카테고리 3색. dataviz 검증 통과 (CVD ΔE 9.2 deutan / 27.6 normal, 양 모드).
# 범례 + 직접 라벨 + 표 뷰가 함께 있어야 하는 조합이다 (light에서 aqua 대비 2.74).
SERIES_ORDER = ("외국인", "개인", "기관")
SERIES_SLOT = {"외국인": 1, "개인": 2, "기관": 3}

# 차트 기하 (viewBox 단위)
CH_W, CH_H = 300.0, 132.0
PAD_L, PAD_R, PAD_T, PAD_B = 46.0, 10.0, 12.0, 22.0


def nice_bounds(values: list[float]) -> tuple[float, float]:
    """0을 반드시 포함하는 눈금 범위. 값이 모두 같아도 납작해지지 않게 한다."""
    lo = min(list(values) + [0.0])
    hi = max(list(values) + [0.0])
    if lo == hi:
        return -1.0, 1.0
    span = hi - lo
    return lo - span * 0.08, hi + span * 0.08


def _points(values: list, dates: list, lo: float, hi: float):
    """(x, y, 값) 목록. 결측은 None 자리로 남겨 선을 끊는다."""
    n = len(dates)
    plot_w = CH_W - PAD_L - PAD_R
    plot_h = CH_H - PAD_T - PAD_B
    out = []
    for i, v in enumerate(values):
        x = PAD_L + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)
        if v is None:
            out.append(None)
            continue
        ratio = (float(v) - lo) / (hi - lo) if hi > lo else 0.5
        out.append((x, PAD_T + plot_h * (1 - ratio), v))
    return out


def render_chart(title: str, dates: list, by_investor: dict) -> str:
    """지표 하나의 선 그래프. 선 = 투자자, x = 영업일."""
    flat = [
        float(v) for values in by_investor.values()
        for v in values if v is not None
    ]
    if not flat:
        return ""
    lo, hi = nice_bounds(flat)

    plot_w = CH_W - PAD_L - PAD_R
    zero_y = PAD_T + (CH_H - PAD_T - PAD_B) * (1 - (0 - lo) / (hi - lo))

    parts = [
        f'<g class="grid">',
        f'<line x1="{PAD_L}" y1="{zero_y:.1f}" x2="{PAD_L + plot_w}" y2="{zero_y:.1f}" class="zero"/>',
        f'<text x="{PAD_L - 6}" y="{zero_y + 3:.1f}" class="tick">{esc(fmt_axis(0))}</text>',
        f'<text x="{PAD_L - 6}" y="{PAD_T + 3}" class="tick">{esc(fmt_axis(hi))}</text>',
        f'<text x="{PAD_L - 6}" y="{CH_H - PAD_B + 3}" class="tick">{esc(fmt_axis(lo))}</text>',
        "</g>",
    ]

    # x축 날짜 — 5개면 전부, 그보다 많으면 양끝과 가운데만
    n = len(dates)
    show = range(n) if n <= 5 else (0, n // 2, n - 1)
    for i in show:
        x = PAD_L + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)
        parts.append(
            f'<text x="{x:.1f}" y="{CH_H - 6}" class="tick xtick">'
            f'{esc(fmt_daylabel(dates[i]))}</text>'
        )

    for who in SERIES_ORDER:
        values = by_investor.get(who)
        if not values:
            continue
        slot = SERIES_SLOT[who]
        pts = _points(values, dates, lo, hi)
        # 결측을 만나면 선을 끊는다 (없는 값을 이어 그리면 거짓말이 된다)
        run, segments = [], []
        for pt in pts:
            if pt is None:
                if len(run) > 1:
                    segments.append(run)
                run = []
            else:
                run.append(pt)
        if len(run) > 1:
            segments.append(run)

        for seg in segments:
            d = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in seg)
            parts.append(f'<polyline points="{d}" class="ln s{slot}"/>')
        for pt in pts:
            if pt is None:
                continue
            x, y, v = pt
            text, _ = fmt_billion(v)
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" class="dot s{slot}">'
                f'<title>{esc(who)} {esc(text)}</title></circle>'
            )

    return (
        f'<figure class="chart">'
        f'<figcaption>{esc(title)}</figcaption>'
        f'<svg viewBox="0 0 {CH_W:.0f} {CH_H:.0f}" role="img" '
        f'aria-label="{esc(title)} 5영업일 추이">{"".join(parts)}</svg>'
        f'</figure>'
    )


def render_investor_table(dates: list, by_measure: dict) -> str:
    """차트와 같은 값을 읽을 수 있는 표. 접근성 대비(대비 WARN) 해소용."""
    head = "".join(f"<th>{esc(fmt_daylabel(d))}</th>" for d in dates)
    rows = []
    for measure, by_investor in by_measure.items():
        for who in SERIES_ORDER:
            values = by_investor.get(who)
            if not values:
                continue
            cells = []
            for v in values:
                text, cls = fmt_billion(v)
                cells.append(f'<td class="{cls}">{esc(text)}</td>')
            rows.append(
                f'<tr><th class="inv-who">{esc(measure)} · {esc(who)}</th>'
                f'{"".join(cells)}</tr>'
            )
    if not rows:
        return ""
    return (
        f'<details class="tableview"><summary>값으로 보기</summary>'
        f'<div class="inv-scroll"><table class="inv">'
        f'<thead><tr><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody>'
        f'</table></div></details>'
    )


def render_investor(trend: dict) -> str:
    """투자자 수급 — 종목마다 [순매수·매수·매도] 선 그래프 3개."""
    dates = trend.get("dates") or []
    series = trend.get("series") or {}
    if not dates or not series:
        return ""

    legend = "".join(
        f'<span class="lg"><i class="s{SERIES_SLOT[w]}"></i>{esc(w)}</span>'
        for w in SERIES_ORDER
    )

    blocks = []
    for key, by_measure in series.items():
        charts = [
            render_chart(m, dates, by_measure[m])
            for m in ("순매수", "매수", "매도") if m in by_measure
        ]
        charts = [c for c in charts if c]
        if not charts:
            continue
        blocks.append(
            f'<div class="inv-block"><h3>{esc(key)}</h3>'
            f'<div class="charts">{"".join(charts)}</div>'
            f'{render_investor_table(dates, by_measure)}</div>'
        )
    if not blocks:
        return ""

    return (
        f'<section class="card viz"><h2>투자자 수급 · 최근 {len(dates)}영업일</h2>'
        f'<div class="legend">{legend}</div>'
        f'{"".join(blocks)}'
        f'<p class="note">거래대금 기준. 순매수는 매수－매도이며 0선 위가 순매수, '
        f'아래가 순매도. 수집이 없던 날은 선이 끊긴다.</p></section>'
    )


def fmt_dday(days) -> str:
    """0 → D-DAY, 3 → D-3. 음수는 호출부가 걸러낸다."""
    try:
        n = int(days)
    except (TypeError, ValueError):
        return ""
    return "D-DAY" if n == 0 else f"D-{n}"


def render_calendar(events: list[dict]) -> str:
    """다가오는 이벤트. stock_report가 이미 D-day 순으로 잘라 보낸다.

    날짜가 "TBD"인 항목은 애초에 여기까지 오지 않는다.
    """
    if not events:
        return ""
    items = []
    for e in events:
        tag = fmt_dday(e.get("d_day"))
        if not tag:
            continue
        items.append(
            f'<li><span class="dday">{esc(tag)}</span>'
            f'<span class="cal-label">{esc(e.get("label", ""))}</span>'
            f'<time datetime="{esc(e.get("date", ""))}">{esc(fmt_daylabel(e.get("date", "")))}</time>'
            f'</li>'
        )
    if not items:
        return ""
    return (
        f'<section class="card cal"><h2>📅 다가오는 일정</h2>'
        f'<ul class="cal-list">{"".join(items)}</ul></section>'
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


def render_stale(flag) -> str:
    """시세 지연 표시. 수집 단계가 판정한 결과를 그대로 옮긴다."""
    if not flag:
        return ""
    return (
        '<span class="stale" role="img" aria-label="데이터 지연" '
        'title="시세 시각이 수집 시각보다 오래됐습니다 (데이터 지연)">⚠️</span>'
    )


def fmt_pct(value, *, unit: str = "%") -> tuple[str, str]:
    """(표시 문자열, CSS 클래스). 값이 없으면 —."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "—", "flat"
    v = float(value)
    cls = "up" if v > 0 else "down" if v < 0 else "flat"
    return f"{v:+.2f}{unit}", cls


def render_weekly(weekly) -> str:
    """주간 심화 섹션. 값은 전부 stock_report가 계산해 보낸 것을 옮기기만 한다."""
    if not isinstance(weekly, dict):
        return ""
    stocks = weekly.get("stocks") or []
    past = weekly.get("past_events") or []
    if not stocks and not past:
        return ""

    blocks = []
    for w in stocks:
        currency = w.get("currency")
        change, change_cls = fmt_pct(w.get("change_pct"))
        recovery, recovery_cls = fmt_pct(w.get("recovery_delta"), unit="%p")
        span = ""
        if w.get("from") and w.get("to"):
            span = f'{esc(fmt_daylabel(w["from"]))}~{esc(fmt_daylabel(w["to"]))}'
        sessions = w.get("sessions")
        blocks.append(
            f'<div class="wk-row">'
            f'<div class="wk-head"><h3>{esc(w.get("name", ""))}</h3>'
            f'<span class="chg {change_cls}">{esc(change)}</span></div>'
            f'<div class="meta">주간 고 {esc(fmt_price(w.get("week_high"), currency, is_stock=True))}'
            f' · 저 {esc(fmt_price(w.get("week_low"), currency, is_stock=True))}'
            f'{f" · {span} {esc(sessions)}거래일" if span and sessions else ""}</div>'
            f'<div class="meta">저점 대비 회복률 '
            f'<span class="{recovery_cls}">{esc(recovery)}</span> '
            f'({esc(fmt_pct(w.get("prev_pct_from_low"))[0])} → '
            f'{esc(fmt_pct(w.get("pct_from_low"))[0])})</div>'
            f'</div>'
        )

    events = ""
    if past:
        items = "".join(
            f'<li><span class="dday">D+{esc(e.get("days_ago", ""))}</span>'
            f'<span class="cal-label">{esc(e.get("label", ""))}</span></li>'
            for e in past
        )
        events = (
            f'<div class="wk-row"><h3>지난 {esc(weekly.get("days", 7))}일 이벤트</h3>'
            f'<ul class="cal-list">{items}</ul></div>'
        )

    return (
        f'<section class="card"><h2>주간 심화</h2>'
        f'{"".join(blocks)}{events}'
        f'<p class="note">전부 종가 시계열에서 계산한 값이다. 주간 변동률의 기준은 '
        f'한 주 경계 직전 종가이고, 저점 대비 회복률은 기간 저점 대비 상승률의 '
        f'주간 증감(%p)이다.</p></section>'
    )


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
        stale = render_stale(q.get("stale"))
        level = fmt_level_context(q.get("level_context"), q.get("currency"))
        level_row = f'<div class="meta level">{esc(level)}</div>' if level else ""
        blocks.append(
            f'<article class="stock">'
            f'<header class="stock-head">'
            f'<div class="stock-id"><h3>{esc(name)}</h3>'
            f'<span class="ticker">{esc(q["symbol"])}</span></div>'
            f'<div class="stock-num"><span class="price">{esc(fmt_price(q.get("price"), q.get("currency"), is_stock=True))}</span>'
            f'{stale}'
            f'<span class="chg {cls}">{esc(text)}</span></div>'
            f'</header>'
            f'<div class="meta">전일종가 {esc(fmt_price(q.get("prev_close"), q.get("currency"), is_stock=True))}'
            f' · 거래량 {esc(fmt_volume(q.get("volume")))}</div>'
            f'{level_row}'
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
.meta.level{margin-top:-6px}
.stale{margin-right:4px;font-size:.85rem;cursor:help}
ul.cal-list{list-style:none;margin:0;padding:0}
ul.cal-list li{display:flex;align-items:baseline;gap:8px;padding:6px 0;
border-top:1px dashed var(--line);font-size:.88rem}
ul.cal-list li:first-child{border-top:0}
.dday{flex:none;min-width:46px;font-size:.74rem;font-weight:600;
font-variant-numeric:tabular-nums;color:var(--up)}
.cal-label{flex:1;word-break:keep-all}
ul.cal-list time{flex:none;font-size:.74rem;color:var(--muted);
font-variant-numeric:tabular-nums}
.wk-row{padding:12px 0;border-top:1px solid var(--line)}
.wk-row:first-of-type{border-top:0;padding-top:0}
.wk-row h3{font-size:1rem;margin:0}
.wk-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
.wk-row .meta{margin:4px 0 0}
/* 카테고리 3색 — dataviz 검증 통과. 다크는 같은 hue를 어두운 면에 맞춰 다시 뽑은 값 */
.viz{--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) .viz{
--s1:#3987e5;--s2:#d95926;--s3:#199e70}}
:root[data-theme="dark"] .viz{--s1:#3987e5;--s2:#d95926;--s3:#199e70}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin:-4px 0 12px}
.lg{display:inline-flex;align-items:center;gap:5px;font-size:.76rem;color:var(--muted)}
.lg i{width:14px;height:2px;border-radius:1px;display:inline-block}
.lg i.s1{background:var(--s1)}.lg i.s2{background:var(--s2)}.lg i.s3{background:var(--s3)}
.inv-block{padding:14px 0;border-top:1px solid var(--line)}
.inv-block:first-of-type{border-top:0;padding-top:0}
.inv-block h3{font-size:.95rem;margin:0 0 10px}
.charts{display:grid;gap:14px}
@media(min-width:620px){.charts{grid-template-columns:repeat(3,1fr)}}
figure.chart{margin:0}
figure.chart figcaption{font-size:.74rem;color:var(--muted);margin-bottom:2px}
figure.chart svg{width:100%;height:auto;display:block;overflow:visible}
svg .zero{stroke:var(--line);stroke-width:1}
svg .tick{fill:var(--muted);font-size:8px;text-anchor:end}
svg .xtick{text-anchor:middle}
svg .ln{fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
svg .dot{stroke:var(--card);stroke-width:2}
svg .s1{stroke:var(--s1)}svg .s2{stroke:var(--s2)}svg .s3{stroke:var(--s3)}
svg circle.s1{fill:var(--s1)}svg circle.s2{fill:var(--s2)}svg circle.s3{fill:var(--s3)}
.tableview{margin-top:10px}
.tableview summary{font-size:.74rem;color:var(--muted);cursor:pointer}
.inv-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin-top:8px}
table.inv{border-collapse:collapse;width:100%;font-size:.78rem;
font-variant-numeric:tabular-nums;white-space:nowrap}
table.inv th,table.inv td{padding:5px 8px;text-align:right}
table.inv thead th{font-weight:500;color:var(--muted);font-size:.72rem;
border-bottom:1px solid var(--line)}
table.inv th.inv-who{text-align:left;font-weight:500;color:var(--fg)}
table.inv tbody tr+tr th,table.inv tbody tr+tr td{border-top:1px dashed var(--line)}
.note{margin:12px 0 0;font-size:.72rem;color:var(--muted)}
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
{render_calendar(report.get("calendar") or [])}
{render_indices(quotes.get("indices") or [])}
{render_investor(report.get("investor_trend") or {})}
{render_stocks(quotes.get("stocks") or [], report.get("stock_news") or {})}
{render_weekly(report.get("weekly"))}
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
