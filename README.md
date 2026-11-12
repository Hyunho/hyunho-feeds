# 오늘 루틴 (today-routine)

오늘의 루틴과 일정을 관리하는 사이트입니다.
Cloudflare Pages 프로젝트 `hyunho-feeds`에 배포되며, 주소는 `https://hyunho-feeds.pages.dev` 입니다. 표준 라이브러리만 쓰는 `publish.py`로 빌드합니다.
어느 디렉터리에서 실행해도 됩니다(경로는 스크립트 위치 기준).

RSS/Atom 피드와 OPML은 발행하지 않습니다. 루틴은 Markdown frontmatter로 저장되고, 빌드가 `/<secret>/today` 페이지를 만듭니다.

- [작업 frontmatter v1](docs/today-routine-frontmatter-v1.md)
- [복사해서 사용할 Markdown 예시](examples/weekly-review.md)

공유 박스에서는 보통 이 경로입니다.

```bash
PUB=/workspace/valley-feed/publish.py
```

## 1. 출처 만들기 (봇당 한 번)

```bash
python3 $PUB channel create --slug deals --name "할인정보 정리" --description "매일 할인 소식 요약" \
    --agent-id <내 Grok Bot agent id> --agent-name "할인정보/구매 봇"
```

- **`--agent-id`**: 자기 Grok Bot의 agent id(UUID). 오늘 페이지에서 그 출처 카드의 봇 열기 링크에 씁니다. `--agent-name`은 링크 문구(생략 시 `--name`).
- 이미 만든 출처에 나중에 넣거나 바꾸기: `python3 $PUB channel update --slug deals --agent-id <id> [--agent-name "..."]` (`--name/--description/--link`도 변경 가능, 저장 후 재배포).
- `valley` 출처(브리핑·공부)는 valley ai 학습 봇(31c9ced1-…)으로 고정되어 있습니다.
- `--slug`: 영문 소문자·숫자·하이픈(`[a-z0-9-]`, 최대 40자). 저장 파일 이름이 됩니다(`items/deals.json`).
- 예약어라 쓸 수 없는 slug: `feed, market, insights, news, community, channels, valley, index`
- `--name`: 오늘 페이지에 보이는 이름. `--link URL`로 대표 링크를 적을 수도 있습니다.
- 만들면 바로 배포됩니다(`--no-deploy`로 생략 가능). 이미 있는 slug면 오류가 납니다.

## 2. 루틴 등록하기 (마크다운 파일)

```bash
python3 $PUB add --channel deals --title "2026-10-03 할인 정리" --file /path/to/post.md
# 옵션: --date 2026-10-03T07:30:00+09:00 (기본: 지금, KST)  --link https://원문링크  --no-deploy
# 표준입력: --file -

python3 $PUB add --channel valley --title "2026-10-05 오늘의 공부 (1일차)" --file study.md
```

- `today_routine` frontmatter가 있는 글만 오늘 페이지 카드가 됩니다. 없는 글은 저장만 됩니다.
- 등록되지 않은 slug면 `channel create`를 먼저 하라는 오류가 납니다.
- 출처마다 **최근 60개** 항목만 유지됩니다.
- Valley AI 글(브리핑·공부 일정)은 `add --channel valley`(또는 `--channel` 생략)로 등록합니다. 다른 봇은 **반드시 자기 `--channel`을 지정**하세요.
- `sns` 출처의 글은 SNS 타임라인 페이지에 들어갑니다.

### 지원하는 마크다운

- `#`~`######` 제목, 빈 줄로 구분한 문단(줄바꿈 유지)
- `-`/`*`/`+` 글머리표, 2칸 들여쓰기로 하위 항목
- `**굵게**`, `[링크 텍스트](https://...)`, 맨 URL 자동 링크
- HTML 특수문자는 자동 이스케이프됩니다(표, 이미지, 코드블록은 미지원 → 일반 텍스트로 보임).

## 3. 출처 목록 보기

```bash
python3 $PUB channel list     # slug, 이름, 항목 수, 오늘 페이지 URL
```

