#!/usr/bin/env python3
"""
Daily job scanner: pulls openings posted in the last ~24h from company ATS
boards (Greenhouse/Lever) and free job APIs, scores them against your resume
profile, publishes an HTML report (GitHub Pages) and emails you the ranked list.

Free and dependency-light: requests + PyYAML only.
"""
import hashlib
import html as htmllib
import json
import os
import re
import smtplib
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime

import requests
import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (job-scanner; personal use)"}
NOW = datetime.now(timezone.utc)

def load_yaml(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return yaml.safe_load(f)

CFG = load_yaml("config.yaml")
PROFILE = load_yaml("profile.yaml")
LOOKBACK = timedelta(hours=CFG.get("lookback_hours", 26))

def get(url, params=None, tries=2):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=25)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
    return None

def strip_html(s):
    return re.sub(r"<[^>]+>", " ", htmllib.unescape(s or "")).lower()

def parse_dt(val):
    """Best-effort -> aware UTC datetime."""
    if val is None:
        return None
    try:
        if isinstance(val, (int, float)):
            if val > 1e12:
                val /= 1000.0
            return datetime.fromtimestamp(val, tz=timezone.utc)
        s = str(val).strip()
        if re.fullmatch(r"\d{10,13}", s):
            return parse_dt(int(s))
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def job(source, company, title, url, location, description, posted_at):
    return {
        "source": source, "company": (company or "?").strip(),
        "title": (title or "").strip(), "url": url or "",
        "location": (location or "").strip(),
        "description": (description or "")[:20000],
        "posted_at": posted_at,
    }

# ---------------- sources ----------------

def src_greenhouse(token):
    r = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", {"content": "true"})
    if not r:
        return []
    out = []
    for j in r.json().get("jobs", []):
        dt = parse_dt(j.get("first_published") or j.get("updated_at"))
        out.append(job("greenhouse", (j.get("company_name") or token), j.get("title"),
                       j.get("absolute_url"), (j.get("location") or {}).get("name", ""),
                       strip_html(j.get("content", "")), dt))
    return out

def src_lever(handle):
    r = get(f"https://api.lever.co/v0/postings/{handle}", {"mode": "json"})
    if not r:
        return []
    out = []
    try:
        data = r.json()
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    for j in data:
        cat = j.get("categories") or {}
        out.append(job("lever", handle, j.get("text"), j.get("hostedUrl"),
                       cat.get("location", ""), (j.get("descriptionPlain") or "").lower(),
                       parse_dt(j.get("createdAt"))))
    return out


