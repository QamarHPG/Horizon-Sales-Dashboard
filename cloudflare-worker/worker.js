// Cloudflare Worker: refresh-button proxy for the Horizon dashboard.
// The dashboard's "Refresh now" button POSTs here; this Worker holds the
// GitHub token server-side and triggers the refresh-dashboard workflow.
//
// Required secret (set in Cloudflare, never in this file):
//   GITHUB_TOKEN — a GitHub PAT with permission to dispatch Actions on the repo.

const GITHUB_REPO = "qmrabs-design/Horizon-Sales-Dashboard";
const WORKFLOW_FILE = "refresh-dashboard.yml";
const ALLOWED_ORIGINS = [
  "https://qmrabs-design.github.io",
];

function corsHeaders(origin) {
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const allowed = ALLOWED_ORIGINS.includes(origin);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: allowed ? corsHeaders(origin) : {} });
    }
    if (!allowed) {
      return new Response("Forbidden", { status: 403 });
    }
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405, headers: corsHeaders(origin) });
    }

    const gh = await fetch(
      `https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "Accept": "application/vnd.github+json",
          "User-Agent": "horizon-dashboard-refresh-worker",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        body: JSON.stringify({ ref: "main" }),
      }
    );

    // GitHub returns 204 No Content on a successful dispatch.
    if (gh.status === 204) {
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json", ...corsHeaders(origin) },
      });
    }

    const detail = (await gh.text()).slice(0, 300);
    return new Response(JSON.stringify({ ok: false, status: gh.status, detail }), {
      status: 502,
      headers: { "Content-Type": "application/json", ...corsHeaders(origin) },
    });
  },
};