## 4. 페이지 URL

```
https://hyunho-feeds.pages.dev/<secret>/           # 비공개 홈
https://hyunho-feeds.pages.dev/<secret>/today      # 오늘 루틴
https://hyunho-feeds.pages.dev/<secret>/sns        # SNS 타임라인
```

`<secret>`은 `/workspace/valley-feed/secret.txt`에 있습니다. 오늘 페이지 URL은 `channel list` 출력에서 복사하세요.
사이트 루트(`/`)는 보안상 빈 페이지로 둡니다.

## 5. 주의사항

- **배포 토큰**은 공유 박스의 환경변수 `CLOUDFLARE_API_TOKEN`에 있습니다. 출력·로그·메시지에 절대 노출하지 마세요.
- **secret 경로는 비공개 주소 역할**을 합니다. 사용자 본인에게만 전달하고, 공개 게시판·저장소·공개 채널에 올리지 마세요. 사이트 루트(`/`)는 빈 페이지입니다.
- 여러 봇이 동시에 등록해도 파일 잠금(`.publish.lock`)으로 순서대로 처리됩니다. 앞 작업의 배포가 끝날 때까지(보통 수십 초) 기다릴 수 있습니다.
- 배포가 실패해도 글은 저장되어 있으니 `python3 $PUB deploy`로 다시 배포하면 됩니다.
- 배포 전 today DATA 개수가 라이브보다 크게 줄면(대략 70% 미만) 중단합니다. 에이전트/새 박스의 오래된 `items/*.json`으로 덮어쓰지 않도록 하기 위함입니다. 의도적 교체만 `FORCE_STALE_ITEMS_DEPLOY=1`.
- 출처 삭제(저장된 글도 삭제): `python3 $PUB channel remove --slug deals --yes` — 다른 봇의 출처는 건드리지 마세요.
- 데이터: `channels.json`(출처 등록), `items/<slug>.json`(봇 글), `items.json`(Valley AI: 브리핑+공부). 오늘 페이지는 이 데이터로 매번 새로 생성됩니다.

## 6. 할 일 Inbox (AI가 넣고, 앱이 가져간 뒤 비우기)

AI/봇이 할 일을 넣으면, 사용자의 iPhone 앱이 가져가서 저장한 뒤 **가져간 id만** 지웁니다.
Pages Function(`functions/api/inbox/index.js`) + Cloudflare KV(`INBOX` 바인딩)로 같은 `hyunho-feeds` 프로젝트에 함께 배포됩니다.

- **엔드포인트**: `https://hyunho-feeds.pages.dev/api/inbox` (secret 경로 아님 — 토큰으로 보호)
- **인증**: `Authorization: Bearer <INBOX_TOKEN>` 필수. 토큰은 박스의 `/workspace/valley-feed/inbox_token.txt`(chmod 600)와 Pages secret `INBOX_TOKEN`에 있습니다. 출력·로그·저장소에 노출 금지. 비공개 경로와는 별개입니다.
- CORS: `*` 허용(쿠키 없음, 토큰 인증), `GET, POST, DELETE, OPTIONS`.
- **공개 API 문서(토큰·secret 없음, 다른 AI에게 공유 가능)**:
  - AI용 가이드: https://hyunho-feeds.pages.dev/api/inbox/docs.md (같은 내용: `/api/inbox/llms.txt`)
  - OpenAPI 3.1: https://hyunho-feeds.pages.dev/api/inbox/openapi.json
  - 원본은 `docs/docs.md`, `docs/openapi.json`이고 `build` 때 `public/api/inbox/`로 복사됩니다. API를 바꾸면 이 둘도 같이 고치세요. 문서에 토큰이나 secret을 절대 넣지 마세요.

### 봇용 CLI (공유 박스)

```bash
python3 $PUB inbox add --title "우유 사기" [--note "..."] [--due 2026-10-07 | 2026-10-07T18:30] \
                       [--priority low|normal|high|urgent] [--source deals-bot] [--tags 장보기,급함]
python3 $PUB inbox list            # 대기 중인 할 일 (오래된 순)
python3 $PUB inbox clear --ids ID [ID ...]   # 보통은 앱이 함. 봇은 남의 항목을 지우지 마세요
```

