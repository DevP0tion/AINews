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
   └ collect_stock.py : 국내 증시·경제 RSS 5종 / Yahoo Finance 지수·관심종목 시세
                        → inbox/YYYY-MM-DD-stock-raw.json
   · 두 파일 커밋
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
[Job 4: pages] ── 주식 리포트를 HTML 한 장으로 렌더링해서 GitHub Pages 배포
   · render_stock_page.py → site/index.html → actions/deploy-pages
   ↓
[Job 5: publish_stock] ── 배포된 페이지 링크 한 줄만 주식 채널로 전송
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
│   └── watchlist.json                # 관심종목·지수 목록 (여기만 고치면 종목 추가됨)
├── scripts/
│   ├── collect_data.py               # Job 1: AI/IT 소스 fetching
│   ├── collect_stock.py              # Job 1: 증시 RSS + Yahoo Finance 시세
│   ├── daily_report.py               # Job 2(b): AI/IT 검증·필터·archive/state
│   ├── stock_report.py               # Job 2(c): 주식 검증·필터·archive/state
│   ├── render_stock_page.py          # Job 4: archive JSON → site/index.html
│   ├── send_discord.py               # Job 3/5: Discord 전송 (--stock으로 링크 모드)
│   ├── test_daily_report.py          # 중복 키 self-check
│   └── test_stock_report.py          # 주식 검증·중복·렌더링 self-check
├── state/
│   ├── seen_urls.json                # AI/IT 뉴스 URL 인덱스 (영구 누적)
│   ├── seen_claude.json              # Claude 업데이트 항목 키
│   └── seen_stock_urls.json          # 주식 뉴스 URL 인덱스 (AI/IT와 분리)
├── inbox/                            # Job 1 출력
│   ├── YYYY-MM-DD-raw.json
│   └── YYYY-MM-DD-stock-raw.json
├── archive/                          # Job 2 출력
│   ├── YYYY/MM/YYYY-MM-DD.{json,md}
│   └── YYYY/MM/YYYY-MM-DD-stock.json
└── site/                             # Job 4 생성물 (gitignore — 매 실행마다 새로 만듦)
    └── index.html
```

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

**종목 매칭** — 수집 단계에서 제목·요약에 `aliases`가 들어가면 `matched` 필드에 기계적으로 표시한다.
오탐이 있을 수 있어 최종 판단은 curate 단계의 Claude가 한다.

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

### 2. GitHub Actions 권한

Settings → Actions → General → Workflow permissions:

- ✅ **Read and write permissions** (archive/state 커밋에 필요)

### 3. GitHub Pages 활성화

Settings → Pages → Build and deployment → Source를 **GitHub Actions**로 변경한다.
(기본값인 "Deploy from a branch"로 두면 `pages` job의 배포가 실패한다.)

배포 주소는 `https://devp0tion.github.io/AINews/` 이고, **공개 저장소이므로 페이지도 공개**다.
페이지는 매 실행마다 최신 리포트 한 장으로 덮어쓴다. 과거 리포트는 `archive/`에만 남는다.

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
- [ ] `publish_stock` job: 주식 채널에 링크 한 줄 수신, 링크를 누르면 리포트가 열림

## 로컬 개발

### 수집 스크립트만 테스트

```bash
pip install requests feedparser
TARGET_DATE=2026-04-20 python3 scripts/collect_data.py
cat inbox/2026-04-20-raw.json

TARGET_DATE=2026-04-20 python3 scripts/collect_stock.py
cat inbox/2026-04-20-stock-raw.json
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
```

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
| pages job 실패 (Pages 미설정) | Settings → Pages → Source가 "GitHub Actions"인지 확인. `publish_stock`은 `needs: pages`라 함께 스킵된다. AI/IT 전송은 영향 없음. |
| 주식 archive 없음 | 빈 페이지를 배포하고 링크는 정상 전송한다 (링크가 404가 되는 것보다 낫다). |

## 토큰 갱신

`CLAUDE_CODE_OAUTH_TOKEN`은 유효기간 1년. 만료 전 `claude setup-token`으로 재발급해 Secrets 값 교체.
