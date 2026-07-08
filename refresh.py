# Python Script: Instantly Dashboard Refresher

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

# The dashboard advertises "Times in Eastern (ET)". Compute "today" in ET,
# not UTC — after 8 PM ET the UTC date has already rolled to tomorrow, which
# made the Today filter point at a day with no sends yet.
ET = ZoneInfo("America/New_York")
import requests

API_KEY = os.environ.get("INSTANTLY_API_KEY", "")
if not API_KEY:
    print("ERROR: INSTANTLY_API_KEY not set", file=sys.stderr)
    sys.exit(1)

BASE = "https://api.instantly.ai/api/v2"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# Campaigns are no longer hardcoded here — they're auto-discovered from the
# Instantly API on every run (see fetch_all_campaigns/classify_status below),
# so a new campaign just needs to exist in Instantly; pressing refresh picks
# it up with no code changes.

def get(path, params=None):
    url = f"{BASE}{path}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    if not r.ok:
        print(f"  HTTP {r.status_code} for {r.url}: {r.text[:300]}", file=sys.stderr)
    r.raise_for_status()
    return r.json()

def post(path, body=None):
    url = f"{BASE}{path}"
    r = requests.post(url, headers=HEADERS, json=body or {}, timeout=30)
    if not r.ok:
        print(f"  HTTP {r.status_code} for {url}: {r.text[:300]}", file=sys.stderr)
    r.raise_for_status()
    return r.json()

def extract_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("result") or data.get("data") or data.get("items") or []
    return []

# Instantly campaign status codes: 0=draft, 1=active, 2=paused, 3=completed.
# Anything else (e.g. -1, seen on an AI SDR campaign type) is non-standard
# and excluded. Drafts are excluded too — they've never sent anything.
def classify_status(status):
    return {1: "active", 2: "paused", 3: "completed"}.get(status)

def sequence_step_count(campaign):
    seqs = campaign.get("sequences") or []
    if seqs:
        return len(seqs[0].get("steps") or [])
    return 0

def fetch_all_campaigns():
    results = []
    cursor = None
    while True:
        params = {"limit": 100}
        if cursor:
            params["starting_after"] = cursor
        data = get("/campaigns", params)
        items = data.get("items", [])
        results.extend(items)
        cursor = data.get("next_starting_after")
        if not cursor or not items:
            break
    return results

def fetch_overview(campaign_id):
    return get("/campaigns/analytics/overview", {"id": campaign_id})

def fetch_daily(campaign_id, days=30):
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    data = get("/campaigns/analytics/daily", {
        "campaign_id": campaign_id,
        "start_date": str(start),
        "end_date": str(end),
    })
    return extract_list(data)

def fetch_steps(campaign_id):
    data = get("/campaigns/analytics/steps", {"campaign_id": campaign_id})
    return extract_list(data)

def fetch_repeat_openers(campaign_key, campaign_id, campaign_name):
    results = []
    cursor = None
    while True:
        body = {"campaign": campaign_id, "limit": 100}
        if cursor:
            body["starting_after"] = cursor
        data = post("/leads/list", body)  # V2 API uses POST /leads/list, not GET /leads
        items = data.get("items", [])
        for lead in items:
            oc = lead.get("email_open_count") or 0
            if oc >= 2:
                # status_summary.lastStep.stepID is "seq_step_variant" with a
                # 0-based step index, so step index + 1 = emails sent to this
                # lead. 0 means unknown (no step info on the lead record).
                step_id = ((lead.get("status_summary") or {}).get("lastStep") or {}).get("stepID", "")
                try:
                    emails_sent = int(step_id.split("_")[1]) + 1
                except (IndexError, ValueError):
                    emails_sent = 0
                results.append({
                    "name": f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip(),
                    "email": lead.get("email", ""),
                    "company": lead.get("company_name", ""),
                    "campaignKey": campaign_key,
                    "campaign": campaign_name,
                    "opens": oc,
                    "emailsSent": emails_sent,
                    "replies": lead.get("email_reply_count") or 0,
                    "lastOpen": lead.get("timestamp_last_touch") or "",
                })
        cursor = data.get("next_starting_after")
        if not cursor or not items:
            break
    return results