`--source`에는 **자기 봇 이름**을 꼭 넣으세요. `due`에 시간대가 없으면 KST(+09:00)로 해석합니다.

### API

| 요청 | 본문 | 응답 |
|---|---|---|
| `POST /api/inbox` | `{"title": "필수", "note"?, "due"?, "priority"?, "source"?, "tags"?: ["..."]}` | `201 {"item": {...}}` |
| `GET /api/inbox?limit=100` | — | `200 {"items": [...], "count": n, "has_more": bool}` (오래된 순, limit 최대 500) |
| `DELETE /api/inbox` | `{"ids": ["...", ...]}` (최대 1000) | `200 {"deleted": n, "ids": [...]}` |
| `DELETE /api/inbox` | `{"all": true}` (비권장) | `200 {"deleted": n, "all": true}` |

item: `{"id": "<uuid v7>", "created_at": "2026-10-06T07:29:55+09:00", "title", "note"?, "due"?, "priority"?, "source"?, "tags"?}` — 없는 필드는 생략됩니다.
오류: `{"error": {"code", "message"}}` — 400 invalid/bad_json, 401 unauthorized, 405, 413 too_large(16KB 초과), 503 not_configured.
없는 id 삭제는 오류 없이 무시됩니다(멱등).

```bash
TOKEN=$(cat /workspace/valley-feed/inbox_token.txt); U=https://hyunho-feeds.pages.dev/api/inbox
curl -X POST "$U" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"title":"우유 사기","due":"2026-10-07","priority":"high","source":"deals-bot","tags":["장보기"]}'
curl "$U" -H "Authorization: Bearer $TOKEN"
curl -X DELETE "$U" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     -d '{"ids":["01a10e30-18ba-7b82-964d-d1201190e0c1"]}'
```

### 앱 개발 메모 (iPhone 앱)

1. `GET /api/inbox` → 2. 받은 항목을 **먼저 로컬에 저장**(id로 중복 제거) → 3. 저장된 id들만 `DELETE {"ids": [...]}`.
   `{"all": true}`를 쓰면 GET과 DELETE 사이에 들어온 항목이 사라질 수 있으니 쓰지 마세요.
- `has_more: true`면 1~3을 반복합니다. 앱 실행/포그라운드 진입 시, 또는 몇 분 간격 폴링이면 충분합니다.
- KV는 전역 복제가 **최종 일관성**(최대 약 60초)이라, 방금 넣은 항목이 바로 안 보이거나 방금 지운 항목이 잠시 다시 보일 수 있습니다. **항상 id로 중복 제거**하세요(DELETE는 같은 id를 다시 보내도 안전).
- DELETE가 실패(네트워크 등)하면 다음 폴링 때 같은 id를 다시 지우면 됩니다.
- 토큰은 앱의 Keychain에 저장하세요.

### 최초 설정 (한 번)

KV 네임스페이스 생성 → `python3 $PUB inbox bind --namespace-id <32자리 id>` (바인딩 + 재배포, `inbox_kv_id.txt`에 기록).
출력의 `INBOX_TOKEN secret present: False`면 `wrangler pages secret put INBOX_TOKEN --project-name hyunho-feeds`로 다시 넣으세요.
바인딩 전에는 API가 `503 not_configured`를 돌려줍니다.

## 7. 오늘 루틴(today_routine v1) 등록하기

사양: https://github.com/Hyunho/hyunho-feeds/blob/main/docs/today-routine-frontmatter-v1.md
(저장소 사본: [docs/today-routine-frontmatter-v1.md](docs/today-routine-frontmatter-v1.md). 시작 시각과 캘린더 연결이 있는 복사 예시는 [examples/weekly-review.md](examples/weekly-review.md).)
마크다운 맨 앞 YAML frontmatter의 `today_routine`에 작업/세부 작업을 넣으면, 빌드가 오늘 페이지 카드로 올립니다.
등록은 `add --channel <내 출처> --file doc.md` 입니다(별도 옵션 없음).

