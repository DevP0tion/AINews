# AINews

PotionBot News 일일 리포트 저장소.
**GitHub Actions + Claude Code Action**으로 매일 06:30 KST에 자동 실행되어
두 가지 리포트를 Discord로 전송한다.

| 리포트 | 내용 | 전송 형태 | 채널 |
|---|---|---|---|
| **AI/IT** | AI/IT 뉴스 + Claude/Anthropic 업데이트 | Discord embed 2개 | `DISCORD_WEBHOOK_POTIONBOT_NEWS` |
| **주식** | 지수·관심종목 시세 + 증시 뉴스 | GitHub Pages 링크 한 줄 | `DISCORD_WEBHOOK_POTIONBOT_STOCK` |

실행 시각이 06:30 KST인 이유: 미 증시 정규장 마감(06:00 KST, 서머타임 05:00)보다 뒤라
전일 미국 지수 종가가 확정돼 있고, 국내 증시 개장(09:00)보다 앞선다.

## 아키텍처

```
[매일 06:30 KST cron]
   ↓
[Job 1: collect] ── 결정론적 fetching (Python)
   ├ collect_data.py  : Anthropic 뉴스 sitemap / Claude 릴리즈 노트 / GitHub Releases / HN AI / arxiv
   │                    → inbox/YYYY-MM-DD-raw.json
   ├ collect_stock.py : 국내 증시·경제 RSS 5종 / Yahoo Finance 지수·관심종목 시세
   │                    → inbox/YYYY-MM-DD-stock-raw.json
   └ collect_investor.py : KRX 투자자별 매수·매도·순매수 **직전 영업일 하루치**
                        → state/investor_trend.json 에 누적 (리포트는 최근 5영업일 사용)
   · 세 산출물 커밋
   ↓
[Job 2: curate]
   ├ (a) Claude Code Action (anthropics/claude-code-action@v1)
   │    · CLAUDE_CODE_OAUTH_TOKEN 인증 (Pro/Max 구독 사용, 별도 결제 없음)
   │    · 모델은 daily.yml의 claude_args에서 --model로 지정한다 (미지정 시 액션 기본값)
   │    · 프롬프트는 prompts/{common,ai_news,stock}.md 를 이어붙여 전달 (Claude 실행은 1회)
   │    · 파트 1: inbox 읽어서 한국어 요약·top 선정·specials 판정 → /tmp/processed.json
   │    · 파트 2: 증시 뉴스 선정·요약·종목 매칭        → /tmp/processed_stock.json
   │    · 산출물은 이 두 파일뿐. 여기서 Claude의 역할 종료
   │    · 도구는 Read/Write/WebFetch만 — Bash 없음, git 자격증명 없음
   │    · 시세 숫자는 Claude를 거치지 않는다 (inbox에서 직접 읽어 씀)
   ├ (b) Process report → Commit & push   : daily_report.py → 검증·중복 제거·archive/state
   └ (c) Process stock report → Commit & push : stock_report.py → 동일 (state는 별도 파일)
        · 리포트별로 [처리 → 커밋]이 분리돼 있고, 커밋 스텝에만 토큰이 주입된다
   ↓
[Job 3: publish] ── AI/IT를 Discord embed로 전송
   · archive/YYYY/MM/YYYY-MM-DD.json 읽어서 webhook POST
   ↓
[Job 4: pages] ── archive의 **모든 날짜**를 정적 HTML로 렌더링해서 GitHub Pages 배포
   · render_stock_page.py → site/{날짜}/index.html + site/index.html(최신)
   · actions/deploy-pages
   ↓
[Job 5: publish_stock] ── 그날 날짜 페이지 링크 한 줄만 주식 채널로 전송
```

### 실패 격리

**주식 쪽이 깨져도 AI/IT 리포트는 정상 전송된다.**

Job 2에서 AI/IT 처리·커밋이 먼저 끝난 뒤 주식 처리가 시작된다.
주식 스텝은 `continue-on-error`라 실패해도 job 자체는 성공으로 끝나고,
결과는 `curate.outputs.stock_outcome`으로 후속 job에 전달된다.

| 상황 | Job 3 (AI/IT 전송) | Job 4~5 (주식 배포·전송) |
|---|---|---|
| 둘 다 정상 | 전송 | 배포 후 링크 전송 |
| 주식만 실패 | **전송** | 스킵 (`::warning::` 로그) |
| AI/IT 실패 | 스킵 | 스킵 (job이 거기서 중단) |

반대 방향(AI/IT 실패 시 주식만 살리기)은 지원하지 않는다.
AI/IT 처리가 실패하면 Claude 출력 자체가 잘못됐을 가능성이 높아 주식도 신뢰하기 어렵다.

