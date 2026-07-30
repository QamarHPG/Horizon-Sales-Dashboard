// Puts the Horizon dashboard behind a shared username/password (HTTP Basic Auth)
// before serving the static site, and proxies live per-range click lookups
// to Instantly (see handleRangeClicks) so the dashboard never needs the
// Instantly API key client-side.
//
// Required secrets (set in Cloudflare, never in this file):
//   BASIC_AUTH_USER
//   BASIC_AUTH_PASS
//   INSTANTLY_API_KEY

export default {
  async fetch(request, env) {
    const expected = "Basic " + btoa(`${env.BASIC_AUTH_USER}:${env.BASIC_AUTH_PASS}`);
    const provided = request.headers.get("Authorization");

    if (provided !== expected) {
      return new Response("Authentication required", {
        status: 401,
        headers: { "WWW-Authenticate": 'Basic realm="Horizon Dashboard"' },
      });
    }

    const url = new URL(request.url);
    if (url.pathname === "/api/range-clicks") {
      return handleRangeClicks(url, env);
    }

    return env.ASSETS.fetch(request);
  },
};

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const ID_RE = /^[a-zA-Z0-9-]+$/;
const MAX_IDS = 20;

// Custom date ranges are picked live in the browser, long after refresh.py
// has already baked the daily/lifetime data into index.html — there's no way
// to precompute an exact deduped click count for an arbitrary range ahead of
// time. This calls Instantly's ranged overview endpoint live, per campaign,
// using a Worker-only secret, and returns just the unique-click counts.
async function handleRangeClicks(url, env) {
  const start = url.searchParams.get("start") || "";
  const end = url.searchParams.get("end") || "";
  const ids = (url.searchParams.get("ids") || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, MAX_IDS);

  if (!DATE_RE.test(start) || !DATE_RE.test(end) || !ids.length || !ids.every((id) => ID_RE.test(id))) {
    return new Response(JSON.stringify({ error: "invalid params" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  const result = {};
  await Promise.all(
    ids.map(async (id) => {
      try {
        const apiUrl =
          `https://api.instantly.ai/api/v2/campaigns/analytics/overview` +
          `?id=${encodeURIComponent(id)}&start_date=${start}&end_date=${end}`;
        const r = await fetch(apiUrl, {
          headers: { Authorization: `Bearer ${env.INSTANTLY_API_KEY}` },
        });
        if (!r.ok) {
          result[id] = null;
          return;
        }
        const data = await r.json();
        result[id] = typeof data.link_click_count_unique === "number" ? data.link_click_count_unique : null;
      } catch {
        result[id] = null;
      }
    })
  );

  return new Response(JSON.stringify(result), {
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}
