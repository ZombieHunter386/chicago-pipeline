# Deploying to Render

Stand up a publicly-reachable, password-protected instance of the webapp on
Render with the SQLite database stored on Cloudflare R2. Total time: ~30 min
the first time, ~5 min for subsequent updates (which auto-deploy on push).

**End state:** `https://chicago-pipeline-XXXX.onrender.com` prompts for a
username + password, then renders the same UI you use locally.

**Cost:** ~$7.25/mo today — Render Starter plan ($7/mo) + 1 GB persistent
disk (~$0.25/mo) + Cloudflare R2 free tier ($0/mo). Scales to ~$10-12/mo
if the DB grows to all of Chicago / Cook County (R2 stays free up to 10 GB,
disk grows at $0.25/GB/mo).

---

## What this repo provides

The repo already contains everything Render needs:

| File | Purpose |
|---|---|
| `Dockerfile` | Builds a Python 3.14 image with the webapp + gunicorn |
| `render.yaml` | Blueprint Render reads to provision the service + disk + env vars |
| `webapp/wsgi.py` | gunicorn entry point — reads config from env vars |
| `webapp/auth.py` | HTTP basic auth middleware, gated by env vars |
| `scripts/init_db.sh` | First-boot init — downloads the DB onto the persistent disk |
| `requirements.txt` | Includes `gunicorn` for production WSGI |

You don't need to edit any of these for the standard deploy.

---

## Step 1 — Upload the database to Cloudflare R2 (free, ~10 min)

The 619 MB SQLite DB is too large for git. We host it on Cloudflare R2's
free tier (10 GB storage free, **zero egress fees forever** — Render
redownloads cost $0 even on disk wipes).

1. **Create a free Cloudflare account** at https://dash.cloudflare.com/sign-up
   - You don't need a domain or any paid plan
   - Verify the email

2. **Enable R2** (one-time, requires payment-card on file even though the
   free tier covers everything):
   - Cloudflare dashboard → left nav → "R2 Object Storage"
   - Click "Purchase R2 Plan" / "Enable R2"
   - Add a card; you won't be charged unless you exceed 10 GB or hit
     paid-tier API ops (roughly impossible for our use case)

3. **Create a public bucket:**
   - R2 dashboard → "Create bucket"
   - **Bucket name:** `chicago-pipeline-db` (lowercase, hyphens; must be
     unique within your account)
   - **Location:** "Automatic" (Cloudflare picks the nearest region)
   - Click "Create bucket"

4. **Enable public access on the bucket:**
   - Click into the bucket → "Settings" tab
   - Under "Public Access" → "R2.dev subdomain" → "Allow Access"
   - Confirm "Allow"
   - Cloudflare gives you a public URL like:
     `https://pub-1234567890abcdef.r2.dev`
   - Copy this base URL — you'll combine it with the file name in step 6

5. **Upload `data/full.alt.db`:**
   - Bucket → "Objects" tab → "Upload" → pick
     `/Users/hunterheyman/Claude/chicago-pipeline/data/full.alt.db`
   - Wait for the 619 MB upload (~3-8 min depending on your connection)
   - The web upload caps at ~5 GB; for larger files use `wrangler` or
     `rclone` (see "Updating the database" at the bottom)

6. **Build the full download URL** by combining the base URL + file name:
   - Pattern: `<r2.dev base URL>/full.alt.db`
   - Example: `https://pub-1234567890abcdef.r2.dev/full.alt.db`
   - **Verify it works:** paste into a browser; it should start downloading
     the file. (Cancel after a few MB once you've confirmed it's the DB.)

7. **Save this URL** — you'll paste it into Render in Step 3.

---

## Step 2 — Connect Render to GitHub

1. **Sign up / sign in to Render:** https://render.com — sign in with GitHub
   so Render can read the repo

