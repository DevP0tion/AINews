
---

# 파트 1 — AI/IT 리포트

목표:
1. `{{INBOX_PATH}}`에서 뉴스 후보 2~3건 **선정**
2. **한국어 요약** 작성
3. Anthropic 사이트·Claude 릴리즈 노트(Platform·앱)·GitHub Releases에서 **오늘~어제 분량만** 정리
4. 판정 기준에 맞는 항목에 **`special: true`** 플래그

## 1단계. 입력 읽기

`Read` 도구로 `{{INBOX_PATH}}`를 읽는다.

필드:
- `anthropic_news` — anthropic.com sitemap.xml에서 고른 글. title은 slug 추정값, summary는 비어 있음. `section`으로 출처를 구분한다
  - `news` / `engineering` / `research` — 해당 경로의 글. `published`는 sitemap lastmod
  - `page` — 루트 경로의 `claude-*` 페이지(모델 발표 등). sitemap에 날짜가 없어 `published`는 비어 있고, 수집기가 처음 발견한 날짜가 `first_seen`에 있다
- `claude_release_notes_md` — Claude Platform(API·SDK·Console) 릴리즈 노트 원본 markdown
- `claude_apps_release_notes` — Claude 앱(웹·데스크톱·모바일) 릴리즈 노트. 최신 날짜 섹션 몇 개가 `{date, heading, text}`로 들어 있다. `text` 안의 `(https://...)`는 원문 링크다
- `github_releases.{claude_code, sdk_python, sdk_typescript}` — GitHub Releases
- `hn_ai_stories` — Hacker News top 스토리 중 AI 관련
- `arxiv_recent` — arxiv cs.LG, cs.CL 최신 (참고용)

`claude_release_notes_md`가 길면 오늘~어제 섹션만 추출한다. 전체 파싱 금지.

## 2단계. AI/IT 뉴스 선정 (2~3건)

**우선순위 주제** (있으면 우선, 없으면 일반 AI/IT 주요 뉴스):
- 데이터 최적화 기법
- Diffusion 모델 아키텍처 진보
- 신규/신흥 AI 기술
- LLM 개발 동향

**선정 기준**:
- `hn_ai_stories`에서 score 높고 제목이 위 주제에 맞는 것 우선
- `arxiv_recent`는 지나치게 세부적인 논문이면 skip
- `anthropic_news`는 Claude 리포트(3단계)로 넘기고 여기선 제외
- `hn_ai_stories` 중 URL이 `anthropic_news`에 있는 글이 3단계에서 Claude 업데이트로 선정되면 뉴스에서는 뺀다 (같은 글을 두 섹션에 싣지 않는다)
- 같은 이벤트 중복 보도는 가장 권위있는 1개만

**이 파트에서 `WebFetch`가 특히 필요한 경우**:
- `hn_ai_stories`는 **제목과 URL뿐이다.** 최종 선정할 2~3건은 원문을 확인하고 요약한다
- `arxiv_recent`의 `summary`는 600자에서 잘린다. 핵심 기여를 정확히 쓰려면 abstract 원문을 본다
- HN 제목이 과장·축약된 경우가 잦다. 제목을 그대로 번역하지 말 것

## 3단계. Claude/Anthropic 업데이트 정리

소스별 파싱 기준:
- `anthropic_news` → 오늘~어제 published만 (`section: page`는 `first_seen`이 오늘~어제인 것). 카테고리는 내용 기반 판정 (제품 / 모델/API / 생태계)
  - **주의**: `published`는 sitemap `lastmod`(페이지 수정 시각)라서 과거 글을 수정해도 최신으로 잡힐 수 있다. 제목·발행일은 `WebFetch`로 원문을 확인해 판정하고, 단순 페이지 수정으로 재등장한 과거 글은 제외할 것. `title`도 URL slug에서 유도한 근사값이므로 원문 제목으로 교체할 것
  - `section: page`는 새로 생긴 페이지라는 뜻일 뿐 발행일이 아니다. 원문에서 발표 내용·날짜를 확인하고, 이미 지난 발표를 정리한 소개 페이지면 제외한다
  - 이 확인은 **`WebFetch`를 쓸 가치가 가장 높은 곳**이다. `anthropic_news` 후보가 여러 건이면 `section: page`를 먼저, 나머지는 `published`가 최신인 것부터 확인한다
