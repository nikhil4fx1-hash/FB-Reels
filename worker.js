// Cloudflare Worker: instant Telegram -> GitHub trigger + message store.
//
// 1) Telegram sends every message here (webhook). Messages are stored in KV.
// 2) Text messages (topic, "reel", "cancel", "/new ...") immediately start the GitHub workflow.
//    Photos/videos are only stored, so sending 7 photos does not start 7 workflow runs.
// 3) The bot scripts read stored messages from GET /updates (same job as Telegram's getUpdates).

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // ---- Telegram webhook ----
    if (request.method === "POST" && url.pathname === "/telegram") {
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      const update = await request.json();
      const msg = update.message;
      if (!msg || String(msg.chat.id) !== String(env.TELEGRAM_CHAT_ID)) {
        return new Response("ok");
      }
      const id = String(update.update_id).padStart(12, "0");
      await env.UPDATES.put("u:" + id, JSON.stringify(update), { expirationTtl: 172800 });

      const doc = msg.document || {};
      const isMedia = msg.photo || msg.video || /^(image|video)\//.test(doc.mime_type || "");
      if (!isMedia && msg.text) {
        ctx.waitUntil(triggerGithub(env));
      }
      return new Response("ok");
    }

    // ---- stored messages for the bot scripts ----
    if (request.method === "GET" && url.pathname === "/updates") {
      if (request.headers.get("Authorization") !== "Bearer " + env.API_KEY) {
        return new Response("forbidden", { status: 403 });
      }
      const offset = parseInt(url.searchParams.get("offset") || "0", 10);
      const list = await env.UPDATES.list({ prefix: "u:", limit: 1000 });
      const result = [];
      for (const k of list.keys) {
        const id = parseInt(k.name.slice(2), 10);
        if (id < offset) {                       // already handled: clean up
          ctx.waitUntil(env.UPDATES.delete(k.name));
          continue;
        }
        const v = await env.UPDATES.get(k.name);
        if (v) result.push(JSON.parse(v));
      }
      result.sort((a, b) => a.update_id - b.update_id);
      return Response.json({ ok: true, result });
    }

    return new Response("reels worker is running");
  },
};

async function triggerGithub(env) {
  const r = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/bot.yml/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "reels-worker",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      body: JSON.stringify({ ref: env.GITHUB_BRANCH || "main" }),
    }
  );
  if (!r.ok) console.log("GitHub dispatch failed", r.status, await r.text());
}
