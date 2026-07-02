# Refresh-button Worker

A tiny Cloudflare Worker that lets the dashboard's **Refresh now** button trigger
the `refresh-dashboard.yml` GitHub Action without exposing a GitHub token in the
public HTML. The button POSTs to this Worker; the Worker holds the token as a
secret and calls GitHub's workflow-dispatch API.

## Deploy (one-time, ~5 minutes)

### Step 1 — Create a GitHub token

Either reuse your existing PAT (it already has `repo` + `workflow` scopes), or
create a safer fine-grained one:

1. GitHub → Settings → Developer settings → **Fine-grained personal access tokens** → Generate new token
2. Repository access: **Only select repositories** → `Horizon-Sales-Dashboard`
3. Permissions → Repository permissions → **Actions: Read and write**
4. Generate and copy the token

### Step 2 — Create the Worker (dashboard method, no CLI needed)

1. [dash.cloudflare.com](https://dash.cloudflare.com) → **Workers & Pages** → **Create** → **Create Worker**
2. Name it `horizon-dashboard-refresh` → **Deploy** (deploys the hello-world stub)
3. **Edit code** → replace everything with the contents of `worker.js` → **Deploy**
4. Worker → **Settings** → **Variables and Secrets** → **Add**:
   - Type: **Secret**, Name: `GITHUB_TOKEN`, Value: the token from Step 1
5. Copy the Worker URL, e.g. `https://horizon-dashboard-refresh.<your-subdomain>.workers.dev`

*(Alternative: `npx wrangler deploy` from this folder, then
`npx wrangler secret put GITHUB_TOKEN`.)*

### Step 3 — Point the dashboard at the Worker

In `index.html`, set:

```js
const REFRESH_WORKER_URL = "https://horizon-dashboard-refresh.<your-subdomain>.workers.dev";
```

Commit and push. The **Refresh now** button appears in the dashboard header
automatically once the URL is non-empty.

## How it works

- Only requests with `Origin: https://qmrabs-design.github.io` are accepted
  (CORS-enforced); everything else gets 403.
- On POST, the Worker calls
  `POST /repos/qmrabs-design/Horizon-Sales-Dashboard/actions/workflows/refresh-dashboard.yml/dispatches`
  with `{"ref":"main"}`.
- The dashboard shows a 3-minute countdown (workflow run + GitHub Pages
  redeploy), then reloads itself with a cache-busting query string.