def build_lifetime(overview, campaign_name):
    return {
        "name": campaign_name,
        "sent": overview.get("emails_sent_count", 0),
        "contacted": overview.get("contacted_count", 0),
        "opens": overview.get("open_count", 0),
        "clicks": overview.get("link_click_count", 0),
        "replies": overview.get("reply_count", 0),
        "bounces": overview.get("bounced_count", 0),
    }

def build_daily_rows(rows, today_iso):
    out = []
    for r in rows:
        out.append({
            "date": r.get("date", ""),
            "sent": r.get("sent", 0),
            "opens": r.get("opened", 0),
            "clicks": r.get("clicks", 0),
            "replies": r.get("replies", 0),
        })
    # Instantly buckets days by UTC. Between 8 PM ET and midnight ET the
    # current UTC day is already "tomorrow", so activity from this evening
    # lands in a bucket dated past the ET today. Fold those buckets into
    # today's row so the Today filter counts the full ET calendar day.
    future = [r for r in out if r["date"] > today_iso]
    if future:
        out = [r for r in out if r["date"] <= today_iso]
        today_row = next((r for r in out if r["date"] == today_iso), None)
        if today_row is None:
            today_row = {"date": today_iso, "sent": 0, "opens": 0, "clicks": 0, "replies": 0}
            out.append(today_row)
        for r in future:
            for k in ("sent", "opens", "clicks", "replies"):
                today_row[k] += r[k]
    return out

def build_step_sends(steps, max_steps, total_rows):
    by_step = {}
    for s in steps:
        idx = s.get("step")
        if idx is None or str(idx) == "null":
            continue
        by_step[int(idx)] = s.get("sent", 0)
    sends = []
    for i in range(total_rows):
        if i >= max_steps:
            sends.append(None)
        else:
            sends.append(by_step.get(i, 0))
    return sends