- `claude_release_notes_md` → **오늘~어제 날짜 섹션만** 추출
- `claude_apps_release_notes` → `date`가 **오늘~어제인 섹션만**. 카테고리는 "제품" (모델 출시 공지면 "모델/API"). 같은 변경이 `claude_release_notes_md`나 `anthropic_news`에도 있으면 하나만 남긴다
- `github_releases.*` → `published_at`이 오늘~어제인 것만. sdk_python/sdk_typescript는 카테고리 "SDK", claude_code는 "제품"

**카테고리**: "모델/API" | "제품" | "SDK" | "문서" | "생태계" 중 하나

`special` 판정이 애매하면(breaking change인지, 단순 개선인지) 릴리즈 본문이나 해당 릴리즈 페이지를 확인한다.
`special`은 Discord에서 ⚠️로 강조되므로 오판정 비용이 크다.

**`special: true` 기준** (하나라도 해당):
- 메이저 모델 릴리즈 (Opus/Sonnet/Haiku의 주 버전 변경)
- Breaking change
- 가격/rate limit 변경
- 신규 제품/기능의 GA 전환
- 공식 정책/ToS 변경

## 4단계. `/tmp/processed.json` 저장

`Write` 도구로 정확히 이 구조로 저장한다:

```json
{
  "news": [
    {"title": "...", "summary": "한국어 2~3문장", "url": "..."}
  ],
  "claude_updates": [
    {"id": "...", "category": "...", "title": "...", "content": "한국어 1문장", "url": "...", "special": false}
  ]
}
```

**`id` 작성 규칙 (중복 판정 키 — 반드시 채울 것)**

`title`은 매일 다르게 재작성되는 자유 문구라 중복 판정에 쓸 수 없다. `id`는 **원본 데이터에서 그대로 따온 안정적인 값**이어야 하며, 같은 릴리즈는 며칠에 걸쳐 다시 입력돼도 항상 같은 `id`가 나와야 한다. 재작성한 문구를 넣지 말 것.

| 소스 | 형식 | 예시 |
|---|---|---|
| `github_releases.*` | `gh::{repo}::{tag}` | `gh::anthropic-sdk-python::v0.121.0` |
| `anthropic_news` | `news::{url}` | `news::https://www.anthropic.com/news/...` |
| `claude_release_notes_md` | `rn::{날짜 섹션}::{항목 핵심 키워드}` | `rn::2026-08-06::opus-5-ga` |
| `claude_apps_release_notes` | `app::{date}::{항목 핵심 키워드}` | `app::2026-10-07::monthly-api-credits` |

- `repo`: 아래 표대로만 매핑한다. **추론·축약 금지** (owner 접두사 `anthropics/`는 붙이지 않는다).
  | 인박스 키 | `repo` 값 |
  |---|---|
  | `github_releases.claude_code` | `claude-code` |
  | `github_releases.sdk_python` | `anthropic-sdk-python` |
  | `github_releases.sdk_typescript` | `anthropic-sdk-typescript` |
- `tag`: 원본 JSON의 `tag` 필드 값 그대로 (`v` 접두사 포함, 가공 금지).
- `news::`의 `url`: 원본 JSON의 `url` 값 그대로. `section`과 무관하게 `anthropic_news` 항목은 전부 `news::`를 쓴다.
- `rn::`의 날짜 섹션: 릴리즈 노트 원문의 날짜 헤딩 그대로 (`YYYY-MM-DD`). 키워드는 원문에서 따온 **영문 소문자 + 하이픈** (예: `opus-5-ga`, `mcp-oauth`). 번역·의역 금지.
- `app::`의 `date`: 원본 JSON의 `date` 값 그대로. 키워드 규칙은 `rn::`과 같다 (각 항목의 굵은 제목에서 따온다).
- `app::` 항목의 `url`: `text` 안에 그 항목의 링크가 있으면 그것을, 없으면 `https://support.claude.com/en/articles/12138966-release-notes`.
- **이미 보고된 항목은 같은 id**: `rn::`·`app::` id를 정하기 전에 `Read`로 `state/seen_claude.json`을 읽어 같은 날짜 섹션(`rn::{날짜}::`·`app::{날짜}::`)으로 시작하는 id가 있는지 확인한다. 이미 보고된 변경과 같은 내용이면 새 키워드를 짓지 말고 **그 id를 글자 그대로** 쓴다 — 스크립트가 중복으로 걸러 다시 전송되지 않는다. 키워드만으로 같은 변경인지 애매하면 어제 archive(`archive/YYYY/MM/YYYY-MM-DD.json`)의 `claude_updates` title·content와 비교한다.

선정할 것이 없으면 빈 배열로: `{"news": [], "claude_updates": []}`

**여기까지가 파트 1이다. 이어서 파트 2를 수행할 것.**
