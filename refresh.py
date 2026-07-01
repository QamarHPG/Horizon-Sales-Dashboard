# Python Script: Instantly Dashboard Refresher

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
import requests

API_KEY = os.environ.get("INSTANTLY_API_KEY", "")
if not API_KEY:
    print("ERROR: INSTANTLY_API_KEY not set", file=sys.stderr)
    sys.exit(1)

BASE = "https://api.instantly.ai/api/v2"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

CAMPAIGNS = {
    "dental1": {"id": "a4a98af6-b3af-4332-9e14-d59c0a30e860", "name": "Dental Campaign"},
    "hhNew":   {"id": "8b497725-593b-4df8-850c-c9865d048d31", "name": "Home Health Care (New Leads)"},
    "dental2": {"id": "5f24f6fe-9d4c-4562-b979-1f5ad9f88667", "name": "Dental Campaign 2"},
    "hh1to5":  {"id": "07bee31e-9703-4058-9a61-507e2f4840a7", "name": "Home Health Care (1M - 5M)"},
}

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

def fetch_overview(campaign_id):
    return get("/campaigns/analytics/overview", {"id": campaign_id})

def extract_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("result") or data.get("data") or data.get("items") or []
    return []

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
                results.append({
                    "name": f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip(),
                    "email": lead.get("email", ""),
                    "company": lead.get("company_name", ""),
                    "campaignKey": campaign_key,
                    "campaign": campaign_name,
                    "opens": oc,
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

def build_daily_rows(rows):
    out = []
    for r in rows:
        out.append({
            "date": r.get("date", ""),
            "sent": r.get("sent", 0),
            "opens": r.get("opened", 0),
            "clicks": r.get("clicks", 0),
            "replies": r.get("replies", 0),
        })
    return out

def build_step_sends(steps, max_steps):
    by_step = {}
    for s in steps:
        idx = s.get("step")
        if idx is None or str(idx) == "null":
            continue
        by_step[int(idx)] = s.get("sent", 0)
    sends = []
    for i in range(5):
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

def main():
    today = datetime.now(timezone.utc).strftime("%B %-d, %Y")
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    print("Fetching Instantly data...")

    lifetime_totals = {}
    daily_data = {}
    step_sends = {}
    repeat_openers = []
    max_steps_map = {"dental1": 4, "hhNew": 5, "dental2": 4, "hh1to5": 4}

    for key, camp in CAMPAIGNS.items():
        cid = camp["id"]
        cname = camp["name"]
        print(f"  {cname}...")

        overview = fetch_overview(cid)
        lifetime_totals[key] = build_lifetime(overview, cname)

        rows = fetch_daily(cid, days=30)
        daily_data[key] = build_daily_rows(rows)

        steps = fetch_steps(cid)
        step_sends[key] = {
            "maxSteps": max_steps_map[key],
            "sends": build_step_sends(steps, max_steps_map[key]),
        }

        openers = fetch_repeat_openers(key, cid, cname)
        repeat_openers.extend(openers)

    repeat_openers.sort(key=lambda x: x["opens"], reverse=True)

    total_sent = sum(v["sent"] for v in lifetime_totals.values())
    total_opens = sum(v["opens"] for v in lifetime_totals.values())
    total_bounces = sum(v["bounces"] for v in lifetime_totals.values())
    open_rate_pct = pct(total_opens, total_sent)
    bounce_rate_pct = pct(total_bounces, total_sent)

    lt_js = "{\n" + ",\n".join(
        f'    {k}: {{ name: {json.dumps(v["name"])}, sent: {v["sent"]}, contacted: {v["contacted"]}, '
        f'opens: {v["opens"]}, clicks: {v["clicks"]}, replies: {v["replies"]}, bounces: {v["bounces"]} }}'
        for k, v in lifetime_totals.items()
    ) + "\n  }"

    def daily_row_js(r):
        return (f'{{date:{json.dumps(r["date"])},sent:{r["sent"]},'
                f'opens:{r["opens"]},clicks:{r["clicks"]},replies:{r["replies"]}}}')

    dd_js = "{\n" + ",\n".join(
        f'    {k}: [\n      ' + ",".join(daily_row_js(r) for r in rows) + '\n    ]'
        for k, rows in daily_data.items()
    ) + "\n  }"

    def opener_js(o):
        return (f'{{ name: {json.dumps(o["name"])}, email: {json.dumps(o["email"])}, '
                f'company: {json.dumps(o["company"])}, campaignKey: {json.dumps(o["campaignKey"])}, '
                f'campaign: {json.dumps(o["campaign"])}, opens: {o["opens"]}, '
                f'replies: {o["replies"]}, lastOpen: {json.dumps(o["lastOpen"])} }}')

    ro_js = "[\n    " + ",\n    ".join(opener_js(o) for o in repeat_openers) + "\n  ]"

    def step_js(key, v):
        sends_str = ", ".join("null" if x is None else str(x) for x in v["sends"])
        return f'{key}: {{ maxSteps: {v["maxSteps"]}, sends: [{sends_str}] }}'

    ss_js = "{\n    " + ",\n    ".join(step_js(k, v) for k, v in step_sends.items()) + "\n  }"

    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()

    def replace_block(html, var_name, new_value, end_marker="};"):
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

    html = re.sub(r'v\d+ —', f'v{today_iso} —', html)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Done. {len(repeat_openers)} repeat openers, open rate {open_rate_pct}%, bounce rate {bounce_rate_pct}%")


if __name__ == "__main__":
    main()
