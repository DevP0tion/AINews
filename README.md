# AINews

PotionBot News 일일 리포트 저장소.
**Claude Code Routine**이 수집·AI 처리를 하고, **GitHub Actions**가 결정론적 fetching·Discord 전송·main 머지를 담당한다.

## 아키텍처

```
[09:00 KST] Routine 시작
   ├─(1) gh workflow run collect.yml
   ├─(2) gh run watch  ─────────┐
   │                             ▼
   │                    [Action: collect.yml]
   │                    · Anthropic news / 릴리즈 노트 / GitHub Releases / HN AI / arxiv
   │                    · inbox/YYYY-MM-DD-raw.json 을 main에 커밋
   │                             │
   ├─(3) git pull origin main ◄──┘
   ├─(4) AI: 중복체크·한국어 요약·top 선정·specials 판정
   ├─(5) claude/daily-YYYY-MM-DD 브랜치 생성 + archive/state 커밋
   └─(6) gh workflow run finalize.yml -f branch=claude/daily-YYYY-MM-DD ─┐
                                                                         ▼
                                                              [Action: finalize.yml]
                                                              · send_discord.py
                                                              · main에 머지
                                                              · claude/daily-* 삭제
```

**`claude/` 브랜치 제약**: Routine은 `claude/*` 에만 push 가능. main 커밋은 Action이 담당.

## 구조

```
AINews/
├── README.md
├── .github/workflows/
│   ├── collect.yml              # 공식/공개 소스 수집 (Action)
│   └── finalize.yml             # Discord 전송 + main 머지 (Action)
├── scripts/
│   ├── collect_data.py          # Action에서 실행 — 결정론적 fetching
│   ├── daily_report.py          # Routine에서 실행 — 필터/archive/state
│   └── send_discord.py          # finalize Action에서 실행
├── state/
│   ├── seen_urls.json           # 영구 누적 URL 인덱스
│   └── seen_claude.json         # Claude 업데이트 항목 키
├── inbox/                       # collect.yml 출력 (raw 데이터)
│   └── YYYY-MM-DD-raw.json
└── archive/                     # daily_report.py 출력 (최종 리포트)
    └── YYYY/MM/YYYY-MM-DD.{json,md}
```

## 수집 소스 (collect.yml이 자동으로)

- **Anthropic News RSS** — https://www.anthropic.com/news/rss.xml
- **Claude 릴리즈 노트** — https://docs.claude.com/en/release_notes/overview.md (raw markdown)
- **GitHub Releases** — `anthropics/claude-code`, `anthropics/anthropic-sdk-python`, `anthropics/anthropic-sdk-typescript`
- **Hacker News** top stories 중 AI 키워드 매치 (최대 15건)
- **arxiv** cs.LG / cs.CL 최신 (최대 10건/카테고리)

이걸 `inbox/YYYY-MM-DD-raw.json`에 덤프. Routine이 이 원본에서 2~3개의 주요 뉴스를 선정하고 한국어 요약.

## 중복 판정

- **뉴스**: URL 정규화 (scheme/host 소문자화, trailing slash 제거, tracking param 제거, fragment 제거) 후 완전 일치
- **Claude 업데이트**: `{category}::{title_normalized}` 키 완전 일치
- **윈도우**: 영구 (state/seen_*.json은 계속 누적)

## 셋업

### 1. GitHub Secrets 설정

Repository Settings → Secrets and variables → Actions → New repository secret:

- `DISCORD_WEBHOOK_POTIONBOT_NEWS` — Discord webhook URL

### 2. GitHub Actions 권한

Settings → Actions → General → Workflow permissions:

- **Read and write permissions** 체크
- **Allow GitHub Actions to create and approve pull requests** 체크 (선택)

### 3. Claude Code Routine 생성

https://claude.ai/code/routines 에서:

- **Repository**: `DevP0tion/AINews` 연결
- **Schedule**: Daily, 09:00 KST
- **Prompt**: `potionbot_news_prompt_v4.md` 내용 붙여넣기
- **Connectors**: 필요시 추가 (기본은 Repository만)

### 4. 수동 첫 실행으로 검증

```bash
# 로컬에서 collect 워크플로만 테스트
gh workflow run collect.yml

# 몇 분 뒤 inbox 확인
git pull
ls inbox/
```

### 5. (선택) 로컬 개발 시 daily_report.py 단독 실행

```bash
export AINEWS_REPO=$(pwd)
echo '{"news": [], "claude_updates": []}' | python3 scripts/daily_report.py
```

## 실패 처리

- **collect.yml 실패**: inbox에 파일 미생성. Routine이 이를 감지해 "금일 수집 실패" 상태 리포트 생성.
- **Routine AI 처리 실패**: claude/daily-* 브랜치 미생성. finalize 호출 없음. 다음 날 재시도.
- **finalize.yml 실패**: claude/daily-* 브랜치가 남아있음. Actions 탭에서 재실행 (workflow_dispatch) 가능.
- **Discord 전송 실패**: Action 로그 확인. webhook URL이 유효한지 점검.