2. **Authorize Render** to access `ZombieHunter386/chicago-pipeline`. If you
   already have Render connected to GitHub, you may need to "Configure" the
   GitHub app and grant access to this specific repo (Render only sees repos
   you've explicitly authorized).

---

## Step 3 — Deploy via Blueprint

1. **In Render dashboard:** click "New +" (top right) → "Blueprint"

2. **Connect your repo:** click "Connect a repository" → pick
   `ZombieHunter386/chicago-pipeline`

3. **Render reads `render.yaml` from the repo** and shows a preview of what
   will be created: one web service + one persistent disk + three secret
   env vars to fill in.

4. **Fill in the three secrets** (Render prompts for each):

   | Key | Value |
   |---|---|
   | `WEBAPP_USER` | (your friend's username — e.g. `David`) |
   | `WEBAPP_PASSWORD` | (your friend's password — never commit this anywhere) |
   | `DB_DOWNLOAD_URL` | the R2 public URL from Step 1.6 |

   These values stay in Render's encrypted secret store. They're not in the
   repo and not in this guide for that reason.

5. **Click "Apply"** to provision the service.

---

## Step 4 — Watch the first deploy

The first deploy takes ~3-5 minutes:

1. **Build phase (~2 min):** Render pulls the repo, builds the Docker image
   (`pip install -r requirements.txt` is the slow step).
2. **Disk provisioning:** Render attaches a 1 GB persistent disk at `/data`.
3. **Container start:** the entrypoint runs `scripts/init_db.sh`, which
   downloads the 619 MB DB from R2 (~30-90 sec on Render's network), then
   starts gunicorn.
4. **Health check:** Render hits `GET /` to confirm the app is responding.

Watch the deploy log live in the Render dashboard. Successful boot looks
like:
```
[init_db] Downloading DB from https://pub-XXXX.r2.dev/full.alt.db to /data/full.alt.db ...
[init_db] Downloaded 648900608 bytes to /data/full.alt.db
[2026-05-02 22:14:01 +0000] [1] [INFO] Starting gunicorn 23.0.0
[2026-05-02 22:14:01 +0000] [1] [INFO] Listening at: http://0.0.0.0:10000
```

When the deploy turns green, the URL is live.

---

## Step 5 — Test it yourself

1. **Open the URL** Render gives you (e.g.
   `https://chicago-pipeline-abcd.onrender.com`). Browser will pop a basic
   auth dialog asking for username + password.

2. **Enter your credentials.** Browser will remember them for the session.

3. **Verify the UI loads:** ranked list, map, filter panel — all the same
   as your local instance. The score breakdown panel should populate when
   you click a parcel.

If anything looks off, check the Render logs (dashboard → service → "Logs"
tab).

---

## Step 6 — Share with your friend

Send your friend three things, **out-of-band** (text, email, signal — not
in the public repo):

- The URL: `https://chicago-pipeline-XXXX.onrender.com`
- The username
- The password

Tell them: "It'll prompt for a login the first time, then your browser
remembers it. The map shows all the parcels in the Lincoln Park /
Lakeview area; click any pin or row in the left panel to see details."

---

## What auto-deploys on push

`render.yaml` has `autoDeploy: true`. Every push to `master` triggers a new
build + deploy. The DB on `/data` is **not** affected by deploys — it
persists across restarts.

## Updating the database

After re-running the local pipeline (cleanup → consolidate → condo_rollup →
score), the DB on R2 needs a refresh and Render needs to be told to
re-download it.

1. **Re-upload to R2:**
   - R2 dashboard → bucket → "Upload" → pick the new `data/full.alt.db`
   - Choose "Replace" when asked. The URL stays the same.

2. **Force Render to re-download** (it skips download if the disk has the
   file already):
   - Render dashboard → service → "Disks" → delete `chicago-pipeline-data`
     (it'll re-provision empty on the next deploy)
   - Trigger a manual deploy: "Manual Deploy" → "Deploy latest commit"
   - First boot of the new disk will run `init_db.sh` and pull the fresh DB

A faster option for big DBs (skip the manual deletion): SSH into the Render
service via "Shell" tab and `rm /data/full.alt.db`, then restart.

---

## Updating the password later

Render dashboard → service → "Environment" tab → edit `WEBAPP_PASSWORD` →
"Save Changes". Render restarts the service with the new value (~30 sec).

---

## Cost breakdown

| Component | Plan | Cost |
|---|---|---|
| Render web service | Starter ($7/mo, always-on, no cold starts) | $7.00 |
| Render disk | 1 GB persistent | ~$0.25 |
| Cloudflare R2 storage | 619 MB / 10 GB free | $0.00 |
| Cloudflare R2 egress | unlimited free, forever | $0.00 |
| **Total** | | **~$7.25/mo** |

If the DB grows to all of Chicago (~10 GB), increase `disk.sizeGB` in
`render.yaml` to 10 — Render disk goes to ~$2.50/mo, R2 stays free, total
~$9.50/mo. Past 10 GB on R2 it's $0.015/GB/mo (so 20 GB = $0.15/mo over
the free tier).

Render bills monthly and pro-rates by the day. You can pause the service
in the Render dashboard at any time to stop billing (the disk + R2 data
persist; redeploy when you want it live again).

---

## Cleaning up later

If you want to tear it down:

1. Render dashboard → service → "Settings" → "Delete Web Service"
2. Render dashboard → "Disks" → delete `chicago-pipeline-data`
3. Cloudflare R2 → bucket → "Manage Objects" → select all → "Delete"
4. Cloudflare R2 → bucket → "Settings" → "Delete bucket"

Once all four are deleted, you stop being billed.

---

## Refreshing the production DB on R2

The Railway deployment downloads the DB from `DB_DOWNLOAD_URL` at container
boot. When you need to refresh prod, **always sanitize first** if your local
DB has outreach data:

```bash
# 1. Sanitize a copy of the working DB (strips outreach/contacts/waves rows).
.venv/bin/python scripts/sanitize_db_for_r2.py data/full.alt.db data/full.alt.sanitized.db

# 2. Upload data/full.alt.sanitized.db to the R2 bucket using whatever method
#    you currently use (rclone, the Cloudflare web UI, etc.). Replace the
#    object at DB_DOWNLOAD_URL.

# 3. In the Railway dashboard, wipe the persistent volume and trigger a
#    redeploy so the new DB is downloaded fresh.
```

If you re-fetched from upstream (`pipeline.fetch_all → cleanup → consolidate
→ condo_rollup → score`) on a clean DB that never had outreach rows, you can
skip step 1 — but running the sanitize step on every upload is a safe habit.
