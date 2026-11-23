/**
 * Heal FIXED_ROUTINES when a stale valley-feed box Pages deploy wipes them.
 * Runs on a cron outside the Pages project so ad-hoc box deploys cannot remove it.
 *
 * Secrets: FEEDS_SECRET (32-hex private path), CLOUDFLARE_API_TOKEN (Pages + Account read)
 * Vars: ACCOUNT_ID, PAGES_PROJECT
 */
const MARKER = "var FIXED_ROUTINES=";
const UA = "hyunho-feeds-heal-fixed/1";

function todayUrl(env, host) {
  return `https://${host}/${env.FEEDS_SECRET}/today`;
}

async function fetchTodayHtml(env, host) {
  const r = await fetch(todayUrl(env, host), {
    headers: { "User-Agent": UA, "Cache-Control": "no-cache" },
    cf: { cacheTtl: 0, cacheEverything: false },
  });
  if (!r.ok) return { ok: false, status: r.status, html: "" };
  return { ok: true, status: r.status, html: await r.text() };
}

function hasFixed(html) {
  return typeof html === "string" && html.includes(MARKER) && html.includes("title:'아침 루틴'");
}

async function listDeployments(env) {
  const project = env.PAGES_PROJECT || "hyunho-feeds";
  const url =
    `https://api.cloudflare.com/client/v4/accounts/${env.ACCOUNT_ID}/pages/projects/${project}/deployments?per_page=25`;
  const r = await fetch(url, {
    headers: {
      Authorization: `Bearer ${env.CLOUDFLARE_API_TOKEN}`,
      "User-Agent": UA,
    },
  });
  const body = await r.json();
  if (!r.ok || !body.success) {
    throw new Error(`list deployments HTTP ${r.status}: ${JSON.stringify(body.errors || body)}`);
  }
  return body.result || [];
}

async function rollbackTo(env, deploymentId) {
  const project = env.PAGES_PROJECT || "hyunho-feeds";
  const url =
    `https://api.cloudflare.com/client/v4/accounts/${env.ACCOUNT_ID}/pages/projects/${project}/deployments/${deploymentId}/rollback`;
  const r = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.CLOUDFLARE_API_TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": UA,
    },
  });
  const body = await r.json();
  if (!r.ok || !body.success) {
    throw new Error(`rollback HTTP ${r.status}: ${JSON.stringify(body.errors || body)}`);
  }
  return body.result;
}

async function heal(env) {
  if (!env.FEEDS_SECRET || !/^[0-9a-f]{32}$/.test(env.FEEDS_SECRET)) {
    return { action: "error", reason: "FEEDS_SECRET missing or invalid" };
  }
  if (!env.CLOUDFLARE_API_TOKEN || !env.ACCOUNT_ID) {
    return { action: "error", reason: "CLOUDFLARE_API_TOKEN or ACCOUNT_ID missing" };
  }

  const live = await fetchTodayHtml(env, "hyunho-feeds.pages.dev");
  if (live.ok && hasFixed(live.html)) {
    return { action: "ok", reason: "FIXED_ROUTINES present" };
  }

  const deps = await listDeployments(env);
  // Prefer a previous successful production deploy whose preview today still has FIXED_ROUTINES.
  for (const dep of deps) {
    if (!dep || dep.is_skipped) continue;
    const host = new URL(dep.url).host; // e.g. 508aa050.hyunho-feeds.pages.dev
    if (host === "hyunho-feeds.pages.dev") continue;
    const page = await fetchTodayHtml(env, host);
    if (!page.ok || !hasFixed(page.html)) continue;
    // Skip if this deployment is already production (rollback API rejects that).
    const aliases = dep.aliases || [];
    if (aliases.includes("hyunho-feeds.pages.dev") || dep.environment !== "production") {
      // Still try non-aliased previous builds; production env copies are fine if not current.
    }
    try {
      await rollbackTo(env, dep.id);
      return {
        action: "rollback",
        deploymentId: dep.id,
        preview: dep.url,
        commit: (dep.deployment_trigger && dep.deployment_trigger.metadata &&
          dep.deployment_trigger.metadata.commit_hash) || "",
      };
    } catch (e) {
      // Try next candidate (e.g. "cannot rollback to current").
      continue;
    }
  }
  return { action: "error", reason: "no rollback candidate with FIXED_ROUTINES found" };
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      heal(env)
        .then((r) => console.log("heal", JSON.stringify(r)))
        .catch((e) => console.log("heal failed", String(e)))
    );
  },
  async fetch(request, env) {
    // Manual trigger: GET /heal (no auth beyond Worker URL obscurity; token not returned).
    const url = new URL(request.url);
    if (url.pathname !== "/heal" && url.pathname !== "/") {
      return new Response("not found", { status: 404 });
    }
    try {
      const result = await heal(env);
      return new Response(JSON.stringify(result, null, 2), {
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    } catch (e) {
      return new Response(JSON.stringify({ action: "error", reason: String(e) }, null, 2), {
        status: 500,
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    }
  },
};
