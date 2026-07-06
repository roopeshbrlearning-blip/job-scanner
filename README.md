# Daily Job Scanner — free, autonomous, runs at 8 AM even when your laptop is off

Scans company career portals (Greenhouse / Lever / Ashby ATS boards) + free job APIs
(RemoteOK, Remotive, Arbeitnow, Himalayas, WeWorkRemotely, Adzuna) for roles posted in
the **last 24 hours**, ranks them against your resume profile, then:

- **emails you** the ranked list (company, title, link, fit %, chance %)
- **publishes a web page** you can open from any device, anywhere
- runs **automatically every morning at 8 AM ET** on GitHub's servers (free) — your laptop/phone can be off
- can be **run on demand** anytime with one click

Everything is free: GitHub Actions (2,000 free min/month; this uses ~5/day), GitHub Pages, Gmail.

---

## Setup (~10 minutes, one time)

### 1. Create the GitHub repo
1. Sign up / log in at github.com
2. New repository → name it `job-scanner` → **Private is fine for Actions, but Pages needs Public on the free plan** → choose **Public** if you want the web link
3. Upload all files from this folder (drag-and-drop works: "uploading an existing file"). Make sure the `.github/workflows/daily-scan.yml` path is preserved — if drag-drop drops the folder, use "Add file → Create new file", type `.github/workflows/daily-scan.yml` as the name, and paste the content.

### 2. Gmail app password (so it can email you)
1. Go to https://myaccount.google.com/apppasswords (requires 2-Step Verification enabled)
2. Create app password named `job-scanner` → copy the 16-character password

### 3. Add secrets
Repo → Settings → Secrets and variables → **Actions** → New repository secret:

| Secret name | Value |
|---|---|
| `GMAIL_ADDRESS` | roopesh.billa@gmail.com |
| `GMAIL_APP_PASSWORD` | the 16-char app password |
| `ADZUNA_APP_ID` | (optional) from https://developer.adzuna.com — free, adds Indeed-style aggregate coverage |
| `ADZUNA_APP_KEY` | (optional) |

### 4. Enable the web page
Repo → Settings → Pages → Source: **Deploy from a branch** → Branch: `main`, folder `/docs` → Save.
Your report will live at: `https://<your-username>.github.io/job-scanner/`

### 5. Test it
Repo → **Actions** tab → "Daily job scan" → **Run workflow**. In ~2 min you'll get the email
and the web page updates. That same button is how you run it anytime from your phone.

Done. It now fires every morning at 8 AM ET automatically.

---

## Tuning

- **`profile.yaml`** — your skills/weights, target titles, years of experience. This drives the
  fit & chance scores. Update it whenever your resume changes.
- **`config.yaml`** — keyword filters, and the company board lists. To add a company, find its
  careers page URL:
  - `boards.greenhouse.io/<TOKEN>` → add token under `greenhouse_boards`
  - `jobs.lever.co/<TOKEN>` → `lever_boards`
  - `jobs.ashbyhq.com/<TOKEN>` → `ashby_boards`
  Wrong tokens are skipped harmlessly, so guessing is safe.
- **Schedule** — edit the cron in `.github/workflows/daily-scan.yml` (times are UTC;
  `0 12 * * *` = 8 AM EDT / 7 AM EST).

## How scoring works

- **Fit %** — weighted match of your `profile.yaml` skills against the job title (3×) and
  description (1×), plus similarity to your target titles.
- **Chance %** — fit adjusted by signals in the posting: senior/staff/principal titles,
  "N+ years" requirements above your experience, security-clearance or PhD requirements
  (penalties); entry-level wording and very fresh postings (bonuses). It's a heuristic
  estimate, **not** a real probability.
- **Rank** = 0.6 × fit + 0.4 × chance. Email and web page are sorted by rank.

## Notes & limits

- No free system can literally scan *every* company's portal. This covers three major ATS
  platforms directly (most startups/robotics/defense companies) plus aggregator APIs.
  Workday-hosted boards (big corps like NVIDIA, Qualcomm) have no free stable API — Adzuna
  usually catches those postings instead.
- `data/seen_jobs.json` remembers what you've already been sent, so you only ever see new roles.
- Run locally anytime: `pip install -r requirements.txt && python scan.py --no-email`