def src_ashby(org):
    r = get(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
    if not r:
        return []
    out = []
    try:
        items = r.json().get("jobs", [])
    except ValueError:
        return []
    for j in items:
        if j.get("isListed") is False:
            continue
        out.append(job("ashby", org, j.get("title"), j.get("jobUrl"),
                       j.get("location", ""), (j.get("descriptionPlain") or "").lower(),
                       parse_dt(j.get("publishedAt"))))
    return out

def src_remoteok():
    r = get("https://remoteok.com/api")
    if not r:
        return []
    out = []
    try:
        items = r.json()
    except ValueError:
        return []
    for j in items:
        if not isinstance(j, dict) or not j.get("position"):
            continue
        out.append(job("remoteok", j.get("company"), j.get("position"),
                       j.get("url"), j.get("location") or "Remote",
                       strip_html(j.get("description", "")) + " " + " ".join(j.get("tags") or []),
                       parse_dt(j.get("date"))))
    return out

def src_remotive():
    out = []
    for q in CFG.get("remotive_searches", []):
        r = get("https://remotive.com/api/remote-jobs", {"search": q, "limit": 100})
        if not r:
            continue
        for j in r.json().get("jobs", []):
            out.append(job("remotive", j.get("company_name"), j.get("title"),
                           j.get("url"), j.get("candidate_required_location", "Remote"),
                           strip_html(j.get("description", "")), parse_dt(j.get("publication_date"))))
    return out

def src_arbeitnow():
    r = get("https://www.arbeitnow.com/api/job-board-api")
    if not r:
        return []
    out = []
    for j in r.json().get("data", []):
        out.append(job("arbeitnow", j.get("company_name"), j.get("title"),
                       j.get("url"), j.get("location", ""),
                       strip_html(j.get("description", "")) + " " + " ".join(j.get("tags") or []),
                       parse_dt(j.get("created_at"))))
    return out

def src_himalayas():
    r = get("https://himalayas.app/jobs/api", {"limit": 100})
    if not r:
        return []
    out = []
    try:
        items = r.json().get("jobs", [])
    except ValueError:
        return []
    for j in items:
        out.append(job("himalayas", j.get("companyName"), j.get("title"),
                       j.get("applicationLink") or j.get("guid"),
                       ", ".join(j.get("locationRestrictions") or []) or "Remote",
                       strip_html(j.get("description") or j.get("excerpt") or ""),
                       parse_dt(j.get("pubDate"))))
    return out

def src_weworkremotely():
    out = []
    for feed in ["https://weworkremotely.com/categories/remote-programming-jobs.rss",
                 "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss"]:
        r = get(feed)
        if not r:
            continue
        try:
            root = ET.fromstring(r.content)
        except ET.ParseError:
            continue
        for item in root.iter("item"):
            t = (item.findtext("title") or "").split(":", 1)
            company, title = (t[0], t[1]) if len(t) == 2 else ("?", t[0])
            out.append(job("weworkremotely", company, title, item.findtext("link"),
                           "Remote", strip_html(item.findtext("description") or ""),
                           parse_dt(item.findtext("pubDate"))))
    return out

def src_adzuna():
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    az = CFG.get("adzuna", {})
    if not (az.get("enabled") and app_id and app_key):
        return []
    out = []
    for q in az.get("queries", []):
        r = get(f"https://api.adzuna.com/v1/api/jobs/{az.get('country','us')}/search/1",
                {"app_id": app_id, "app_key": app_key, "what": q,
                 "max_days_old": 1, "results_per_page": 50, "content-type": "application/json"})
        if not r:
            continue
        for j in r.json().get("results", []):
            out.append(job("adzuna", (j.get("company") or {}).get("display_name"),
                           j.get("title"), j.get("redirect_url"),
                           (j.get("location") or {}).get("display_name", ""),
                           strip_html(j.get("description", "")), parse_dt(j.get("created"))))
    return out

# ---------------- filtering & scoring ----------------

def relevant(j):
    if not j["url"] or not j["title"] or j["posted_at"] is None:
        return False
    if NOW - j["posted_at"] > LOOKBACK:
        return False
    tl, dl = j["title"].lower(), j["description"].lower()
    if any(x in tl for x in CFG.get("exclude_title_keywords", [])):
        return False
    must = CFG.get("title_must_contain", [])
    if must and not any(k in tl for k in must):
        return False
    return any(k in tl or k in dl for k in CFG.get("include_keywords", []))

def kw_pattern(kw):
    e = re.escape(kw.lower())
    # word boundaries, but c/c++ need care
    return re.compile(r"(?<![a-z0-9+#])" + e + r"(?![a-z0-9+#])")

SKILL_PATTERNS = {k.lower(): (kw_pattern(k), w) for k, w in (PROFILE.get("skills") or {}).items()}

def score(j):
    tl, dl = j["title"].lower(), j["description"].lower()
    # skill fit
    total_w = sum(w for _, w in SKILL_PATTERNS.values()) or 1
    hit_w = sum(w * (3 if p.search(tl) else (1 if p.search(dl) else 0)) / 3
                for p, w in SKILL_PATTERNS.values())
    skill_score = hit_w / total_w  # 0..1-ish
    # title fit: best overlap with target titles
    title_score = 0.0
    twords = set(re.findall(r"[a-z++]+", tl))
    for t in PROFILE.get("target_titles", []):
        need = set(t.lower().split())
        title_score = max(title_score, len(need & twords) / len(need))
    fit = min(99, round(100 * (0.55 * min(skill_score * 2.2, 1.0) + 0.45 * title_score)))

    # chance of landing it (heuristic)
    years = PROFILE.get("years_experience", 3)
    chance = fit
    reasons = []
    if re.search(r"\b(staff|principal)\b", tl) and years < 8:
        chance -= 25; reasons.append("staff/principal-level title")
    elif re.search(r"\b(senior|lead)\b", tl) and years < 4:
        chance -= 15; reasons.append("senior-level title")
    m = re.search(r"(\d{1,2})\s*\+?\s*(?:or more\s*)?years", dl)
    if m and int(m.group(1)) > years:
        gap = int(m.group(1)) - years
        chance -= min(30, gap * 6); reasons.append(f"asks {m.group(1)}+ yrs exp")
    if re.search(r"(security clearance|ts/sci|secret clearance)", dl) and not PROFILE.get("has_security_clearance"):
        chance -= 30; reasons.append("clearance required")
    if re.search(r"\bphd\b.{0,40}(required|must)", dl):
        chance -= 20; reasons.append("PhD required")
    if re.search(r"\b(junior|entry.level|new grad|early career|associate)\b", tl) and years <= 4:
        chance += 10; reasons.append("entry-friendly")
    if NOW - j["posted_at"] < timedelta(hours=12):
        chance += 5; reasons.append("very fresh posting")
    j["fit"] = fit
    j["chance"] = max(1, min(99, chance))
    j["rank"] = round(0.6 * j["fit"] + 0.4 * j["chance"], 1)
    j["notes"] = "; ".join(reasons)
    return j

# ---------------- state / dedupe ----------------

SEEN_PATH = os.path.join(ROOT, "data", "seen_jobs.json")

def load_seen():
    try:
        with open(SEEN_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_seen(seen):
    cutoff = (NOW - timedelta(days=45)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    os.makedirs(os.path.dirname(SEEN_PATH), exist_ok=True)
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=0)

def jid(j):
    return hashlib.sha1(j["url"].encode()).hexdigest()[:16]

# ---------------- outputs ----------------

def build_rows(jobs):
    rows = []
    for j in jobs:
        age_h = int((NOW - j["posted_at"]).total_seconds() // 3600)
        rows.append(
            f"<tr><td>{htmllib.escape(j['company'])}</td>"
            f"<td><a href='{htmllib.escape(j['url'])}' target='_blank'>{htmllib.escape(j['title'])}</a></td>"
            f"<td>{htmllib.escape(j['location'][:60])}</td>"
            f"<td class='fit'>{j['fit']}%</td><td class='ch'>{j['chance']}%</td>"
            f"<td>{age_h}h ago</td><td class='src'>{j['source']}</td>"
            f"<td class='notes'>{htmllib.escape(j['notes'])}</td></tr>")
    return "\n".join(rows)

def write_report(jobs, stats):
    ts = NOW.strftime("%Y-%m-%d %H:%M UTC")
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Roopesh's Daily Job Scan</title><style>
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:24px;background:#0f172a;color:#e2e8f0}}
h1{{font-size:22px}} .sub{{color:#94a3b8;font-size:13px;margin-bottom:18px}}
table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{padding:8px 10px;border-bottom:1px solid #1e293b;text-align:left;vertical-align:top}}
th{{color:#7dd3fc;cursor:pointer;position:sticky;top:0;background:#0f172a}}
a{{color:#7dd3fc;text-decoration:none}} a:hover{{text-decoration:underline}}
.fit{{color:#4ade80;font-weight:600}} .ch{{color:#facc15;font-weight:600}}
.src{{color:#94a3b8;font-size:12px}} .notes{{color:#94a3b8;font-size:12px;max-width:220px}}
tr:hover{{background:#1e293b}}
</style></head><body>
<h1>Daily Job Scan &mdash; openings from the last 24h</h1>
<div class="sub">Generated {ts} &middot; {len(jobs)} matches &middot; {stats}
&middot; sorted by overall rank (0.6&times;fit + 0.4&times;chance) &middot; click headers to sort</div>
<table id="t"><thead><tr><th>Company</th><th>Role</th><th>Location</th><th>Fit</th>
<th>Chance</th><th>Posted</th><th>Source</th><th>Notes</th></tr></thead>
<tbody>{build_rows(jobs)}</tbody></table>
<script>
document.querySelectorAll('th').forEach((th,i)=>th.onclick=()=>{{
const tb=document.querySelector('#t tbody');const rows=[...tb.rows];
const num=r=>parseFloat(r.cells[i].innerText)||0;const txt=r=>r.cells[i].innerText;
const isNum=[3,4,5].includes(i);
rows.sort((a,b)=>isNum?num(b)-num(a):txt(a).localeCompare(txt(b)));
rows.forEach(r=>tb.appendChild(r));}});
</script></body></html>"""
    os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
    with open(os.path.join(ROOT, "docs", "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    with open(os.path.join(ROOT, "data", "latest.json"), "w", encoding="utf-8") as f:
        json.dump([{k: (v.isoformat() if isinstance(v, datetime) else v)
                    for k, v in j.items() if k != "description"} for j in jobs], f, indent=1)


def airtable_existing(url, hdr):
    """Fetch existing rows so we never insert a job that's already tracked."""
    keys = set()
    offset = None
    while True:
        params = {"pageSize": 100, "fields[]": ["Source Link", "Company", "Role Title"]}
        if offset:
            params["offset"] = offset
        r = requests.get(url, headers=hdr, params=params, timeout=30)
        if r.status_code != 200:
            print(f"Airtable read error {r.status_code}; skipping dedupe this run.")
            return keys
        d = r.json()
        for rec in d.get("records", []):
            f = rec.get("fields", {})
            if f.get("Source Link"):
                keys.add(f["Source Link"].strip().lower().rstrip("/"))
            if f.get("Company") and f.get("Role Title"):
                keys.add((f["Company"].strip().lower(), f["Role Title"].strip().lower()))
        offset = d.get("offset")
        if not offset:
            return keys


def sync_airtable(jobs):
    token = os.environ.get("AIRTABLE_TOKEN")
    at = CFG.get("airtable", {})
    if not (token and at.get("enabled") and jobs):
        if not token:
            print("Airtable skipped: AIRTABLE_TOKEN not set.")
        return
    url = f"https://api.airtable.com/v0/{at['base_id']}/{at['table']}"
    hdr = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    existing = airtable_existing(url, hdr)
    before = len(jobs)
    jobs = [j for j in jobs
            if j["url"].strip().lower().rstrip("/") not in existing
            and (j["company"].strip().lower(), j["title"].strip().lower()) not in existing]
    if before - len(jobs):
        print(f"Airtable: {before - len(jobs)} already tracked, skipping those.")
    sent = 0
    for i in range(0, len(jobs), 10):
        recs = [{"fields": {
            "Role Title": j["title"][:255],
            "Company": j["company"][:255],
            "Date Found": NOW.strftime("%Y-%m-%d"),
            "Location": j["location"][:255],
            "Source Link": j["url"],
            "Fit %": j["fit"],
            "Chance %": j["chance"],
            "Status": at.get("new_status", "New"),
            "Notes": (f"rank {j['rank']} | source: {j['source']}"
                      + (f" | {j['notes']}" if j['notes'] else "")),
        }} for j in jobs[i:i+10]]
        try:
            r = requests.post(url, headers=hdr, json={"records": recs, "typecast": True}, timeout=30)
            if r.status_code == 200:
                sent += len(recs)
            else:
                print(f"Airtable error {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            print(f"Airtable error: {e}")
    print(f"Airtable: {sent} records added.")

def send_email(jobs):
    # Works with Gmail (GMAIL_ADDRESS/GMAIL_APP_PASSWORD) or any SMTP service
    # like Brevo (SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/EMAIL_FROM).
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ.get("SMTP_USER") or os.environ.get("GMAIL_ADDRESS")
    pw = os.environ.get("SMTP_PASSWORD") or os.environ.get("GMAIL_APP_PASSWORD")
    addr = os.environ.get("EMAIL_FROM") or os.environ.get("GMAIL_ADDRESS") or user
    to = os.environ.get("EMAIL_TO") or CFG["email"]["to"]
    if not (user and pw):
        print("Email skipped: no SMTP credentials set.")
        return
    top = jobs[:CFG["email"].get("max_jobs_in_email", 40)]
    rows = "".join(
        f"<tr><td style='padding:6px 10px'>{htmllib.escape(j['company'])}</td>"
        f"<td style='padding:6px 10px'><a href='{htmllib.escape(j['url'])}'>{htmllib.escape(j['title'])}</a></td>"
        f"<td style='padding:6px 10px'>{j['fit']}%</td><td style='padding:6px 10px'>{j['chance']}%</td>"
        f"<td style='padding:6px 10px;color:#666;font-size:12px'>{htmllib.escape(j['notes'])}</td></tr>"
        for j in top)
    body = f"""<html><body style="font-family:sans-serif">
<h2>{len(jobs)} new matching jobs (last 24h)</h2>
<p>Ranked by fit &amp; estimated chance. Full sortable list:
<a href="{os.environ.get('PAGES_URL','(enable GitHub Pages for web link)')}">web report</a></p>
<table border="0" cellspacing="0" style="border-collapse:collapse;font-size:14px">
<tr style="background:#f1f5f9"><th style="padding:6px 10px;text-align:left">Company</th>
<th style="padding:6px 10px;text-align:left">Role</th><th style="padding:6px 10px">Fit</th>
<th style="padding:6px 10px">Chance</th><th style="padding:6px 10px;text-align:left">Notes</th></tr>
{rows}</table>
<p style="color:#888;font-size:12px">"Chance" is a heuristic estimate from seniority/experience/clearance
signals in the posting — not a real probability.</p></body></html>"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{CFG['email'].get('subject_prefix','[Job Scan]')} {len(jobs)} new roles — {NOW.strftime('%b %d')}"
    msg["From"], msg["To"] = addr, to
    msg.attach(MIMEText(body, "html"))
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.starttls()
    with server as s:
        s.login(user, pw)
        s.sendmail(addr, [to], msg.as_string())
    print(f"Email sent to {to} ({len(top)} jobs).")

# ---------------- main ----------------

def main():
    no_email = "--no-email" in sys.argv
    all_jobs, errors = [], []
    sources = []
    fa = CFG.get("free_apis", {})
    if fa.get("remoteok"): sources.append(("remoteok", src_remoteok))
    if fa.get("remotive"): sources.append(("remotive", src_remotive))
    if fa.get("arbeitnow"): sources.append(("arbeitnow", src_arbeitnow))
    if fa.get("himalayas"): sources.append(("himalayas", src_himalayas))
    if fa.get("weworkremotely"): sources.append(("weworkremotely", src_weworkremotely))
    sources.append(("adzuna", src_adzuna))
    for tk in CFG.get("greenhouse_boards", []):
        sources.append((f"gh:{tk}", lambda t=tk: src_greenhouse(t)))
    for tk in CFG.get("lever_boards", []):
        sources.append((f"lever:{tk}", lambda t=tk: src_lever(t)))
    for tk in CFG.get("ashby_boards", []):
        sources.append((f"ashby:{tk}", lambda t=tk: src_ashby(t)))

    for name, fn in sources:
        try:
            got = fn()
            fresh = [j for j in got if relevant(j)]
            all_jobs.extend(fresh)
            print(f"{name:24s} {len(got):4d} jobs, {len(fresh):3d} fresh+relevant")
        except Exception as e:
            errors.append(name)
            print(f"{name:24s} ERROR: {e}")

    # dedupe within run and against history
    seen = load_seen()
    uniq, new = {}, []
    for j in all_jobs:
        uniq[jid(j)] = j
    for h, j in uniq.items():
        score(j)
        if h not in seen:
            new.append(j)
            seen[h] = NOW.isoformat()
    new.sort(key=lambda j: -j["rank"])
    save_seen(seen)

    stats = f"{len(sources)} sources scanned" + (f", {len(errors)} unavailable" if errors else "")
    write_report(new, stats)
    print(f"\n{len(new)} new matches -> docs/index.html")

    if new:
        try:
            sync_airtable(new)
        except Exception as e:
            print(f"Airtable failed: {e}")
    if new and not no_email:
        try:
            send_email(new)
        except Exception as e:
            print(f"Email failed: {e}")
    elif not new:
        print("No new jobs; email skipped.")

if __name__ == "__main__":
    main()
