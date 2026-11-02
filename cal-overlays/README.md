# Calendar overlays for today-cal

today-cal.js fetches these same-origin ICS files in addition to Pages iCal secrets
(개인 / Workout / Routine / Runna):

- `family.ics` → 가족 (RSVP ACCEPTED only, via ORGANIZER + ATTENDEE filter)
- `family-one.ics` → 가족 one

`publish.py build/deploy` copies `*.ics` here to `public/<secret>/cal/`.
Runna uses the `ICAL_RUNNA_URL` Pages secret (live fetch), not an overlay file.
대한민국의 휴일은 public Google holiday ICS (no overlay file).