**장점**
- Routine/로컬 PC 불필요 — 전부 GitHub 인프라에서 실행
- 구독 토큰 사용으로 **API 비용 없음** (Pro/Max 한도 내)
- secrets 관리 일원화 (webhook + OAuth 토큰)
- 실행 이력/로그 Actions 탭에서 자동 확인
- 수동 재실행은 Actions UI에서 버튼 하나

## 구조

```
AINews/
├── README.md
├── .github/
│   └── workflows/
│       └── daily.yml                 # 통합 워크플로 (collect → curate → publish/pages)
├── prompts/                          # Claude에 전달되는 프롬프트 (이 순서로 조립됨)
│   ├── common.md                     # 공통 — 환경·산출물·보안/작성 원칙
│   ├── ai_news.md                    # 파트 1 — AI/IT 리포트
│   └── stock.md                      # 파트 2 — 주식 리포트
├── config/
│   ├── watchlist.json                # 관심종목·지수 목록 (여기만 고치면 종목 추가됨)
│   └── calendar.json                 # 이벤트 캘린더 (날짜는 직접 기입, "TBD"는 미표시)
├── scripts/
│   ├── collect_data.py               # Job 1: AI/IT 소스 fetching
│   ├── collect_stock.py              # Job 1: 증시 RSS + Yahoo Finance 시세
│   ├── collect_investor.py           # Job 1: KRX 투자자별 매매동향 (하루치 누적)
│   ├── collect_research.py           # Job 1: 애널리스트 컨센서스 + 증권사 리포트
│   ├── daily_report.py               # Job 2(b): AI/IT 검증·필터·archive/state
│   ├── stock_report.py               # Job 2(c): 주식 검증·필터·archive/state
│   ├── level_context.py              # 종가 시계열 → 기간 고저·박스권·주간 통계 (순수 함수)
│   ├── render_stock_page.py          # Job 4: archive JSON → site/index.html
│   ├── send_discord.py               # Job 3/5: Discord 전송 (--stock으로 링크 모드)
│   ├── test_daily_report.py          # 중복 키 self-check
│   ├── test_stock_report.py          # 주식 검증·중복·재시도·렌더링 self-check
│   └── test_level_context.py         # 레벨 컨텍스트·주간 통계 self-check
├── state/
│   ├── seen_urls.json                # AI/IT 뉴스 URL 인덱스 (영구 누적)
│   ├── seen_claude.json              # Claude 업데이트 항목 키
│   ├── seen_stock_urls.json          # 주식 뉴스 URL 인덱스 (AI/IT와 분리)
│   ├── investor_trend.json           # 투자자 순매수 일별 누적 (최근 40일 보관)
│   ├── research_reports.json         # 증권사 리포트 누적 (최근 180일 보관)
│   └── consensus_trend.json          # 목표주가·투자의견 일별 누적 (최근 180일)
├── inbox/                            # Job 1 출력
│   ├── YYYY-MM-DD-raw.json
│   ├── YYYY-MM-DD-stock-raw.json     # curate가 읽는 파일 (뉴스·시세)
│   ├── YYYY-MM-DD-stock-history.json # 종가 시계열 (후속 스텝 전용)
│   └── YYYY-MM-DD-research.json      # 컨센서스·리포트 (후속 스텝 전용)
├── archive/                          # Job 2 출력
│   ├── YYYY/MM/YYYY-MM-DD.{json,md}
│   └── YYYY/MM/YYYY-MM-DD-stock.json
└── site/                             # Job 4 생성물 (gitignore — 매 실행마다 새로 만듦)
    ├── index.html                     # 최신 리포트
    └── YYYY-MM-DD/index.html          # 날짜별 리포트
```

### 사이트 구조

매 배포마다 `archive/**/*-stock.json`을 전부 읽어 날짜별 페이지를 새로 만든다.

- `/` — 최신 리포트. 루트로 들어온 사람에게 보이는 화면
- `/YYYY-MM-DD/` — 그날 리포트. **Discord 링크는 이 주소로 나간다**.
  나중에 지난 메시지를 눌러도 그날 내용이 그대로 나온다
- 좌측 **드로어**에 전체 날짜 목록이 월별로 접혀 있고, 보고 있는 날짜는 표시된다.
  헤더에는 하루씩 넘기는 `‹ 이전 / 다음 ›` 링크가 있다

드로어는 **JS 없이 checkbox로** 토글한다. 화면이 1040px보다 넓으면 늘 펼쳐진 채
본문을 밀어내고, 좁으면 ☰ 버튼으로 여닫는다.