def js_val(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(v)

def build_js_object(d):
    parts = []
    for k, v in d.items():
        if isinstance(v, list):
            inner = ", ".join(js_val(x) for x in v)
            parts.append(f"sends: [{inner}]")
        elif isinstance(v, dict):
            parts.append(f"{k}: {build_js_object(v)}")
        else:
            parts.append(f"{k}: {js_val(v)}")
    return "{ " + ", ".join(parts) + " }"

def pct(n, d):
    return f"{(n / d * 100):.1f}" if d else "0.0"

def build_email_summary(daily_data, campaigns, today_iso, today_label):
    rows = []
    total = {"sent": 0, "opens": 0, "clicks": 0, "replies": 0}
    for key, camp in campaigns.items():
        today_row = next((r for r in daily_data[key] if r["date"] == today_iso), None)
        stats = today_row or {"sent": 0, "opens": 0, "clicks": 0, "replies": 0}
        rows.append((camp["name"], stats["sent"], stats["opens"], stats["clicks"], stats["replies"]))
        for k in total:
            total[k] += stats[k]

    def cells(name, sent, opens, clicks, replies):
        return (f"<td style='padding:4px 12px;'>{name}</td>"
                f"<td style='padding:4px 12px;text-align:right;'>{sent}</td>"
                f"<td style='padding:4px 12px;text-align:right;'>{opens}</td>"
                f"<td style='padding:4px 12px;text-align:right;'>{clicks}</td>"
                f"<td style='padding:4px 12px;text-align:right;'>{replies}</td>")

    body_rows = "\n".join(f"<tr>{cells(*r)}</tr>" for r in rows)
    total_row = (f"<tr style='font-weight:bold;border-top:2px solid #ccc;'>"
                 f"{cells('Total', total['sent'], total['opens'], total['clicks'], total['replies'])}</tr>")

    html = f"""<div style="font-family:Arial,sans-serif;">
  <h2 style="margin-bottom:4px;">Horizon Path Group — Daily Dashboard Summary</h2>
  <p style="color:#555;margin-top:0;">{today_label}</p>
  <table style="border-collapse:collapse;font-size:14px;">
    <thead>
      <tr style="background:#f0f0f0;">
        <th style="padding:4px 12px;text-align:left;">Campaign</th>
        <th style="padding:4px 12px;text-align:right;">Sent</th>
        <th style="padding:4px 12px;text-align:right;">Opens</th>
        <th style="padding:4px 12px;text-align:right;">Clicks</th>
        <th style="padding:4px 12px;text-align:right;">Replies</th>
      </tr>
    </thead>
    <tbody>
      {body_rows}
      {total_row}
    </tbody>
  </table>
  <p style="margin-top:16px;">
    <a href="https://horizon-sales-dashboard.qamar-76f.workers.dev">View full dashboard &rarr;</a>
  </p>
</div>
"""
    with open("email_summary.html", "w", encoding="utf-8") as f:
        f.write(html)

def main():
    today = datetime.now(ET).strftime("%B %-d, %Y")
    today_iso = datetime.now(ET).strftime("%Y-%m-%d")

    print("Discovering campaigns from Instantly...")
    raw_campaigns = fetch_all_campaigns()

    active_campaigns = {}    # id -> {"name":..., "maxSteps": int}
    tracked_campaigns = {}   # id -> {"name":..., "status": "active"|"paused"|"completed"} (all non-draft)

    for c in raw_campaigns:
        status_label = classify_status(c.get("status"))
        if status_label is None:
            continue  # skip drafts and any non-standard status
        cid = c["id"]
        cname = c.get("name", "Untitled Campaign")
        tracked_campaigns[cid] = {"name": cname, "status": status_label}
        if status_label == "active":
            active_campaigns[cid] = {"name": cname, "maxSteps": sequence_step_count(c) or 1}

    # All active campaigns' step tables share one row count so no campaign's
    # later steps get truncated by another campaign's shorter sequence.
    overall_max_steps = max((v["maxSteps"] for v in active_campaigns.values()), default=1)

    print(f"  {len(active_campaigns)} active, "
          f"{len(tracked_campaigns) - len(active_campaigns)} paused/completed tracked for lifetime totals.")

    lifetime_totals = {}
    daily_data = {}
    step_sends = {}
    repeat_openers = []
    all_campaigns_lifetime = {}

    for cid, info in tracked_campaigns.items():
        cname = info["name"]
        print(f"  {cname} ({info['status']})...")

        overview = fetch_overview(cid)
        lt = build_lifetime(overview, cname)

        all_campaigns_lifetime[cid] = {
            "name": cname,
            "status": info["status"],
            "sent": lt["sent"],
            "opens": lt["opens"],
            "replies": lt["replies"],
            "bounces": lt["bounces"],
        }

        if cid not in active_campaigns:
            continue  # paused/completed: lifetime totals only, no daily/step/opener detail

        lifetime_totals[cid] = lt

        rows = fetch_daily(cid, days=30)
        daily_data[cid] = build_daily_rows(rows, today_iso)

        max_steps = active_campaigns[cid]["maxSteps"]
        steps = fetch_steps(cid)
        step_sends[cid] = {
            "name": cname,
            "maxSteps": max_steps,
            "sends": build_step_sends(steps, max_steps, overall_max_steps),
        }

        openers = fetch_repeat_openers(cid, cid, cname)
        repeat_openers.extend(openers)

    repeat_openers.sort(key=lambda x: x["opens"], reverse=True)

    build_email_summary(daily_data, active_campaigns, today_iso, today)

    # "Your rate" stats below stay scoped to active campaigns (matches what
    # the benchmark section already claims to measure).
    total_sent = sum(v["sent"] for v in lifetime_totals.values())
    total_opens = sum(v["opens"] for v in lifetime_totals.values())
    total_bounces = sum(v["bounces"] for v in lifetime_totals.values())
    open_rate_pct = pct(total_opens, total_sent)
    bounce_rate_pct = pct(total_bounces, total_sent)

    lt_js = "{\n" + ",\n".join(
        f'    {json.dumps(k)}: {{ name: {json.dumps(v["name"])}, sent: {v["sent"]}, contacted: {v["contacted"]}, '
        f'opens: {v["opens"]}, clicks: {v["clicks"]}, replies: {v["replies"]}, bounces: {v["bounces"]} }}'
        for k, v in lifetime_totals.items()
    ) + "\n  }"

    def daily_row_js(r):
        return (f'{{date:{json.dumps(r["date"])},sent:{r["sent"]},'
                f'opens:{r["opens"]},clicks:{r["clicks"]},replies:{r["replies"]}}}')

    dd_js = "{\n" + ",\n".join(
        f'    {json.dumps(k)}: [\n      ' + ",".join(daily_row_js(r) for r in rows) + '\n    ]'
        for k, rows in daily_data.items()
    ) + "\n  }"

    def opener_js(o):
        return (f'{{ name: {json.dumps(o["name"])}, email: {json.dumps(o["email"])}, '
                f'company: {json.dumps(o["company"])}, campaignKey: {json.dumps(o["campaignKey"])}, '
                f'campaign: {json.dumps(o["campaign"])}, opens: {o["opens"]}, '
                f'emailsSent: {o["emailsSent"]}, '
                f'replies: {o["replies"]}, lastOpen: {json.dumps(o["lastOpen"])} }}')

    ro_js = "[\n    " + ",\n    ".join(opener_js(o) for o in repeat_openers) + "\n  ]"

    def step_js(key, v):
        sends_str = ", ".join("null" if x is None else str(x) for x in v["sends"])
        return f'{json.dumps(key)}: {{ name: {json.dumps(v["name"])}, maxSteps: {v["maxSteps"]}, sends: [{sends_str}] }}'

    ss_js = "{\n    " + ",\n    ".join(step_js(k, v) for k, v in step_sends.items()) + "\n  }"

    def acl_js_entry(cid, v):
        return (f'{json.dumps(cid)}: {{ name: {json.dumps(v["name"])}, status: {json.dumps(v["status"])}, '
                f'sent: {v["sent"]}, opens: {v["opens"]}, replies: {v["replies"]}, bounces: {v["bounces"]} }}')

    acl_js = "{\n    " + ",\n    ".join(acl_js_entry(cid, v) for cid, v in all_campaigns_lifetime.items()) + "\n  }"

    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()

    def replace_block(html, var_name, new_value):
        pattern = rf'(const {re.escape(var_name)}\s*=\s*)(\{{[\s\S]*?\n  \}};)'
        replacement = rf'\g<1>{new_value};'
        result = re.sub(pattern, replacement, html)
        if result == html:
            print(f"  WARNING: could not find {var_name} block to replace", file=sys.stderr)
        return result

    def replace_array_block(html, var_name, new_value):
        pattern = rf'(const {re.escape(var_name)}\s*=\s*)(\[[\s\S]*?\n  \];)'
        replacement = rf'\g<1>{new_value};'
        result = re.sub(pattern, replacement, html)
        if result == html:
            print(f"  WARNING: could not find {var_name} array to replace", file=sys.stderr)
        return result

    html = replace_block(html, "lifetimeTotals", lt_js)
    html = replace_block(html, "dailyData", dd_js)
    html = replace_array_block(html, "repeatOpeners", ro_js)
    html = replace_block(html, "stepSends", ss_js)
    html = replace_block(html, "allCampaignsLifetime", acl_js)

    html = re.sub(
        r'Pulled \w+ \d+, \d{4}',
        f'Pulled {today}',
        html
    )

    html = re.sub(
        r'const TODAY = new Date\("[^"]+"\);',
        f'const TODAY = new Date("{today_iso}T23:59:59Z");',
        html
    )

    html = re.sub(
        r'(<td>Open rate</td>\s*<td class="num">)([\d.]+%)',
        rf'\g<1>{open_rate_pct}%',
        html
    )

    bounce_badge_class = "bad" if float(bounce_rate_pct) >= 5 else "good"
    html = re.sub(
        r'(<td>Bounce rate</td>\s*<td class="num">)<span class="badge (?:good|bad)">[\d.]+%</span>',
        rf'\g<1><span class="badge {bounce_badge_class}">{bounce_rate_pct}%</span>',
        html
    )

    html = re.sub(
        r'(<span id="activeCampaignCount">)\d+(</span>)',
        rf'\g<1>{len(active_campaigns)}\g<2>',
        html
    )

    html = re.sub(r'v\d+ —', f'v{today_iso} —', html)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Done. {len(active_campaigns)} active campaigns, {len(all_campaigns_lifetime)} total tracked "
          f"(incl. paused/completed). {len(repeat_openers)} repeat openers, "
          f"open rate {open_rate_pct}%, bounce rate {bounce_rate_pct}%")


if __name__ == "__main__":
    main()