```bash
# 1) 템플릿 만들기 (--task ID=제목 반복, --with-steps 는 예시 소요시간·세부작업 추가)
python3 $PUB routine new --id morning-check --title "아침 점검" \
    --task water="물 마시기" --task stretch="스트레칭" --with-steps --out /tmp/routine.md
# 2) 편집 후 검증
python3 $PUB routine validate --file /tmp/routine.md
# 3) 등록 (add 도 자동으로 같은 검증을 하고, 틀리면 오류 목록과 함께 거부)
python3 $PUB add --channel <내 출처> --title "2026-10-21 아침 점검" --file /tmp/routine.md
```

완전한 예시(`examples/today-routine-example.md`):

```markdown
---
title: "주간 업무 점검"
date: "2026-10-20"
today_routine:
  version: 1
  id: weekly-review
  tasks:
    - id: inbox-zero
      title: "메일함 정리"
      duration_minutes: 15
    - id: plan-week
      title: "다음 주 계획 세우기"
      duration_minutes: 30
      steps:
        - id: check-calendar
          title: "캘린더 확인"
        - id: pick-top3
          title: "우선순위 3개 고르기"
---

# 주간 업무 점검

읽기용 본문.
```

규칙(검증됨):
- `today_routine.version: 1`(정수), `id`, `tasks`(1개 이상) 필수. 작업은 `id`, `title` 필수, `duration_minutes`(정수 ≥1)·`steps` 선택. 세부 작업은 `id`, `title` 필수, **한 단계만**.
- ID: `^[a-z0-9][a-z0-9_-]*$`. 작업 id는 문서 안에서, 세부 작업 id는 부모 작업 안에서 고유. **같은 작업이면 제목이 바뀌어도 id 유지**, 루틴 id는 같은 구독 안에서 고유하게(매일 같은 루틴이면 같은 id 재사용 가능).
- `date`는 대상 날짜 `"YYYY-MM-DD"`(**따옴표 필수**). `title` 선택.
- `today_routine.start_time`(선택): 루틴 시작 제안 시각, **따옴표 필수** 24시간제 `"HH:mm"`(`"00:00"`~`"23:59"`, 현지 시각), `tasks`와 같은 수준에 한 번. 생략 = 제안 없음. `09:00`(따옴표 없음)·`"9:00"`·`"24:00"`·`"09:00:00"`·`""`·`null`은 거부. 템플릿: `routine new ... --start-time 09:00`.
- `today_routine.calendar_link`(선택, `tasks`와 같은 수준): 기존 Google 캘린더 일정에 연결. `provider: "google"`, `calendar_id`(실제 ID, `"primary"` 금지), `event_id`(Google API 일정 ID; URL·iCalUID·제목 금지, `series`/`occurrence`는 반복 원본 ID=`recurringEventId`), `scope: "event"|"series"|"occurrence"` 모두 필수. `occurrence`만 `original_start_time`에 `date_time`(오프셋/`Z` 포함 RFC 3339, 예 `"2026-10-21T09:00:00+09:00"`) 또는 `date`(`"YYYY-MM-DD"`, 종일) **정확히 하나** 필수(Google `originalStartTime` 값 그대로, 일정이 옮겨져도 유지). `event`/`series`에는 `original_start_time` 금지. 빈 문자열·공백·`null` 불가, 생략 = 연결 제안 없음. 외부 ID에는 id 정규식 미적용. 등록은 캘린더 API를 호출하거나 일정을 만들지 않습니다.
  템플릿: `routine new --id ... --calendar-id "<id>@group.calendar.google.com" --event-id <id> --scope occurrence --original-start 2026-10-21T09:00:00+09:00`(`YYYY-MM-DD`면 `date`로 들어감).
- 금지: YAML 앵커/별칭(`&`, `*`, `<<`), 사용자 정의/명시 태그(`!foo`), 중복 키, 완료 여부·진행 상태(`done`, `status` 등).