> Pages는 `/AINews/` 하위에 배포되므로 **페이지 안의 링크는 전부 상대경로**다.
> 루트 문서는 `2026-09-21/`, 날짜 문서는 `../2026-09-21/`로 쓴다.
> 절대경로(`/2026-09-21/`)로 바꾸면 전부 깨진다.

### 프롬프트 수정하기

Claude에게 전달되는 지침은 `prompts/` 아래 세 파일에 나뉘어 있고,
워크플로의 `Load prompt` 스텝이 **이 순서대로 이어붙여** 한 번에 전달한다.

| 파일 | 고칠 때 |
|---|---|
| `common.md` | 양쪽에 걸리는 규칙 — 언어, 보안(프롬프트 주입 방어), 저장소 파일 수정 금지, 요약 원칙, 추가 수집 한도 |
| `ai_news.md` | AI/IT 뉴스 선정 기준, Claude 업데이트 카테고리, `special` 판정, `id` 규칙 |
| `stock.md` | 증시 뉴스 선정 기준, 종목별 뉴스 건수, 출력 스키마 |

- 조립 순서는 `daily.yml`의 `PROMPT_FILES` 환경변수에서 바꾼다
- 런타임 값은 `{{TARGET_DATE}}` `{{INBOX_PATH}}` `{{STOCK_INBOX_PATH}}` 세 개뿐이고,
  `sed`로 치환된다. 치환되지 않은 `{{...}}`가 남으면 스텝이 에러로 멈춘다
- 출력 스키마(`/tmp/processed*.json`)를 고치면 `daily_report.py` / `stock_report.py`의
  검증 로직도 같이 고쳐야 한다. 스키마를 벗어난 항목은 조용히 drop된다

### 추가 정보 수집

제목·요약만으로 판단이 안 서면 curate 단계의 Claude가 **`WebFetch`로 원문을 직접 확인**한다.
기준과 한도는 `prompts/common.md`의 «추가 정보 수집» 절에 있다.

- **한도는 파트 1·2를 합쳐 최대 8회.** 늘리려면 그 절의 숫자를 고친다
- fetch할 수 있는 것은 **inbox의 `url` 값과 그렇게 가져온 페이지 안의 실제 링크뿐**이다.
  URL을 추측해서 만들지 않도록 명시해 뒀다 (존재하지 않는 주소를 지어내는 일이 잦다)
- 실패해도 재시도하지 않고 넘어간다. 리포트 생성 자체는 멈추지 않는다
- `WebSearch`는 여전히 차단돼 있다 (`daily.yml`의 `--disallowedTools`).
  검색으로 새 소스를 찾는 것은 불가능하고, 이미 아는 URL의 원문만 읽는다
- 한도를 늘리면 curate job 실행 시간이 늘어난다 (현재 Claude 스텝 약 2분)

### 관심종목 추가하기

`config/watchlist.json`의 `stocks` 배열에 항목을 추가하면 된다. 코드 수정은 필요 없다.

```json
{
  "symbol": "035420.KS",
  "name": "NAVER",
  "aliases": ["NAVER", "네이버"]
}
```

- `symbol`: Yahoo Finance 티커. KOSPI는 `6자리.KS`, KOSDAQ은 `6자리.KQ`
- `name`: 리포트에 표시되는 이름. Claude가 종목별 뉴스를 묶을 때 쓰는 키이기도 하다
- `aliases`: RSS 헤드라인에서 이 종목을 찾을 때 쓰는 별칭. 약칭·영문명을 넣으면 매칭률이 오른다

## 수집 소스 (collect_data.py)

- **Anthropic News** — https://www.anthropic.com/sitemap.xml 의 `/news/` 항목을 `lastmod` 내림차순 10건 (anthropic.com은 RSS 미제공)
- **Claude 릴리즈 노트** — https://docs.claude.com/en/release_notes/overview.md (raw markdown)
- **GitHub Releases API** — `anthropics/claude-code`, `anthropics/anthropic-sdk-python`, `anthropics/anthropic-sdk-typescript`
- **Hacker News** top stories 중 AI 키워드 매치
- **arxiv** cs.LG / cs.CL 최신 (참고용)

## 수집 소스 (collect_stock.py)

**뉴스** — 피드당 최근 25건, 발행 30시간 이내만 채택하고 URL 기준으로 합친다.

| 출처 | URL |
|---|---|
| 매일경제 증권 | `https://www.mk.co.kr/rss/50200011/` |
| 연합뉴스 경제 | `https://www.yna.co.kr/rss/economy.xml` |
| 한국경제 금융 | `https://www.hankyung.com/feed/finance` |
| 한국경제 경제 | `https://www.hankyung.com/feed/economy` |
| 전자신문 증권 | `https://rss.etnews.com/Section902.xml` |

