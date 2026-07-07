// Puts the Horizon dashboard behind a shared username/password (HTTP Basic Auth)
// before serving the static site.
//
// Required secrets (set in Cloudflare, never in this file):
//   BASIC_AUTH_USER
//   BASIC_AUTH_PASS

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/__debug") {
      return new Response(JSON.stringify({
        userLen: (env.BASIC_AUTH_USER || "").length,
        passLen: (env.BASIC_AUTH_PASS || "").length,
        userType: typeof env.BASIC_AUTH_USER,
        passType: typeof env.BASIC_AUTH_PASS,
      }), { headers: { "Content-Type": "application/json" } });
    }

    const expected = "Basic " + btoa(`${env.BASIC_AUTH_USER}:${env.BASIC_AUTH_PASS}`);
    const provided = request.headers.get("Authorization");

    if (provided !== expected) {
      return new Response("Authentication required", {
        status: 401,
        headers: { "WWW-Authenticate": 'Basic realm="Horizon Dashboard"' },
      });
    }

    return env.ASSETS.fetch(request);
  },
};