저장 방식:
- frontmatter를 포함한 Markdown 원문은 항목의 `md`에 그대로 둡니다. 오늘 페이지는 빌드 때 여기서 작업을 읽습니다.
- 글 id(등록마다 새로 생성)와 `today_routine.id`(루틴 식별자)는 별개입니다.
- 파일은 바이트 그대로(줄바꿈 포함) 저장됩니다. 검증에는 PyYAML이 필요합니다(박스에 설치됨, 없으면 `pip install --user pyyaml`).

### 오늘 루틴 타임라인 페이지

빌드 때 모든 출처의 `today_routine` 글을 모아 `/<secret>/today`(= `today.html`, 홈에 링크)를 자동 생성합니다(noindex).
- 루틴 id마다 frontmatter `date`(없으면 등록일, KST)가 **오늘(KST, 브라우저에서 계산) 이하인 최신 글**을 보여줌 → 전날 밤 다음 날짜로 미리 등록해도 됨. 아직 날짜가 안 된 것만 있으면 "예정된 루틴"에 표시.
- 루틴별 카드: 출처(봇 열기 링크)·제목·대상 날짜, 작업 순서대로 소요/누적 시간, 세부 작업, 총 시간. 본문 `[링크](url)` 텍스트가 작업 제목과 겹치면 작업에 링크.
- 루틴 제목(카드·아웃라인)이나 왼쪽 시간표·목차의 항목을 누르면 `#/r/<루틴 id>` 세부 페이지로 이동합니다. 그 페이지는 작업을 펼친 카드와 본문을 보여 주고, "← 오늘 목록" 또는 브라우저 뒤로 가기로 목록에 돌아옵니다. 완료·스킵·세부 작업은 목록과 같은 저장소를 씁니다.
- 카드마다 시작 시간: 기본값은 `start_time`, 사용자가 바꾸면 루틴 id별로 기기 localStorage에 저장되어 우선 적용("제안 시간으로" 버튼으로 되돌림). 둘 중 하나라도 있으면 각 작업의 실제 시각 표시. `calendar_link`가 있으면 카드에 "📅 Google 캘린더 연결 · 범위(· 회차 시각 KST)" 배지를 붙입니다. 오늘 페이지는 오늘 일정만 가져와서, `calendar_id`와 `event_id`가 오늘 한 건과 맞을 때만 그 카드에 붙입니다. `occurrence`는 `original_start_time`이 그 회차와 같아야 하고, 같은 캘린더의 다른 일정은 그대로 따로 둡니다. 맞춘 일정이 둘 이상이면 붙이지 않습니다. 등록은 일정을 만들거나 바꾸지 않습니다. 따라서 `duration_minutes`를 넣어 주세요.
- 고정 세부 루틴: 등록된 글이 없어도 캘린더 일정에 미리 정해 둔 작업 목록을 붙일 수 있습니다. 오늘 페이지의 `FIXED_ROUTINES`에 Google `event_id`(우선) 또는 제목으로 매칭합니다. 예: Routine 캘린더의 `아침 루틴`, `일과 시작`, `Valley AI 확인`. 카드에는 소요 시간이 있는 작업 목록과 `고정 세부 루틴` 표시가 붙고, 페이지에서 추가·삭제하는 D1 세부 작업과는 별개입니다. 같은 시각·제목으로 봇이 `today_routine` 글(세부 작업 포함)을 올려도 체크리스트는 `FIXED_ROUTINES`가 우선합니다(봇 출처·열기 링크는 유지).
- 학습 봇 슬롯(영어·독서·Forge·Claude·노마드 등): 예전에 같은 시각·제목으로 봇 글이 있던 Routine 캘린더 칸은, 오늘 봇 피드가 오기 전 가짜 1칸 체크리스트로 보이지 않습니다. 봇이 세부 작업을 등록한 뒤에만 카드가 나타납니다(식사·알림 같은 일반 캘린더 일정은 그대로).
- 캘린더 캐시: 브라우저가 그날 `today-cal` 성공 응답을 `localStorage`에 저장합니다. API가 404·네트워크 오류여도 같은 날짜 캐시가 있으면 캘린더 카드를 유지하고 `캘린더 캐시` 태그를 붙입니다. 완료 상태·접기·보기 모드도 기기 `localStorage`에 둡니다.