한국경제는 증권 전용 피드(`/feed/stock`)가 404라 금융·경제로 대체했다.

**시세** — Yahoo Finance chart API (`query1.finance.yahoo.com/v8/finance/chart/{symbol}`, API 키 불필요).
`range=1d`의 `meta.chartPreviousClose`를 전일 종가로, `regularMarketPrice`를 현재가로 쓴다.
대상은 `config/watchlist.json`의 `indices`(코스피/코스닥/나스닥/원달러)와 `stocks`.

**종가 시계열** — 관심종목에 한해 `range=6mo&interval=1d`로 일봉 종가도 받아
**별도 파일** `inbox/YYYY-MM-DD-stock-history.json`에 `{symbol: [{"date","close"}]}`
형태로 담는다. 레벨 컨텍스트와 주간 통계의 재료다.

`-stock-raw.json`과 분리한 이유 — curate 단계의 Claude는 raw 파일을 `Read`로
통째로 읽는다. 뉴스 선정에 전혀 쓰지 않는 6개월치 배열(종목당 ~125일)이 같은
파일에 있으면 프롬프트만 수만 토큰 불어난다. 시계열은 `stock_report.py`만 읽는다.

지수는 받지 않는다 (표시하지 않으므로 호출만 늘어난다). 휴장·거래정지로
`close`가 `null`인 날은 통째로 버린다 — 앞 값으로 메우면 없던 거래일을
만들어내는 셈이다. 날짜는 거래소 타임존 기준이다.

시계열 파일이 없어도 에러가 아니다. 레벨 컨텍스트와 주간 섹션만 빠지고
시세·뉴스 리포트는 그대로 나간다.

**재시도** — 모든 Yahoo 호출에 3회 재시도 + 1→2→4초 지수 백오프(`FETCH_RETRIES`,
`FETCH_BACKOFF`). runner에서 간헐적 DNS 실패가 실측된 적이 있어, 한 번 실패했다고
그날 그 종목 시세를 통째로 비우지 않기 위한 것이다.

**시세 신선도** — `meta.regularMarketTime`을 `quote_time`(ISO UTC)으로 저장하고,
수집 시각과의 차이가 24시간(`STALE_THRESHOLD_HOURS`)을 넘으면 `stale: true`.
페이지에서는 가격 옆에 ⚠️로 표시된다. **파이프라인은 세우지 않는다** — 지연된
값이라도 없는 것보다 낫고, 경고만 남긴다. 지수에는 붙이지 않는다: 해외 지수는
주말·휴일이면 정상적으로 며칠 전 종가라 오탐만 난다.

**종목 매칭** — 수집 단계에서 제목·요약에 `aliases`가 들어가면 `matched` 필드에 기계적으로 표시한다.
오탐이 있을 수 있어 최종 판단은 curate 단계의 Claude가 한다.

## 레벨 컨텍스트 (level_context.py)

등락률만으로는 지금이 고점 근처인지 저점에서 반등 중인지 알 수 없다.
종목 줄에 한 줄을 더해 위치를 보여준다.

```
고점 -24.4% · 저점 +63.9% · 20일 박스 248,500~274,000
```

| 값 | 뜻 |
|---|---|
| `period_high` / `period_low` | 수집 기간(`HISTORY_RANGE`, 기본 6개월) 종가 고·저 |
| `pct_from_high` / `pct_from_low` | 마지막 종가의 고점·저점 대비 변화율 (%) |
| `box_high` / `box_low` | 최근 `BOX_WINDOW`(기본 20) 거래일 종가의 고·저 |
| `box_window` | **실제로 쓴 표본 수**. 시계열이 짧으면 20보다 작고, 표기도 그 수를 따른다 |

계산은 `stock_report.py`가 시계열 파일을 읽어 직접 한다. curated JSON에
같은 이름의 필드가 있어도 읽지 않고 버린다 — 리포트의 숫자는 수집 원본과
결정론적 계산에서만 나온다. `history` 자체는 archive에 싣지 않는다
(매일 6개월치를 커밋하면 저장소만 불어난다).

## 이벤트 캘린더 (config/calendar.json)

실적 발표·FOMC처럼 미리 알면 뉴스 해석이 달라지는 일정을 페이지 최상단에
`📅 D-n 라벨` 형태로, D-day 오름차순으로 띄운다. 범위는 오늘부터
`CALENDAR_LOOKAHEAD_DAYS`(기본 14)일 이내다.

