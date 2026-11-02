// Per-day completion state for the today timeline (card / outline).
// Bindings: DB (D1). Route: /<secret>/api/done
// Auth: none — secret URL path is the gate. Validates date + item_id only.
// done column: 0 = open, 1 = done, 2 = skip (backward compatible with 0/1 rows).
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const ID_RE = /^[\w.:@+\-]{1,200}$/;
const STATES = { open: 0, done: 1, skip: 2 };

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
  "Access-Control-Max-Age": "86400",
};

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Robots-Tag": "noindex, nofollow",
      ...CORS,
    },
  });
}
const err = (status, code, message) => json(status, { error: { code, message } });

function kstNow() {
  const d = new Date(Date.now() + 9 * 3600e3);
  return d.toISOString().replace(/\.\d+Z$/, "+09:00");
}

function parseState(body) {
  const s = String(body.state || body.status || "").toLowerCase();
  if (s === "open" || s === "done" || s === "skip") return s;
  if (body.skip === true || body.skip === 1 || body.skip === "1") return "skip";
  if (body.done === true || body.done === 1 || body.done === "1") return "done";
  if (body.done === false || body.done === 0 || body.done === "0") return "open";
  return null;
}

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (!env.DB) return err(500, "no_db", "D1 binding DB missing");

  if (request.method === "GET") {
    const url = new URL(request.url);
    const date = (url.searchParams.get("date") || "").trim();
    if (!DATE_RE.test(date)) return err(400, "invalid", "date must be YYYY-MM-DD");
    try {
      const { results } = await env.DB.prepare(
        "SELECT item_id, done FROM done WHERE date = ? AND done IN (1, 2)"
      ).bind(date).all();
      const done = [];
      const skip = [];
      for (const r of results || []) {
        if (Number(r.done) === 2) skip.push(r.item_id);
        else done.push(r.item_id);
      }
      return json(200, { date, done, skip });
    } catch (e) {
      return err(500, "db_error", String(e && e.message || e));
    }
  }

  if (request.method === "POST") {
    let body;
    try {
      const text = await request.text();
      if (text.length > 4096) return err(413, "too_large", "body too large");
      body = text.trim() ? JSON.parse(text) : {};
    } catch {
      return err(400, "bad_json", "body is not valid JSON");
    }
    if (!body || typeof body !== "object" || Array.isArray(body)) return err(400, "invalid", "body must be object");
    const date = String(body.date || "").trim();
    const item_id = String(body.item_id || "").trim();
    if (!DATE_RE.test(date)) return err(400, "invalid", "date must be YYYY-MM-DD");
    if (!ID_RE.test(item_id)) return err(400, "invalid", "item_id invalid");
    const state = parseState(body);
    if (!state) return err(400, "invalid", "state must be open|done|skip (or done/skip boolean)");
    const doneVal = STATES[state];
    const updated_at = kstNow();
    try {
      await env.DB.prepare(
        "INSERT INTO done (date, item_id, done, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(date, item_id) DO UPDATE SET done = excluded.done, updated_at = excluded.updated_at"
      ).bind(date, item_id, doneVal, updated_at).run();
      return json(200, {
        date,
        item_id,
        state,
        done: state === "done",
        skip: state === "skip",
        updated_at,
      });
    } catch (e) {
      return err(500, "db_error", String(e && e.message || e));
    }
  }

  return err(405, "method_not_allowed", "GET or POST only");
}
