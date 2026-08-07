# AINews

PotionBot News 일일 리포트 저장소.
**GitHub Actions + Claude Code Action**으로 매일 07:00 KST에 자동 실행되어
AI/IT 뉴스 + Claude/Anthropic 업데이트를 Discord로 전송한다.

## 아키텍처

```
[매일 07:00 KST cron]
   ↓
[Job 1: collect] ── 결정론적 fetching (Python)
   · Anthropic 뉴스 sitemap / Claude 릴리즈 노트 / GitHub Releases / HN AI / arxiv
   · inbox/YYYY-MM-DD-raw.json 커밋
   ↓
[Job 2: curate]
   ├ (a) Claude Code Action (anthropics/claude-code-action@v1)
   │    · CLAUDE_CODE_OAUTH_TOKEN 인증 (Pro/Max 구독 사용, 별도 결제 없음)
   │    · inbox 읽어서 한국어 요약·top 선정·specials 판정
   │    · 산출물은 /tmp/processed.json 하나. 여기서 Claude의 역할 종료
   │    · 도구는 Read/Write/WebFetch만 — Bash 없음, git 자격증명 없음
   ├ (b) Process report (결정론적 스텝)
   │    · scripts/daily_report.py 실행 → 스키마 검증 → 중복 제거 → archive/state 갱신
   └ (c) Commit & push (결정론적 스텝, 이 스텝에만 토큰 주입)
        · archive/ state/ 커밋 후 main에 푸시
   ↓
[Job 3: publish] ── Discord 전송
   · archive/YYYY/MM/YYYY-MM-DD.json 읽어서 webhook POST
```

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
│   ├── curate_prompt.md              # Claude에 전달되는 프롬프트
│   └── workflows/
│       └── daily.yml                 # 통합 워크플로 (collect → curate → publish)
├── scripts/
│   ├── collect_data.py               # Job 1: 공식/공개 소스 fetching
│   ├── daily_report.py               # Job 2(b): 스키마 검증/필터/archive/state
│   └── send_discord.py               # Job 3: Discord 전송
├── state/
│   ├── seen_urls.json                # 영구 누적 URL 인덱스
│   └── seen_claude.json              # Claude 업데이트 항목 키
├── inbox/                            # Job 1 출력
│   └── YYYY-MM-DD-raw.json
└── archive/                          # Job 2 출력
    └── YYYY/MM/YYYY-MM-DD.{json,md}