```json
[
  {"date": "2026-10-08", "label": "삼성전자 3분기 잠정실적 발표", "ticker": "005930.KS"},
  {"date": "TBD", "label": "다음 FOMC 정례회의 결과 발표"}
]
```

- `date`: `YYYY-MM-DD`, 또는 아직 모르면 `"TBD"`. **`"TBD"` 항목은 표시되지 않는다** —
  날짜는 직접 확인해서 기입한다. 틀린 D-day는 아예 없느니만 못하다
- `label`: 표시 문구 (필수)
- `ticker`: 선택. `watchlist`의 `symbol`
- `_comment`만 있는 원소는 무시된다 (JSON 배열에는 주석을 못 쓰므로)

## 주간 심화 섹션

리포트 대상 날짜가 **KST 월요일**(`WEEKLY_DAY`)일 때만 붙는다.
내용은 전부 종가 시계열에서 계산한 값이다.

| 값 | 계산 |
|---|---|
| 주간 변동률 | 한 주 경계(7일 전) 직전 종가 대비 마지막 종가 |
| 주간 고·저 | 경계 이후 종가의 최대·최소 |
| 저점 대비 회복률 증감 | 기간 저점 대비 상승률의 주간 증감 (%p) |
| 지난 이벤트 | `calendar.json`에서 지난 7일 안에 지나간 항목 |
| 목표주가 변화 | 애널리스트 평균 목표가의 전주 대비 증감 (`consensus_trend.json`) |

전주 값은 **과거 archive를 읽지 않고** 시계열을 경계 이전까지 잘라 다시
계산한다. 같은 `history`면 언제 돌려도 같은 값이 나온다.

요일 판정은 `strftime("%A")` 대신 고정 목록을 쓴다 — `%A`는 runner 로케일을 탄다.

## 애널리스트 컨센서스·증권사 리포트 (collect_research.py)

두 소스를 모아 관심종목 블록에 붙인다.

```
목표주가 평균 475,850 (괴리 +73.7%) · 매수 35 · 보유 1 · 매도 0
  삼성전자(005930) 무시할 실적이 아니다   2026-07-31 · IBK투자증권 · 김운호  [매수] [목표 460,000]
```

### 1. 컨센서스 — Yahoo Finance (`yfinance`)

| 값 | 내용 |
|---|---|
| `price_targets` | 목표주가 `current` / `high` / `low` / `mean` / `median` |
| `recommendations` | 투자의견 분포 `strongBuy`·`buy`·`hold`·`sell`·`strongSell`, 최근 4개월 |

`collect_stock.py`가 쓰는 chart API와 달리 **쿠키+crumb 인증이 필요해** 직접
호출하면 401이 난다. `yfinance`가 그 과정을 대신하므로 이 부분만 라이브러리를 쓴다.
`upgrades_downgrades`(개별 증권사 상향·하향 이력)는 국내 종목엔 404라 받지 않는다.

**추이는 따로 쌓는다.** 컨센서스는 그날 스냅샷이라 "목표가가 올라갔는지"를 알 수
없다. 하루치씩 `state/consensus_trend.json`에 누적하고(같은 날 재실행은 덮어쓴다),
주간 섹션이 거기서 전주 값을 읽어 `목표주가 평균 460,000 → 475,850 (+3.45%)`로
보여준다. 쌓는 것은 평균·중앙값과 의견 합계뿐이다 — 목표가 전 구간과 의견 분포를
매일 저장하면 state만 커진다.

"전주"는 경계(7일 전) **이전에서 가장 최근에 기록된 날**이다. 수집이 빠진 날이
있어도 그 앞 기록을 쓰고, 경계 이전 기록이 아예 없으면(수집 시작 직후) 표시하지
않는다 — 비교할 대상이 없는 것을 0%로 쓰면 거짓말이 된다.

### 2. 리포트 목록 — 한경 컨센서스

`consensus.hankyung.com`의 기업 리포트(`report_type=CO`) 목록에서 관심종목 것만
추린다. 종목코드는 제목의 `(005930)`이 아니라 **차트 링크의 `business_code`**에서
꺼낸다 — 제목 표기가 매번 같다는 보장이 없다.

**왜 누적 방식인가** — 관심종목 리포트는 실적 시즌에 몰려 나온다. 실측으로 60일
600건 중 관심종목은 4건이었다. 매 실행 최근 7일만 긁어 `state/research_reports.json`에
쌓고(`report_idx` 기준 중복 제거), 리포트는 거기서 종목당 최근 3건을 읽는다.
누적분이 비어 있는 첫 실행만 180일을 거슬러 백필한다.

