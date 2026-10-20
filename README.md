# hyunho-feeds

구독 피드의 Markdown 문서와 오늘의 루틴 앱에서 사용할 작업 메타데이터 포맷을 정리합니다.

- [피드 작업 frontmatter v1 명세](docs/today-routine-frontmatter-v1.md)
- [복사해서 사용할 Markdown 예시](examples/weekly-review.md)

문서 시작의 YAML frontmatter에 `today_routine` 설정을 넣어 작업과 세부 작업을 선언합니다. 본문에는 참고할 내용을 작성합니다. 문서를 복사할 때 문서 ID를 새로 지정하고, 같은 문서의 제목이나 내용만 수정할 때는 ID를 유지합니다.

현재는 포맷 명세와 예시만 제공합니다. 피드 발행기의 메타데이터 보존과 앱의 작업 선택·추가·진행 기록 기능은 별도 구현이 필요합니다.
