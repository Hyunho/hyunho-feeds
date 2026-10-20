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
| today_routine.tasks | 배열 | 예 | 한 개 이상의 작업, 배열 순서대로 표시 |
| tasks[].id | 문자열 | 예 | 문서 안에서 고유한 작업 식별자 |
| tasks[].title | 문자열 | 예 | 공백만으로 구성되지 않은 작업명 |
| tasks[].duration_minutes | 정수 | 아니오 | 1 이상인 예상 총 소요 시간. 생략하면 미지정 |
| tasks[].steps | 배열 | 아니오 | 세부 작업 목록. 생략하거나 빈 배열이면 단일 작업 |
| steps[].id | 문자열 | 예 | 부모 작업 안에서 고유한 세부 작업 식별자 |
| steps[].title | 문자열 | 예 | 공백만으로 구성되지 않은 세부 작업명 |

ID는 소문자 영문·숫자로 시작하고 소문자 영문·숫자·하이픈·밑줄만 사용한다: `^[a-z0-9][a-z0-9_-]*$`.
제목·배열 순서·문서 경로가 바뀌어도 같은 작업이면 ID를 유지한다. 별개의 문서나 작업에는 새 ID를 부여한다. 세부 작업은 한 단계만 지원한다.

YAML 중복 키, 사용자 정의 태그, 앵커·별칭은 사용하지 않는다. 작업의 완료 여부, 사용자별 진행 상태, 실행 날짜는 frontmatter에 넣지 않는다. `date`는 발행일이다. v1에 없는 필드에 의존하는 작업 동작은 정의하지 않는다.

## RSS/Atom 전달 규칙

Markdown 원본에 설정이 있어도 발행기가 frontmatter를 제거하면 앱에서 작업을 찾을 수 없다. 발행기는 앞뒤 구분자를 포함한 원본 Markdown 전체를 본문에 보존해야 한다.

- RSS 2.0: `content:encoded`에 frontmatter를 포함한 Markdown 전체를 넣는다. CDATA를 쓰면 본문의 `]]>`를 적절히 분할한다. XML escape를 쓰면 파싱 후 원문이 복원되어야 한다.
- Atom: `content type="text/markdown"`에 frontmatter를 포함한 Markdown 전체를 XML escape하여 넣는다.
- HTML로 변환한 본문에 YAML을 섞거나 description/summary에만 일부 설정을 넣지 않는다.
- RSS guid / Atom id는 피드 항목 식별자다. today_routine.id는 문서의 실행 설정 식별자이며, 두 식별자의 역할을 구분한다.