| 항목 | 값 |
|---|---|
| 조회 범위 | 최근 7일 (`RESEARCH_LOOKBACK_DAYS`), 첫 실행은 180일 (`BACKFILL_DAYS`) |
| 표시 | 종목당 3건 (`REPORTS_PER_STOCK`) |
| 보관 | 180일 (`REPORT_KEEP_DAYS`). 그보다 오래됐어도 종목당 최소 3건은 남긴다 |
| 요청 수 | 하루 1회 (7일치 ≈ 60건이 `pagenum=100` 한 페이지에 들어온다) |

### 실패해도 리포트는 나간다

`collect_investor.py`와 같이 모든 예외를 삼키고 종료 코드 0으로 끝난다.
`stock_report.py`도 `inbox/YYYY-MM-DD-research.json`이 없으면 해당 섹션만 빼고
넘어간다. 시세·뉴스 리포트는 영향받지 않는다.

## 투자자 수급 (collect_investor.py)

외국인·개인·기관의 매매동향을 KRX에서 받아 리포트 상단(지수 바로 아래)에
**선 그래프**로 보여준다. 종목마다 `순매수 / 매수 / 매도` 그래프 3개가 나오고,
각 그래프의 선 3개가 외국인·개인·기관이다. x축은 최근 5영업일.

매수·매도를 따로 저장하는 이유 — `순매수 +100억`은 1조 매수/9,900억 매도일 수도,
100억 매수뿐일 수도 있다. 거래 규모가 보여야 그 차이를 읽는다.

**왜 누적 방식인가** — KRX는 기간 조회가 가능하지만, 매 실행마다 **직전 영업일 하루치만**
받아 `state/investor_trend.json`에 쌓는다. 리포트는 그 누적분에서 최근 5영업일을 읽는다.
하루 단위로 기록해두면 어느 날 수집이 실패해도 나머지 날짜는 남고, 재실행 시
같은 날짜를 덮어쓰므로 중복이 생기지 않는다.

| 항목 | 값 |
|---|---|
| 대상 | `watchlist`의 각 종목만 (시장 집계는 받지 않는다) |
| 지표 | `매수` / `매도` / `순매수` (거래대금 기준) |
| 투자자 구분 | `외국인` / `개인` / `기관`(= KRX의 `기관합계` 행) |
| 단위 | 원 (페이지에서는 억/조로 환산) |
| 보관 | 40일 (`KEEP_DAYS`), 표시 5영업일 (`INVESTOR_DAYS`) |

**그래프 규칙** — 계열 색은 `dataviz` 검증을 통과한 3색이고 라이트/다크가 각각
다른 값이다. 0선을 항상 포함해 추세가 과장되지 않게 하고, 수집이 없던 날은
선을 끊는다(없는 값을 이어 그리면 거짓말이 된다). 색만으로 구분시키지 않도록
범례와 `값으로 보기` 표를 함께 둔다.

**스키마가 바뀌어도 예전 데이터를 읽는다** — 투자자별로 순매수 숫자 하나만
저장하던 형태도 읽어서 순매수 그래프에만 쓰고, 매수·매도는 결측으로 남긴다.

**인증이 필요하다.** KRX 정보데이터시스템이 로그인을 요구하도록 바뀌어,
`pykrx`가 `KRX_ID`/`KRX_PW` 환경변수로 세션을 만든다.
**둘 중 하나라도 없으면 조용히 건너뛴다** — 투자자 수급 섹션만 빠지고
시세·뉴스는 그대로 수집된다. 로그인 실패·응답 형식 변경도 같은 방식으로 흡수한다.

> 익명 접근은 막혀 있다. KRX 직접 호출은 `LOGOUT`을, 네이버 금융은 410/302를 반환하고,
> KRX 공식 OPEN API에는 투자자별 거래실적 항목이 없다. 계정 방식이 현재 유일한 경로다.

## 중복 판정

- **뉴스**: URL 정규화 (scheme/host 소문자화, trailing slash 제거, tracking param 제거, fragment 제거) 후 완전 일치
- **Claude 업데이트**: curate 단계가 부여한 `id` 완전 일치.
  `id`는 원본에서 따온 안정적 값 — `gh::{repo}::{tag}` / `news::{url}` / `rn::{날짜}::{키워드}`.
  (title은 매일 재작성되는 자유 문구라 키로 쓰지 않는다.)
  - `id`가 없는 레거시 항목은 `{category}::{title_normalized}` 키로 fallback
  - `gh::`·`news::` 항목은 `url::{normalized_url}`도 보조 키로 함께 대조
    (릴리즈 노트는 여러 항목이 같은 overview URL을 공유하므로 제외)