```

## 수집 소스 (collect_data.py)

- **Anthropic News** — https://www.anthropic.com/sitemap.xml 의 `/news/` 항목을 `lastmod` 내림차순 10건 (anthropic.com은 RSS 미제공)
- **Claude 릴리즈 노트** — https://docs.claude.com/en/release_notes/overview.md (raw markdown)
- **GitHub Releases API** — `anthropics/claude-code`, `anthropics/anthropic-sdk-python`, `anthropics/anthropic-sdk-typescript`
- **Hacker News** top stories 중 AI 키워드 매치
- **arxiv** cs.LG / cs.CL 최신 (참고용)

## 중복 판정

- **뉴스**: URL 정규화 (scheme/host 소문자화, trailing slash 제거, tracking param 제거, fragment 제거) 후 완전 일치
- **Claude 업데이트**: curate 단계가 부여한 `id` 완전 일치.
  `id`는 원본에서 따온 안정적 값 — `gh::{repo}::{tag}` / `news::{url}` / `rn::{날짜}::{키워드}`.
  (title은 매일 재작성되는 자유 문구라 키로 쓰지 않는다.)
  - `id`가 없는 레거시 항목은 `{category}::{title_normalized}` 키로 fallback
  - `gh::`·`news::` 항목은 `url::{normalized_url}`도 보조 키로 함께 대조
    (릴리즈 노트는 여러 항목이 같은 overview URL을 공유하므로 제외)
- **윈도우**: 영구 (state/seen_*.json 누적)

## 셋업

### 1. GitHub Secrets 등록

Repo → Settings → Secrets and variables → Actions → New repository secret:

- **`CLAUDE_CODE_OAUTH_TOKEN`**
  - 로컬에서 생성: 터미널에서 `claude setup-token` 실행
  - 브라우저 OAuth 플로우 완료 후 출력되는 토큰 (유효기간 1년) 복사
  - ⚠️ Pro/Max/Team/Enterprise 구독 필요
- **`DISCORD_WEBHOOK_POTIONBOT_NEWS`**
  - Discord 채널 설정 → Integrations → Webhooks에서 발급한 URL

### 2. GitHub Actions 권한

Settings → Actions → General → Workflow permissions:

- ✅ **Read and write permissions** (archive/state 커밋에 필요)

### 3. (선택) Anthropic GitHub App 설치

로컬에서 한 번 실행:

```bash
claude              # Claude Code CLI
/install-github-app
```

안내 따라 DevP0tion/AINews에 앱 설치. 스케줄 자동화에 필수는 아니지만, curate job이 GitHub API를 호출할 때 권한 경고가 덜 뜬다.

### 4. 첫 실행 검증

Actions 탭 → **Daily Report** → **Run workflow** (manual trigger):
- `date`: 빈칸 (오늘 KST 자동) 또는 특정 날짜 입력

성공 체크리스트:
- [ ] `collect` job: `inbox/YYYY-MM-DD-raw.json` 커밋됨
- [ ] `curate` job — Claude 스텝: Bash 도구 사용 흔적 없이 `/tmp/processed.json` 생성
- [ ] `curate` job — Process report 스텝: 요약 JSON 출력, `archive/YYYY/MM/YYYY-MM-DD.json` 생성
- [ ] `curate` job — Commit & push 스텝: `chore: YYYY-MM-DD report` 커밋 반영
- [ ] `publish` job: Discord 채널에 2개 embed 수신

## 로컬 개발

### 수집 스크립트만 테스트

```bash
pip install requests feedparser
TARGET_DATE=2026-04-20 python3 scripts/collect_data.py
cat inbox/2026-04-20-raw.json
```

### daily_report.py 단독 실행

```bash
export AINEWS_REPO=$(pwd)
echo '{"news": [], "claude_updates": []}' | python3 scripts/daily_report.py --date 2026-04-20
```

### Discord 전송만 테스트

```bash
export DISCORD_WEBHOOK_POTIONBOT_NEWS="https://discord.com/api/webhooks/..."
export REPORT_DATE="2026-04-20"
python3 scripts/send_discord.py
```

## 실패 처리

| 상황 | 동작 |
|---|---|
| collect 실패 | 후속 job 자동 스킵 (`needs` 의존성). Actions 탭에서 수동 재실행. |
| Claude 출력 실패 (`/tmp/processed.json` 없음) | Process report 스텝이 명시적 에러로 즉시 실패. Actions 로그에서 Claude 스텝 원인 확인 후 수동 재실행. |
| Process report 실패 (daily_report.py 오류) | job 실패 → publish 스킵. 입력 스키마 위반은 에러가 아니라 WARN + drop이므로, 여기서 실패하면 스크립트/파일시스템 문제. |
| push 실패 (권한·충돌) | archive/state는 생성됐지만 저장소에 반영 안 됨. 재실행하면 같은 날짜로 다시 생성된다. |
| Commit & push가 commit skip (신규 없음) | publish가 빈 리포트 정상 전송. archive 없어도 "금일 업데이트 없음" embed. |
| publish 실패 (webhook 오류) | Actions 로그에서 HTTP 코드 확인. webhook URL 유효성 점검. |

## 토큰 갱신

`CLAUDE_CODE_OAUTH_TOKEN`은 유효기간 1년. 만료 전 `claude setup-token`으로 재발급해 Secrets 값 교체.
