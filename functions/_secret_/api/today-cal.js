// Live calendar events for the today page. iCal URLs come from Pages secrets only.
const CALS = [["ICAL_PRIMARY_URL", "개인"], ["ICAL_WORKOUT_URL", "Workout"], ["ICAL_ROUTINE_URL", "Routine"], ["ICAL_RUNNA_URL", "Runna"]];
// Extra calendars without Pages secrets: same-origin overlays under /<secret>/cal/*.ics (built by publish.py).
const OVERLAYS = [["cal/family.ics", "가족"], ["cal/family-one.ics", "가족 one"]];
const HOLIDAY_URL = "https://calendar.google.com/calendar/ical/ko.south_korea%23holiday%40group.v.calendar.google.com/public/basic.ics";
// Events organized by these (family) calendars are hidden unless hyunho accepted them.
const HIDE_ORGANIZERS = ["e67c3c0dc31539bff07d2b4150d5d6feea8de16b85ddae875c8bc1eefd0ba35b@group.calendar.google.com", "family14205488799933840083@group.calendar.google.com"];
const KST = 9 * 60; // minutes
const DAYS = ["SU", "MO", "TU", "WE", "TH", "FR", "SA"];

function unfold(t) { return t.replace(/\r\n/g, "\n").replace(/\n[ \t]/g, ""); }
function unesc(s) { return s.replace(/\\n/gi, " ").replace(/\\([,;\\])/g, "$1"); }
function parseEvents(text) {
  const out = []; let cur = null, depth = 0, relcalid = "";
  for (const line of unfold(text).split("\n")) {
    if (line === "BEGIN:VEVENT") { cur = { p: {}, exdate: [], att: [] }; depth = 0; continue; }
    if (line === "END:VEVENT") { if (cur) out.push(cur); cur = null; continue; }
    if (!cur) {
      if (line.startsWith("X-WR-RELCALID:")) relcalid = line.slice("X-WR-RELCALID:".length).trim();
      continue;
    }
    if (line.startsWith("BEGIN:")) { depth++; continue; }
    if (line.startsWith("END:")) { depth--; continue; }
    if (depth > 0) continue;
    const i = line.indexOf(":"); if (i < 0) continue;
    const head = line.slice(0, i), val = line.slice(i + 1);
    const parts = head.split(";"), name = parts[0].toUpperCase(), params = {};
    for (const p of parts.slice(1)) { const j = p.indexOf("="); if (j > 0) params[p.slice(0, j).toUpperCase()] = p.slice(j + 1).replace(/"/g, ""); }
    if (name === "ATTENDEE") cur.att.push({ v: val.toLowerCase(), partstat: (params.PARTSTAT || "").toUpperCase() });
    else if (name === "EXDATE") { for (const v of val.split(",")) cur.exdate.push({ v, params }); }
    else cur.p[name] = { v: val, params };
  }
  return { events: out, relcalid };
}
function calendarIdFromUrl(src) {
  try {
    const m = new URL(src).pathname.match(/\/calendar\/ical\/([^/]+)\//i);
    if (m) return decodeURIComponent(m[1]);
  } catch (e) {}
  return "";
}
function calendarIdFromOrganizers(evs) {
  const ids = new Set();
  for (const ev of evs) {
    const org = (ev.p.ORGANIZER?.v || "").replace(/^mailto:/i, "").trim();
    const low = org.toLowerCase();
    if (low.includes("@group.calendar.google.com") || low.includes("@group.v.calendar.google.com")) ids.add(org);
  }
  return ids.size === 1 ? [...ids][0] : "";
}
function eventIdFromUid(uid) {
  const s = String(uid || "");
  const at = s.lastIndexOf("@");
  return at > 0 ? s.slice(0, at) : s;
}
function utcStamp(ms) {
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, "0");
  return "" + d.getUTCFullYear() + p(d.getUTCMonth() + 1) + p(d.getUTCDate()) + "T" + p(d.getUTCHours()) + p(d.getUTCMinutes()) + p(d.getUTCSeconds()) + "Z";
}
function ymdKst(ms) {
  const p = kstParts(ms);
  return p.y + "-" + String(p.mo + 1).padStart(2, "0") + "-" + String(p.d).padStart(2, "0");
}
// Returns {ms (UTC epoch ms), allDay}
function parseDT(prop) {
  if (!prop) return null;
  const v = prop.v, params = prop.params || {};
  let m = v.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (m || params.VALUE === "DATE") { m = m || v.match(/^(\d{4})(\d{2})(\d{2})/); return { ms: Date.UTC(+m[1], m[2] - 1, +m[3]) - KST * 60000, allDay: true }; }
  m = v.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$/);
  if (!m) return null;
  let ms = Date.UTC(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
  if (!m[7]) ms -= tzOffsetMin(params.TZID || "Asia/Seoul", ms) * 60000;
  return { ms, allDay: false };
}
function tzOffsetMin(tz, ms) {
  try {
    const f = new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const o = {}; for (const p of f.formatToParts(new Date(ms))) o[p.type] = p.value;
    return Math.round((Date.UTC(+o.year, o.month - 1, +o.day, +o.hour, +o.minute, +o.second) - ms) / 60000);
  } catch (e) { return KST; }
}
function kstParts(ms) { const d = new Date(ms + KST * 60000); return { y: d.getUTCFullYear(), mo: d.getUTCMonth(), d: d.getUTCDate(), h: d.getUTCHours(), mi: d.getUTCMinutes(), wd: d.getUTCDay() }; }
function kstDayStart(dateStr) { const [y, m, d] = dateStr.split("-").map(Number); return Date.UTC(y, m - 1, d) - KST * 60000; }
function hhmm(ms) { const p = kstParts(ms); return String(p.h).padStart(2, "0") + ":" + String(p.mi).padStart(2, "0"); }
const DAY = 86400000;

// Expand occurrences (start ms) of a master event that may fall on [dayS, dayE) — DAILY/WEEKLY/MONTHLY/YEARLY basic.
function occurrences(ev, start, dayS, dayE, durMs) {
  const rr = ev.p.RRULE; if (!rr) return (start.ms < dayE && start.ms + Math.max(durMs, 1) > dayS) ? [start.ms] : [];
  const r = {}; for (const kv of rr.v.split(";")) { const [k, v] = kv.split("="); r[k.toUpperCase()] = v; }
  const freq = r.FREQ, interval = +(r.INTERVAL || 1), count = r.COUNT ? +r.COUNT : Infinity;
  let until = Infinity; if (r.UNTIL) { const u = parseDT({ v: r.UNTIL, params: {} }); if (u) until = u.allDay ? u.ms + DAY - 1 : u.ms; }
  const sp = kstParts(start.ms), tod = start.ms - (Date.UTC(sp.y, sp.mo, sp.d) - KST * 60000);
  const byday = r.BYDAY ? r.BYDAY.split(",").map(s => DAYS.indexOf(s.slice(-2))).filter(x => x >= 0) : null;
  const startDayIdx = Math.floor((Date.UTC(sp.y, sp.mo, sp.d)) / DAY);
  const res = []; let n = 0;
  const limitDay = Math.floor((dayE + KST * 60000) / DAY) + 1;
  for (let di = startDayIdx; di <= limitDay; di++) {
    const dd = new Date(di * DAY), wd = dd.getUTCDay(); let ok = false;
    if (freq === "DAILY") ok = (di - startDayIdx) % interval === 0 && (!byday || byday.includes(wd));
    else if (freq === "WEEKLY") {
      const wk0 = Math.floor((startDayIdx - ((new Date(startDayIdx * DAY).getUTCDay() + 6) % 7)) / 7);
      const wk = Math.floor((di - ((wd + 6) % 7)) / 7);
      ok = (wk - wk0) % interval === 0 && (byday ? byday.includes(wd) : wd === sp.wd);
    } else if (freq === "MONTHLY") {
      const mdiff = (dd.getUTCFullYear() - sp.y) * 12 + dd.getUTCMonth() - sp.mo;
      ok = mdiff % interval === 0 && dd.getUTCDate() === sp.d && !byday;
    } else if (freq === "YEARLY") {
      ok = (dd.getUTCFullYear() - sp.y) % interval === 0 && dd.getUTCMonth() === sp.mo && dd.getUTCDate() === sp.d;
    } else return [start.ms];
    if (!ok) continue;
    const ms = di * DAY - KST * 60000 + tod;
    if (ms < start.ms) continue;
    if (ms > until) break;
    if (++n > count) break;
    if (ms < dayE && ms + Math.max(durMs, 1) > dayS) res.push(ms);
  }
  return res;
}

export function collectIcsEvents(text, date, cal, src) {
  const dayS = kstDayStart(date), dayE = dayS + DAY;
  const events = [];
  const parsed = parseEvents(text);
  const evs = parsed.events;
  const calendarId = parsed.relcalid || calendarIdFromUrl(src) || calendarIdFromOrganizers(evs);
  const overrides = {}; // uid -> set of recurrence-id ms
  for (const ev of evs) if (ev.p["RECURRENCE-ID"]) { const uid = ev.p.UID?.v; const rid = parseDT(ev.p["RECURRENCE-ID"]); if (uid && rid) (overrides[uid] = overrides[uid] || new Set()).add(rid.ms); }
  for (const ev of evs) {
    const st = parseDT(ev.p.DTSTART); if (!st) continue;
    if ((ev.p.STATUS?.v || "").toUpperCase() === "CANCELLED") continue;
    let en = parseDT(ev.p.DTEND), durMs;
    if (en) durMs = en.ms - st.ms;
    else if (ev.p.DURATION) { const m = ev.p.DURATION.v.match(/P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?/); durMs = m ? (((+(m[1] || 0) * 7 + +(m[2] || 0)) * 24 + +(m[3] || 0)) * 60 + +(m[4] || 0)) * 60000 + +(m[5] || 0) * 1000 : 0; }
    else durMs = st.allDay ? DAY : 0;
    { const org = (ev.p.ORGANIZER?.v || "").toLowerCase(); if (HIDE_ORGANIZERS.some(h => org.includes(h)) && !ev.att.some(a => a.v.includes("oksknoh@gmail.com") && a.partstat === "ACCEPTED")) continue; }
    const uid = ev.p.UID?.v;
    let list;
    if (ev.p["RECURRENCE-ID"]) list = (st.ms < dayE && st.ms + Math.max(durMs, 1) > dayS) ? [st.ms] : [];
    else {
      list = occurrences(ev, st, dayS, dayE, durMs);
      const ex = new Set(ev.exdate.map(x => parseDT(x)).filter(Boolean).map(x => x.ms));
      const ov = overrides[uid] || new Set();
      list = list.filter(ms => !ex.has(ms) && !ov.has(ms));
    }
    const title = unesc(ev.p.SUMMARY?.v || "(제목 없음)");
    const eventId = eventIdFromUid(uid);
    const rec = ev.p["RECURRENCE-ID"] ? parseDT(ev.p["RECURRENCE-ID"]) : null;
    const recurring = !!(ev.p.RRULE || rec);
    for (const ms of list) {
      // Timed: only events starting this day. All-day: any occurrence overlapping the day.
      if (!st.allDay && ms < dayS) continue;
      const originalMs = rec ? rec.ms : ms;
      const allDay = !!(st.allDay || (rec && rec.allDay));
      const originalDate = allDay ? ymdKst(originalMs) : "";
      const instanceId = !eventId ? "" : (recurring ? eventId + "_" + (allDay ? originalDate.replace(/-/g, "") : utcStamp(originalMs)) : eventId);
      const row = { id: (uid || title) + "@" + ms, title, calendar: cal, calendarId, eventId, instanceId, recurring, originalMs, originalDate };
      if (allDay) events.push({ ...row, start: "종일", end: "종일", duration: null, allDay: true });
      else events.push({ ...row, start: hhmm(ms), end: hhmm(ms + durMs), duration: Math.round(durMs / 60000), allDay: false });
    }
  }
  return events;
}

export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  let date = url.searchParams.get("date");
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) date = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul" }).format(new Date());
  const events = [], errors = [];
  const base = request.url.replace(/\/api\/today-cal.*$/, "/");
  const sources = [
    ...CALS.map(([key, cal]) => ({ cal, src: env[key], kind: "secret" })),
    ...OVERLAYS.map(([path, cal]) => ({ cal, src: base + path, kind: "overlay" })),
    { cal: "대한민국의 휴일", src: HOLIDAY_URL, kind: "public" },
  ];
  await Promise.all(sources.map(async ({ cal, src, kind }) => {
    if (!src) { errors.push(cal + ": not configured"); return; }
    let text;
    try { const r = await fetch(src, { cf: { cacheTtl: 60 } }); if (!r.ok) throw new Error("HTTP " + r.status); text = await r.text(); }
    catch (e) { errors.push(cal + ": fetch failed"); return; }
    events.push(...collectIcsEvents(text, date, cal, src));
  }));
  events.sort((a, b) => (b.allDay ? 1 : 0) - (a.allDay ? 1 : 0) || a.start.localeCompare(b.start) || a.title.localeCompare(b.title));
  return new Response(JSON.stringify({ date, events, errors }), {
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "private, max-age=60", "x-robots-tag": "noindex" },
  });
}
