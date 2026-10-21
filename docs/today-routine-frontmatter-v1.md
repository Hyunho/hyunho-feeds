# 피드 발행자 안내: 작업 frontmatter v1

피드 문서에 작업과 세부 작업을 제공하려는 발행자를 위한 포맷 초안입니다. Markdown 작성 형식과 RSS/Atom 전송 규칙을 정의합니다.

## 문서 구조

UTF-8 Markdown 파일 시작의 `---` 구분자 사이에 YAML 매핑을 작성한다. 닫는 `---` 이후는 읽기용 본문이다. 일반 문서 메타데이터와 충돌하지 않도록 앱 설정을 `today_routine` 아래에 둔다.

`today_routine`을 넣을 때 그 안의 필수 필드를 작성합니다. 작업·세부 작업의 필수 필드는 각 항목을 작성할 때 적용됩니다.

전체 예시는 [주간 업무 점검](../examples/weekly-review.md)을 참고한다.

| 필드 | 타입 | 필수 | 규칙 |
| --- | --- | --- | --- |
| title | 문자열 | 아니오 | 문서 제목. 없으면 피드 항목 제목 사용 |
| date | 문자열 | 아니오 | 발행일, YYYY-MM-DD. YAML 날짜 변환을 피하도록 따옴표 사용 |
| today_routine | 객체 | 아니오 | 없으면 일반 읽기용 피드 |
| today_routine.version | 정수 | 예 | v1은 1 |
| today_routine.id | 문자열 | 예 | 같은 구독 안에서 고유한 문서 식별자 |
| today_routine.start_time | 문자열 | 아니오 | 루틴 시작 시각. 따옴표로 감싼 24시간제 `"HH:mm"` (`"00:00"`~`"23:59"`) |
| today_routine.calendar_link | 객체 | 아니오 | Google 캘린더 기존 일정 연결 대상. 아래 필드·범위 규칙 참고 |
| today_routine.tasks | 배열 | 예 | 한 개 이상의 작업, 배열 순서대로 표시 |
| tasks[].id | 문자열 | 예 | 문서 안에서 고유한 작업 식별자 |
| tasks[].title | 문자열 | 예 | 공백만으로 구성되지 않은 작업명 |
| tasks[].duration_minutes | 정수 | 아니오 | 1 이상인 예상 총 소요 시간. 생략하면 미지정 |
| tasks[].steps | 배열 | 아니오 | 세부 작업 목록. 생략하거나 빈 배열이면 단일 작업 |
| steps[].id | 문자열 | 예 | 부모 작업 안에서 고유한 세부 작업 식별자 |
| steps[].title | 문자열 | 예 | 공백만으로 구성되지 않은 세부 작업명 |

문서·작업·세부 작업 ID는 소문자 영문·숫자로 시작하고 소문자 영문·숫자·하이픈·밑줄만 사용한다: `^[a-z0-9][a-z0-9_-]*$`.
제목·배열 순서·문서 경로가 바뀌어도 같은 작업이면 ID를 유지한다. 별개의 문서나 작업에는 새 ID를 부여한다. 세부 작업은 한 단계만 지원한다.

YAML 중복 키, 사용자 정의 태그, 앵커·별칭은 사용하지 않는다. 작업의 완료 여부, 사용자별 진행 상태, 실행 지시용 날짜는 frontmatter에 넣지 않는다. `calendar_link.original_start_time`의 날짜는 기존 반복 회차 식별에만 사용한다. `date`는 발행일이다. v1에 없는 필드에 의존하는 작업 동작은 정의하지 않는다.

## 루틴 시작 시각

`today_routine.start_time`은 이 문서가 연결될 루틴의 시작 시각을 제안하는 선택 필드다. 개별 작업이나 세부 작업의 시작 시각이 아니므로 `tasks`와 같은 수준에 한 번 작성한다.

```yaml
today_routine:
  version: 1
  id: "weekly-review-2026-10-20"
  start_time: "09:00"
  tasks:
    - id: "review-issues"
      title: "담당 이슈 점검"
```

- 시와 분을 각각 두 자리로 작성하고 반드시 따옴표로 감싼다. 형식은 `^(?:[01][0-9]|2[0-3]):[0-5][0-9]$`이다.
- 초·날짜·UTC 오프셋은 포함하지 않는다. `"9:00"`, `"24:00"`, `"09:60"`, `"09:00:00"`은 유효하지 않다.
- 소비 앱에서 루틴에 사용하는 현지 시간대의 시각으로 해석한다. 특정 시간대의 절대 시점을 나타내는 필드가 아니다.
- 생략하면 시작 시각을 제안하지 않는다는 뜻이다. 기존 시간 삭제를 뜻하지 않는다. 빈 문자열이나 `null`로 삭제를 요청하지 않는다.
- `date`는 문서 발행일이며 `start_time`과 결합해 실행 날짜를 지정하지 않는다.
- 이 필드를 제공해도 기존 루틴 시간의 자동 변경이나 정시 실행을 보장하지 않는다. 적용 여부는 소비 앱의 기능과 사용자 선택에 따른다.
- v1 초안의 선택 필드 추가이며 기존 문서는 변경 없이 사용할 수 있다.

## Google 캘린더 기존 일정 연결

2026-10-21 추가한 v1 초안의 선택 필드다. `today_routine.calendar_link`를 `tasks`와 같은 수준에 작성한다. 기존 문서는 변경 없이 사용할 수 있다. 이 정의는 앱 구현 완료를 의미하지 않으며, 미지원 소비 앱은 캘린더 연결을 수행할 수 없다.

### 필드

