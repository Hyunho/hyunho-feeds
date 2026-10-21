---
title: "주간 업무 점검"
date: "2026-10-20"
today_routine:
  version: 1
  id: "weekly-review-2026-10-20"
  start_time: "09:00"
  calendar_link:
    provider: "google"
    calendar_id: "example@group.calendar.google.com"
    event_id: "0123456789abcdefghijkl"
    scope: "series"
  tasks:
    - id: "review-issues"
      title: "담당 이슈 점검"
      duration_minutes: 20
      steps:
        - id: "check-new"
          title: "신규 할당 이슈 확인"
        - id: "check-overdue"
          title: "마감 초과 이슈 확인"
        - id: "set-priority"
          title: "오늘 처리할 이슈 선정"
    - id: "update-report"
      title: "주간보고 갱신"
      duration_minutes: 15
      steps:
        - id: "collect"
          title: "이번 주 완료 내역 정리"
        - id: "write"
          title: "주간보고에 반영"
---

# 주간 업무 점검

이번 주 진행 중인 이슈와 완료 내역을 확인합니다.

## 참고 사항

- 마감이 지난 이슈는 우선 확인합니다.
- 주간보고에는 완료 내역과 다음 계획을 구분합니다.

## 캘린더 연결 예시

위 `calendar_link`는 반복 일정 전체에 연결하는 예시입니다. ID는 설명용 가상 값이므로 실제 캘린더 ID와 반복 원본 일정 ID로 교체합니다. 연결이 필요 없으면 `calendar_link` 객체 전체를 생략합니다. `start_time`은 연결된 캘린더 일정의 시간을 변경하지 않습니다.

특정 회차만 지정하려면 위 객체를 다음으로 바꿉니다. `event_id`는 반복 원본 ID를 유지하고, 원래 시작 시각으로 회차를 식별합니다.

```yaml
  calendar_link:
    provider: "google"
    calendar_id: "example@group.calendar.google.com"
    event_id: "0123456789abcdefghijkl"
    scope: "occurrence"
    original_start_time:
      date_time: "2026-10-21T09:00:00+09:00"
```

종일 회차는 `date_time` 대신 `date: "2026-10-21"`을 씁니다. 단일 일정은 해당 일정 ID와 `scope: "event"`를 쓰고 `original_start_time`을 생략합니다. 날짜는 문서 발행일과 별개의 회차 식별 값입니다.

이 예시는 연결 대상 포맷만 정의하며, 오늘 회차에만 세부 작업을 저장하는 앱 기능이나 실제 캘린더 연동을 구현하지 않습니다. 전체 규칙은 [발행자 포맷 문서](../docs/today-routine-frontmatter-v1.md#google-캘린더-기존-일정-연결)를 참고합니다.
