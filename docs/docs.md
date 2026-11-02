# hyunho todo inbox: guide for AI agents

> A small HTTP inbox where AI agents add todo items for hyunho (timezone Asia/Seoul, KST = UTC+09:00).
> hyunho's own iPhone app fetches the items, saves them, and then clears them.
> Full spec (OpenAPI 3.1): https://hyunho-feeds.pages.dev/api/inbox/openapi.json

- Endpoint: `https://hyunho-feeds.pages.dev/api/inbox`
- Auth: `Authorization: Bearer <TOKEN>` on every GET/POST/DELETE. hyunho gives you the token separately. Never print, log, commit, or put it in a URL. These docs are public and need no token.
- Format: JSON in, JSON out (`Content-Type: application/json`).

## When to use it

- hyunho asked you to remind them, follow up, or "put it on my todo list".
- Your task produced a concrete action item that hyunho has to do.

Add **one item per task**. Keep `title` short and imperative. Put context and links in `note`. **Always set `source` to your own name.**
Agents normally **only POST**. Don't delete other agents' items, and never send `{"all": true}`. Clearing is the app's job.

## Add a todo (POST)

```bash
curl -sS -X POST https://hyunho-feeds.pages.dev/api/inbox \
  -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" \
  -d '{"title":"전기요금 납부","note":"고지서: https://example.com/bill","due":"2026-10-07","priority":"high","source":"deals-bot","tags":["생활","결제"]}'
```

Fields:

| field | required | meaning |
|---|---|---|
| `title` | yes | task title, ≤ 500 chars (Korean or English) |
| `note` | no | details/links, ≤ 5000 chars |
| `due` | no | **KST**. `YYYY-MM-DD` (all-day) or `YYYY-MM-DDTHH:MM[:SS]`. No offset means +09:00. Resolve "tomorrow" etc. to a real date yourself. |
| `priority` | no | `low` \| `normal` \| `high` \| `urgent` (omit if unknown; use `urgent` sparingly) |
| `source` | recommended | name of the AI/bot adding it, ≤ 100 chars |
| `tags` | no | array of strings (or a comma-separated string); max 20 tags, 50 chars each |

Response `201`:

```json
{"item":{"id":"01a10e48-2c0f-756e-9832-12695d45a818","created_at":"2026-10-06T07:56:13+09:00","title":"전기요금 납부","note":"고지서: https://example.com/bill","due":"2026-10-07","priority":"high","source":"deals-bot","tags":["생활","결제"]}}
```

The 201 is your confirmation. You don't need to re-read the item.

## List pending todos (GET)

```bash
curl -sS "https://hyunho-feeds.pages.dev/api/inbox?limit=100" -H "Authorization: Bearer <TOKEN>"
```

`200 {"items":[...oldest first...],"count":N,"has_more":false}` (`limit` 1-500, default 100). Use it to avoid adding duplicates.

## Clear by ids (DELETE, used by the app)

```bash
curl -sS -X DELETE https://hyunho-feeds.pages.dev/api/inbox \
  -H "Authorization: Bearer <TOKEN>" -H "Content-Type: application/json" \
  -d '{"ids":["01a10e48-2c0f-756e-9832-12695d45a818"]}'
```

`200 {"deleted":1,"ids":[...]}`: idempotent, so unknown ids are ignored. Max 1000 ids. `{"all": true}` exists but is not recommended.

**Recommended app flow:** GET, save locally keyed by `id` (skip ids you already have), DELETE exactly those ids, and repeat while `has_more`. If DELETE fails, retry it next poll.

## Consistency

Storage is Cloudflare KV: **eventually consistent, up to ~60 s**. New items may not show in GET immediately, and deleted items may briefly reappear. **Always de-duplicate by `id`.** Don't poll in a tight loop to confirm writes.

## Errors

`{"error":{"code":"...","message":"..."}}`

- `400 invalid` / `400 bad_json`: fix the request (e.g. "title is required"). Don't retry it unchanged.
- `401 unauthorized`: missing or wrong token.
- `405 method_not_allowed`
- `413 too_large`: body over 16 KB.
- `503 not_configured`: temporary server issue, retry later.

CORS: `Access-Control-Allow-Origin: *`, methods `GET, POST, DELETE, OPTIONS`, headers `Authorization, Content-Type`.
