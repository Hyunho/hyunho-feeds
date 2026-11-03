// Todo inbox: AI agents POST todos, the user's app GETs them and DELETEs the ids it saved.
// Bindings: INBOX (KV namespace), INBOX_TOKEN (secret). Route: /api/inbox
// Keys: "todo:<uuidv7>" -> UUIDv7 is time-ordered, so KV's lexicographic list = oldest first.

const PRIORITIES = ["low", "normal", "high", "urgent"];
const MAX_BODY = 16 * 1024;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$/;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type",
  "Access-Control-Max-Age": "86400",
};

function json(status, body, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
               "X-Robots-Tag": "noindex, nofollow", ...CORS, ...extra },
  });
}
const err = (status, code, message, extra) => json(status, { error: { code, message } }, extra);

function uuidv7() {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  const ms = Date.now();
  for (let i = 0; i < 6; i++) b[i] = Math.floor(ms / 2 ** (8 * (5 - i))) & 0xff;
  b[6] = (b[6] & 0x0f) | 0x70;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function kstNow() {
  const d = new Date(Date.now() + 9 * 3600e3);
  return d.toISOString().replace(/\.\d+Z$/, "+09:00");
}

function safeEqual(a, b) {
  const ea = new TextEncoder().encode(a), eb = new TextEncoder().encode(b);
  let diff = ea.length ^ eb.length;
  for (let i = 0; i < Math.max(ea.length, eb.length); i++) diff |= (ea[i] || 0) ^ (eb[i] || 0);
  return diff === 0;
}

async function readJson(request) {
  const text = await request.text();
  if (text.length > MAX_BODY) throw [413, "too_large", `body over ${MAX_BODY} bytes`];
  if (!text.trim()) return {};
  try { return JSON.parse(text); } catch { throw [400, "bad_json", "body is not valid JSON"]; }
}

function str(v, field, max, required = false) {
  if (v === undefined || v === null || v === "") {
    if (required) throw [400, "invalid", `${field} is required`];
    return undefined;
  }
  if (typeof v !== "string") throw [400, "invalid", `${field} must be a string`];
  const s = v.trim();
  if (required && !s) throw [400, "invalid", `${field} is required`];
  if (s.length > max) throw [400, "invalid", `${field} max ${max} chars`];
  return s || undefined;
}

function normDue(v) {
  if (v === undefined || v === null || v === "") return undefined;
  if (typeof v !== "string") throw [400, "invalid", "due must be an ISO date or datetime string"];
  const s = v.trim();
  if (DATE_RE.test(s)) {
    if (isNaN(Date.parse(s + "T00:00:00+09:00"))) throw [400, "invalid", "due is not a valid date"];
    return s;
  }
  if (!DT_RE.test(s)) throw [400, "invalid", "due must be YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS][+09:00]"];
  const withTz = /(Z|[+-]\d{2}:\d{2})$/.test(s) ? s : s + "+09:00"; // no offset -> KST
  if (isNaN(Date.parse(withTz))) throw [400, "invalid", "due is not a valid datetime"];
  return withTz;
}

function validateTodo(b) {
  if (!b || typeof b !== "object" || Array.isArray(b)) throw [400, "invalid", "body must be a JSON object"];
  const todo = { title: str(b.title, "title", 500, true) };
  const note = str(b.note, "note", 5000); if (note) todo.note = note;
  const due = normDue(b.due); if (due) todo.due = due;
  if (b.priority !== undefined && b.priority !== null && b.priority !== "") {
    const p = String(b.priority).toLowerCase();
    if (!PRIORITIES.includes(p)) throw [400, "invalid", `priority must be one of ${PRIORITIES.join(", ")}`];
    todo.priority = p;
  }
  const source = str(b.source, "source", 100); if (source) todo.source = source;
  if (b.tags !== undefined && b.tags !== null) {
    let tags = b.tags;
    if (typeof tags === "string") tags = tags.split(",");
    if (!Array.isArray(tags) || tags.some((t) => typeof t !== "string")) throw [400, "invalid", "tags must be an array of strings"];
    tags = [...new Set(tags.map((t) => t.trim()).filter(Boolean))];
    if (tags.length > 20 || tags.some((t) => t.length > 50)) throw [400, "invalid", "max 20 tags, 50 chars each"];
    if (tags.length) todo.tags = tags;
  }
  return todo;
}

async function listAll(kv, limit) {
  const keys = [];
  let cursor;
  do {
    const r = await kv.list({ prefix: "todo:", cursor, limit: Math.min(1000, limit + 1 - keys.length) });
    keys.push(...r.keys.map((k) => k.name));
    cursor = r.list_complete ? undefined : r.cursor;
  } while (cursor && keys.length <= limit);
  const has_more = keys.length > limit;
  const vals = await Promise.all(keys.slice(0, limit).map((k) => kv.get(k, "json")));
  return { items: vals.filter(Boolean), has_more };
}

export async function onRequest({ request, env }) {
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (!env.INBOX || !env.INBOX_TOKEN) return err(503, "not_configured", "inbox storage or token not configured");
  const auth = request.headers.get("Authorization") || "";
  const m = auth.match(/^Bearer\s+(.+)$/i);
  if (!m || !safeEqual(m[1].trim(), env.INBOX_TOKEN))
    return err(401, "unauthorized", "missing or invalid bearer token", { "WWW-Authenticate": 'Bearer realm="inbox"' });

  try {
    if (request.method === "POST") {
      const todo = validateTodo(await readJson(request));
      const item = { id: uuidv7(), created_at: kstNow(), ...todo };
      await env.INBOX.put("todo:" + item.id, JSON.stringify(item));
      return json(201, { item });
    }
    if (request.method === "GET") {
      const lim = parseInt(new URL(request.url).searchParams.get("limit") || "100", 10);
      const limit = Math.max(1, Math.min(isNaN(lim) ? 100 : lim, 500));
      const { items, has_more } = await listAll(env.INBOX, limit);
      return json(200, { items, count: items.length, has_more });
    }
    if (request.method === "DELETE") {
      const b = await readJson(request);
      if (b.all === true) {
        let deleted = 0, cursor;
        do {
          const r = await env.INBOX.list({ prefix: "todo:", cursor });
          await Promise.all(r.keys.map((k) => env.INBOX.delete(k.name)));
          deleted += r.keys.length;
          cursor = r.list_complete ? undefined : r.cursor;
        } while (cursor);
        return json(200, { deleted, all: true });
      }
      const ids = b.ids;
      if (!Array.isArray(ids) || !ids.length) throw [400, "invalid", 'body must be {"ids": [...]} (or {"all": true})'];
      if (ids.length > 1000) throw [400, "invalid", "max 1000 ids per request"];
      const bad = ids.filter((id) => typeof id !== "string" || !UUID_RE.test(id));
      if (bad.length) throw [400, "invalid", `invalid id(s): ${bad.slice(0, 5).join(", ")}`];
      const uniq = [...new Set(ids)];
      await Promise.all(uniq.map((id) => env.INBOX.delete("todo:" + id)));
      return json(200, { deleted: uniq.length, ids: uniq });
    }
    return err(405, "method_not_allowed", "use GET, POST, DELETE", { Allow: "GET, POST, DELETE, OPTIONS" });
  } catch (e) {
    if (Array.isArray(e)) return err(e[0], e[1], e[2]);
    return err(500, "internal", "unexpected error");
  }
}