| 필드 (`today_routine.calendar_link` 아래) | 타입 | 규칙 |
| --- | --- | --- |
| `provider` | 문자열 | 필수. 현재 `google`만 지원 |
| `calendar_id` | 문자열 | 필수. 대상 Google 캘린더의 실제 ID |
| `event_id` | 문자열 | 필수. `event`는 단일 일정 ID, `series`·`occurrence`는 반복 원본 일정 ID |
| `scope` | 문자열 | 필수. `event`, `series`, `occurrence` 중 하나 |
| `original_start_time` | 객체 | `occurrence`에만 필수. 회차의 원래 시작 시점 |
| `original_start_time.date_time` | 문자열 | 시간 지정 회차. UTC 오프셋을 포함한 RFC 3339 시각 |
| `original_start_time.date` | 문자열 | 종일 회차. `YYYY-MM-DD` |

`calendar_link`를 생략하면 연결 대상을 제안하지 않는다. 기존 연결 해제를 뜻하지 않는다. 객체를 제공한다면 필수 필드를 모두 작성한다. 빈 문자열·공백만 있는 문자열·`null`은 허용하지 않는다.

캘린더 ID와 일정 ID는 Google에서 받은 값을 그대로 저장한다. 기존 문서·작업 ID의 소문자·숫자·하이픈·밑줄 정규식은 외부 ID에 적용하지 않는다. 표시 이름, 일정 제목, 일정 URL, `iCalUID`를 `event_id` 대신 넣지 않는다. 계정마다 의미가 달라지는 `primary` 대신 실제 캘린더 ID를 사용한다.

### 예시

반복 일정 전체에 연결:

```yaml
today_routine:
  version: 1
  id: "daily-work-review"
  calendar_link:
    provider: "google"
    calendar_id: "example@group.calendar.google.com"
    event_id: "0123456789abcdefghijkl"
    scope: "series"
  tasks:
    - id: "review-issues"
      title: "담당 이슈 점검"
```

2026-10-21 오전 9시의 특정 회차에 연결하려면 위 `calendar_link`를 다음으로 바꾼다:

```yaml
  calendar_link:
    provider: "google"
    calendar_id: "example@group.calendar.google.com"
    event_id: "0123456789abcdefghijkl"
    scope: "occurrence"
    original_start_time:
      date_time: "2026-10-21T09:00:00+09:00"
```

종일 회차는 `date_time` 대신 `date: "2026-10-21"`을 사용한다. 단일 일정은 해당 일정의 `event_id`와 `scope: "event"`를 쓰고 `original_start_time`을 생략한다. 예시의 모든 ID는 설명용이며 실제 조회할 수 있는 ID가 아니다.

### 해석과 검증

- `calendar_id`만으로는 일정을 특정하지 않는다. 제목·시각이 비슷한 일정을 자동 선택하지 않는다.
- `event`는 반복되지 않는 단일 일정에만 사용한다. 반복 원본 또는 반복 회차 ID가 반환되면 범위 불일치로 보류한다.
- `series`와 `occurrence`의 `event_id`는 Google API의 반복 원본 ID이다. 회차 응답에서 얻을 때는 `recurringEventId`를 사용한다.
- `occurrence`는 `original_start_time.date_time`과 `.date` 중 정확히 하나가 필요하다. Google 응답의 `originalStartTime.dateTime` 또는 `.date` 값을 사용한다. 일정이 이동되어도 현재 시작 시각으로 바꾸지 않는다.
- `event`·`series`에서는 `original_start_time`을 허용하지 않는다. `date_time`은 유효한 날짜와 명시적 UTC 오프셋(`Z` 포함)을, `date`는 유효한 날짜를 요구한다.
- 문서 발행일 `date`나 `start_time`으로 회차를 추측하지 않는다. `original_start_time`은 실행 지시가 아니라 기존 회차의 식별 정보이다.
- 캘린더에 연결된 일정의 실제 시간·반복은 캘린더를 따른다. 피드의 `start_time`은 연결된 일정의 시각을 변경하지 않는다.
- 조회 권한 없음, 삭제·취소, 범위 불일치 또는 회차를 유일하게 찾지 못한 경우 연결을 보류한다. 피드 수신만으로 일정 생성·변경·참석자 알림을 수행하지 않는다.
- 이 확장은 대상 참조만 정의한다. 인증·권한 확보, Google ID와 EventKit 식별자 간 매핑, 연결 UI는 앱 구현에서 다뤄야 한다. Google `event.id`를 EventKit 일정 ID로 그대로 사용하지 않는다.

RSS/Atom 전송 시 기존 규칙대로 frontmatter를 포함한 Markdown 원문 전체를 보존한다.


Google 식별자 의미는 [Google Calendar Events API](https://developers.google.com/workspace/calendar/api/v3/reference/events)를 따른다. snake_case 필드명과 `scope`는 이 피드 포맷의 정의이다.

## RSS/Atom 전달 규칙

Markdown 원본에 설정이 있어도 발행기가 frontmatter를 제거하면 앱에서 작업을 찾을 수 없다. 발행기는 앞뒤 구분자를 포함한 원본 Markdown 전체를 본문에 보존해야 한다.

- RSS 2.0: `content:encoded`에 frontmatter를 포함한 Markdown 전체를 넣는다. CDATA를 쓰면 본문의 `]]>`를 적절히 분할한다. XML escape를 쓰면 파싱 후 원문이 복원되어야 한다.
- Atom: `content type="text/markdown"`에 frontmatter를 포함한 Markdown 전체를 XML escape하여 넣는다.
- HTML로 변환한 본문에 YAML을 섞거나 description/summary에만 일부 설정을 넣지 않는다.
- RSS guid / Atom id는 피드 항목 식별자다. today_routine.id는 문서의 실행 설정 식별자이며, 두 식별자의 역할을 구분한다.