- **주식 뉴스**: URL 정규화 후 완전 일치. 인덱스는 `state/seen_stock_urls.json`으로
  AI/IT 뉴스와 분리돼 있어 한쪽이 다른 쪽 기사를 가리지 않는다.
  시장 전반 뉴스와 종목별 뉴스가 같은 실행 안에서 겹치는 것은 허용한다.
- **윈도우**: 영구 (state/seen_*.json 누적)

## 셋업

### 1. GitHub Secrets 등록

Repo → Settings → Secrets and variables → Actions → New repository secret:

- **`CLAUDE_CODE_OAUTH_TOKEN`**
  - 로컬에서 생성: 터미널에서 `claude setup-token` 실행
  - 브라우저 OAuth 플로우 완료 후 출력되는 토큰 (유효기간 1년) 복사
  - ⚠️ Pro/Max/Team/Enterprise 구독 필요
- **`DISCORD_WEBHOOK_POTIONBOT_NEWS`**
  - AI/IT 리포트를 받을 채널. Discord 채널 설정 → Integrations → Webhooks에서 발급한 URL
- **`DISCORD_WEBHOOK_POTIONBOT_STOCK`**
  - 주식 리포트를 받을 채널. 같은 방식으로 **다른 채널에서** 발급한 URL
- **`KRX_ID`**, **`KRX_PW`** *(선택 — 투자자 수급을 쓸 때만)*
  - [KRX 정보데이터시스템](https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS002_S0.cmd) 가입 후 아이디/비밀번호
  - 미설정 시 투자자 수급 섹션만 빠지고 나머지는 정상 동작한다
  - 자동 수집이 이용약관에 저촉되지 않는지 직접 확인할 것. 전용 계정 사용을 권장한다

### 2. GitHub Actions 권한

Settings → Actions → General → Workflow permissions:

- ✅ **Read and write permissions** (archive/state 커밋에 필요)

### 3. GitHub Pages 활성화

Settings → Pages → Build and deployment → Source를 **GitHub Actions**로 변경한다.
(기본값인 "Deploy from a branch"로 두면 `pages` job의 배포가 실패한다.)

배포 주소는 `https://devp0tion.github.io/AINews/` 이고, **공개 저장소이므로 페이지도 공개**다.
날짜별 페이지가 함께 배포되므로 과거 리포트도 웹에서 볼 수 있다.

### 4. (선택) Anthropic GitHub App 설치

로컬에서 한 번 실행:

```bash
claude              # Claude Code CLI
/install-github-app
```

안내 따라 DevP0tion/AINews에 앱 설치. 스케줄 자동화에 필수는 아니지만, curate job이 GitHub API를 호출할 때 권한 경고가 덜 뜬다.

### 5. 첫 실행 검증

Actions 탭 → **Daily Report** → **Run workflow** (manual trigger):
- `date`: 빈칸 (오늘 KST 자동) 또는 특정 날짜 입력

성공 체크리스트:
- [ ] `collect` job: `inbox/YYYY-MM-DD-raw.json`, `inbox/YYYY-MM-DD-stock-raw.json` 커밋됨
- [ ] `curate` job — Claude 스텝: Bash 도구 사용 흔적 없이 `/tmp/processed.json`과 `/tmp/processed_stock.json` 생성
- [ ] `curate` job — Process report 스텝: 요약 JSON 출력, `archive/YYYY/MM/YYYY-MM-DD.json` 생성
- [ ] `curate` job — Commit & push report 스텝: `chore: YYYY-MM-DD report` 커밋 반영
- [ ] `curate` job — Process stock report 스텝: `archive/YYYY/MM/YYYY-MM-DD-stock.json` 생성
- [ ] `curate` job — Commit & push stock report 스텝: `chore: YYYY-MM-DD stock report` 커밋 반영
- [ ] `publish` job: AI/IT 채널에 2개 embed 수신
- [ ] `pages` job: 배포 URL이 job 출력에 표시되고 브라우저에서 열림
- [ ] `pages` job: 로그에 `N개 날짜 + 루트` 출력
- [ ] `publish_stock` job: 주식 채널에 링크 한 줄 수신, 링크를 누르면 **그날** 리포트가 열림
- [ ] 페이지 좌측 드로어에서 과거 날짜로 이동됨

## 로컬 개발

### 수집 스크립트만 테스트

