// Per-day user subtasks for today timeline cards.
// Bindings: DB (D1). Route: /<secret>/api/subtasks
// Auth: none — secret URL path is the gate.
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const CARD_RE = /^[\w.:@+\-]{1,200}$/;
const ID_RE = /^[\w.:@+\-]{1,80}$/;
const TITLE_MAX = 200;
const PER_CARD_MAX = 50;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
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

function newId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "").slice(0, 16);
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
}

async function readBody(request) {
  const text = await request.text();
  if (text.length > 8192) return { error: err(413, "too_large", "body too large") };
  try {
    const body = text.trim() ? JSON.parse(text) : {};
    if (!body || typeof body !== "object" || Array.isArray(body)) return { error: err(400, "invalid", "body must be object") };
    return { body };
  } catch {
    return { error: err(400, "bad_json", "body is not valid JSON") };
  }
}

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (!env.DB) return err(500, "no_db", "D1 binding DB missing");

  if (request.method === "GET") {
    const url = new URL(request.url);
    const date = (url.searchParams.get("date") || "").trim();
    const card_id = (url.searchParams.get("card_id") || "").trim();
    if (!DATE_RE.test(date)) return err(400, "invalid", "date must be YYYY-MM-DD");
    if (card_id && !CARD_RE.test(card_id)) return err(400, "invalid", "card_id invalid");
    try {
      let results;
      if (card_id) {
        ({ results } = await env.DB.prepare(
          "SELECT card_id, id, title, done, created_at, updated_at FROM subtasks WHERE date = ? AND card_id = ? ORDER BY created_at ASC, id ASC"
        ).bind(date, card_id).all());
      } else {
        ({ results } = await env.DB.prepare(
          "SELECT card_id, id, title, done, created_at, updated_at FROM subtasks WHERE date = ? ORDER BY created_at ASC, id ASC"
        ).bind(date).all());
      }
      const items = (results || []).map((r) => ({
        card_id: r.card_id,
        id: r.id,
        title: r.title,
        done: Number(r.done) === 1,
        created_at: r.created_at,
        updated_at: r.updated_at,
      }));
      return json(200, { date, items, count: items.length });
    } catch (e) {
      return err(500, "db_error", String((e && e.message) || e));
    }
  }

  if (request.method === "POST") {
    const parsed = await readBody(request);
    if (parsed.error) return parsed.error;
    const body = parsed.body;
    const date = String(body.date || "").trim();
    const card_id = String(body.card_id || "").trim();
    if (!DATE_RE.test(date)) return err(400, "invalid", "date must be YYYY-MM-DD");
    if (!CARD_RE.test(card_id)) return err(400, "invalid", "card_id invalid");
    const updated_at = kstNow();

    // Update existing (toggle done / rename)
    if (body.id) {
      const id = String(body.id).trim();
      if (!ID_RE.test(id)) return err(400, "invalid", "id invalid");
      const hasDone = Object.prototype.hasOwnProperty.call(body, "done");
      const hasTitle = Object.prototype.hasOwnProperty.call(body, "title");
      if (!hasDone && !hasTitle) return err(400, "invalid", "provide done and/or title");
      try {
        const existing = await env.DB.prepare(
          "SELECT title, done FROM subtasks WHERE date = ? AND card_id = ? AND id = ?"
        ).bind(date, card_id, id).first();
        if (!existing) return err(404, "not_found", "subtask not found");
        let title = existing.title;
        let doneVal = Number(existing.done) ? 1 : 0;
        if (hasTitle) {
          title = String(body.title || "").trim();
          if (!title) return err(400, "invalid", "title required");
          if (title.length > TITLE_MAX) return err(400, "invalid", "title too long");
        }
        if (hasDone) doneVal = body.done === true || body.done === 1 || body.done === "1" ? 1 : 0;
        await env.DB.prepare(
          "UPDATE subtasks SET title = ?, done = ?, updated_at = ? WHERE date = ? AND card_id = ? AND id = ?"
        ).bind(title, doneVal, updated_at, date, card_id, id).run();
        return json(200, { date, card_id, id, title, done: doneVal === 1, updated_at });
      } catch (e) {
        return err(500, "db_error", String((e && e.message) || e));
      }
    }

    // Create
    const title = String(body.title || "").trim();
    if (!title) return err(400, "invalid", "title required");
    if (title.length > TITLE_MAX) return err(400, "invalid", "title too long");
    let id = String(body.new_id || body.create_id || "").trim();
    if (id) {
      if (!ID_RE.test(id)) return err(400, "invalid", "new_id invalid");
    } else {
      id = newId();
    }
    try {
      const { total } = await env.DB.prepare(
        "SELECT COUNT(*) AS total FROM subtasks WHERE date = ? AND card_id = ?"
      ).bind(date, card_id).first();
      if (Number(total) >= PER_CARD_MAX) return err(400, "limit", "too many subtasks on this card");
      await env.DB.prepare(
        "INSERT INTO subtasks (date, card_id, id, title, done, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?)"
      ).bind(date, card_id, id, title, updated_at, updated_at).run();
      return json(201, { date, card_id, id, title, done: false, created_at: updated_at, updated_at });
    } catch (e) {
      const msg = String((e && e.message) || e);
      if (/UNIQUE|constraint/i.test(msg)) return err(409, "conflict", "id already exists");
      return err(500, "db_error", msg);
    }
  }

  if (request.method === "DELETE") {
    const parsed = await readBody(request);
    if (parsed.error) return parsed.error;
    const body = parsed.body;
    const date = String(body.date || "").trim();
    const card_id = String(body.card_id || "").trim();
    const id = String(body.id || "").trim();
    if (!DATE_RE.test(date)) return err(400, "invalid", "date must be YYYY-MM-DD");
    if (!CARD_RE.test(card_id)) return err(400, "invalid", "card_id invalid");
    if (!ID_RE.test(id)) return err(400, "invalid", "id invalid");
    try {
      const info = await env.DB.prepare(
        "DELETE FROM subtasks WHERE date = ? AND card_id = ? AND id = ?"
      ).bind(date, card_id, id).run();
      const deleted = (info && info.meta && info.meta.changes) || 0;
      return json(200, { date, card_id, id, deleted });
    } catch (e) {
      return err(500, "db_error", String((e && e.message) || e));
    }
  }

  return err(405, "method_not_allowed", "GET, POST, or DELETE only");
}