## 8. SNS 타임라인 페이지

`/<secret>/sns.html`(또는 `/<secret>/sns`): `sns` 출처를 X/Instagram 타임라인처럼 보여주는 페이지(최신순, 계정별 필터 칩, 시스템 다크/라이트 모드).
원본은 `docs/sns.html`이고 `build` 때 저장된 글을 페이지에 넣습니다. `add --channel sns` 후 배포되면 반영됩니다(페이지는 5분마다·탭 복귀 시 다시 불러옵니다).

## 9. 전날 진도 확인 (`progress`) — 학습 봇 등록 전 필수

학습 봇(노마드 코더·영어 공부·독서·Valley AI·Claude Academy 등)이 **다음 날 세부 작업을 등록하기 전에**, D1 `done` 테이블에 저장된 전날 완료/스킵/미완료를 읽고 진도를 반영하세요.

### 명령

```bash
# 기본: 어제(KST) 전체 출처
python3 /workspace/valley-feed/publish.py progress

# 특정 날짜
python3 /workspace/valley-feed/publish.py progress --date 2026-10-31

# 출처만 (학습 봇 slug)
python3 /workspace/valley-feed/publish.py progress --date 2026-10-31 --channel english-study
```

학습 출처 slug 예: `nomad-coders`, `english-study`, `reading`, `valley`, `claude-academy`.

출력은 stdout JSON입니다. secret/토큰은 절대 출력하지 않습니다. GET `/api/done?date=` 만 사용합니다(추가 토큰 불필요; secret 경로가 게이트).

### JSON 형태 (요약)

```json
{
  "date": "2026-10-31",
  "channels": [
    {
      "slug": "english-study",
      "name": "영어 공부",
      "items": [
        {
          "rid": "english-study-2026-10-31",
          "title": "...",
          "status": "skip",
          "start_time": "07:30",
          "category": "",
          "tasks": [
            {"id": "review", "title": "...", "status": "skip"}
          ]
        }
      ]
    }
  ],
  "summary": {"items": 1, "tasks_done": 0, "tasks_skip": 5, "tasks_todo": 0}
}
```

- 카드 `status` / 각 세부 작업 `status`: `done` | `skip` | `todo`
- 지금은 D1이 **카드(rid) 단위**로 저장됩니다. 세부 작업별 키가 없으면 카드 상태를 그대로 상속합니다.
- 나중에 `rid|tid` 형태가 D1에 쌓이면 세부 작업 상태가 우선합니다.

### 권장 규칙 (다음 날 목록 구성)

1. **`todo`(미완료)** 세부 작업 → 다음 날 목록 **맨 위**에 이어서 넣기 (carry-over).
2. **`skip`(스킵)** → 드롭하거나, 봇 정책에 따라 나중에 재배치 (기본: 당일 목록에서 제외).
3. **`done`(완료)** → 진도 반영 후 다음 커리큘럼/회차로 진행.
4. 카드 전체가 `skip`이면 해당 회차는 스킵으로 기록하고 새 회차를 등록.
5. 등록 직전에 이 명령을 한 번 돌리고, JSON을 기준으로 오늘 `today_routine.tasks` 순서를 정한 뒤 `add` 하세요.

## 비공개 경로 (deploy mapping)

Cloudflare Pages Functions that sit under the private path are stored in this
repo as `functions/_secret_/` (placeholder). On the live box / deploy tree the
directory is named with the real path segment (same value as `secret.txt`,
never committed). Before `wrangler pages deploy`, rename or symlink:

```bash
# example — SECRET is the path from secret.txt (do not commit it)
cp -a functions/_secret_ "functions/${SECRET}"
```

Do **not** commit the real path directory name. Keep `secret.txt` and tokens out
of git (see `.gitignore`).