```bash
pip install requests feedparser
TARGET_DATE=2026-04-20 python3 scripts/collect_data.py
cat inbox/2026-04-20-raw.json

TARGET_DATE=2026-04-20 python3 scripts/collect_stock.py
cat inbox/2026-04-20-stock-raw.json

# 투자자 수급 (KRX 계정 필요, 없으면 스스로 건너뛴다)
pip install pykrx
KRX_ID=... KRX_PW=... python3 scripts/collect_investor.py
cat state/investor_trend.json
```

### daily_report.py 단독 실행

```bash
export AINEWS_REPO=$(pwd)
echo '{"news": [], "claude_updates": []}' | python3 scripts/daily_report.py --date 2026-04-20
```

### 주식 리포트 처리 + 페이지 렌더링

`inbox/YYYY-MM-DD-stock-raw.json`이 먼저 있어야 한다 (시세를 여기서 읽는다).

```bash
export AINEWS_REPO=$(pwd)
echo '{"market_news": [], "stock_news": []}' | python3 scripts/stock_report.py --date 2026-04-20

REPORT_DATE=2026-04-20 python3 scripts/render_stock_page.py
open site/index.html        # Windows: start site\index.html
```

### Discord 전송만 테스트

```bash
export REPORT_DATE="2026-04-20"

# AI/IT (embed)
export DISCORD_WEBHOOK_POTIONBOT_NEWS="https://discord.com/api/webhooks/..."
python3 scripts/send_discord.py

# 주식 (링크 한 줄)
export DISCORD_WEBHOOK_POTIONBOT_STOCK="https://discord.com/api/webhooks/..."
export STOCK_PAGE_URL="https://devp0tion.github.io/AINews/"
python3 scripts/send_discord.py --stock
```

### self-check 실행

```bash
python3 scripts/test_daily_report.py
python3 scripts/test_stock_report.py
python3 scripts/test_level_context.py
```

셋 다 외부 의존성 없이 돈다 — `requests`·`feedparser`·`pykrx`는 수집 함수 안에서
임포트하므로 순수 함수만 테스트할 때는 설치가 필요 없다.

## 실패 처리

| 상황 | 동작 |
|---|---|
| collect 실패 | 후속 job 자동 스킵 (`needs` 의존성). Actions 탭에서 수동 재실행. |
| Claude 출력 실패 (`/tmp/processed.json` 없음) | Process report 스텝이 명시적 에러로 즉시 실패. Actions 로그에서 Claude 스텝 원인 확인 후 수동 재실행. |
| Claude 주식 출력 실패 (`/tmp/processed_stock.json` 없음) | 주식만 스킵되고 **AI/IT는 정상 전송**. Actions 로그에 `::warning::` 표시. 수동 재실행하면 둘 다 다시 만든다. |
| stock_report.py 오류 | 위와 동일 — 주식만 스킵. |
| Process report 실패 (daily_report.py 오류) | job 실패 → publish 스킵. 입력 스키마 위반은 에러가 아니라 WARN + drop이므로, 여기서 실패하면 스크립트/파일시스템 문제. |
| push 실패 (권한·충돌) | archive/state는 생성됐지만 저장소에 반영 안 됨. 재실행하면 같은 날짜로 다시 생성된다. |
| Commit & push가 commit skip (신규 없음) | publish가 빈 리포트 정상 전송. archive 없어도 "금일 업데이트 없음" embed. |
| publish 실패 (webhook 오류) | Actions 로그에서 HTTP 코드 확인. webhook URL 유효성 점검. |
| 증시 RSS 한 곳 실패 | `safe()`가 WARN 처리하고 나머지 피드로 계속 진행. 리포트 건수만 줄어든다. |
| Yahoo 시세 조회 실패 | 해당 종목/지수만 시세 표에서 빠진다. 뉴스 섹션은 정상. |
| KRX 로그인 실패 / 미설정 | 투자자 수급 섹션만 빠진다. 기존 누적분이 있으면 그 날짜만 비고 나머지는 표시된다. |
| 투자자 수급 일부 결측 | 해당 칸이 `—`로 표시되고, 기간 합계는 수집된 날만 더한다. |
| pages job 실패 (Pages 미설정) | Settings → Pages → Source가 "GitHub Actions"인지 확인. `publish_stock`은 `needs: pages`라 함께 스킵된다. AI/IT 전송은 영향 없음. |
| 주식 archive 없음 | 그날은 빈 페이지를 만들어 배포하고 링크는 정상 전송한다 (404가 되는 것보다 낫다). |
| archive JSON 손상 | 그 날짜만 빈 페이지가 되고 나머지 날짜는 정상 렌더링된다. |

## 토큰 갱신

`CLAUDE_CODE_OAUTH_TOKEN`은 유효기간 1년. 만료 전 `claude setup-token`으로 재발급해 Secrets 값 교체.
