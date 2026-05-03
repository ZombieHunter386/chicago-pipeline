# Deploying to Render

Stand up a publicly-reachable, password-protected instance of the webapp on
Render with the SQLite database stored on Backblaze B2. Total time: ~30 min
the first time, ~5 min for subsequent updates (which auto-deploy on push).

**End state:** `https://chicago-pipeline-XXXX.onrender.com` prompts for a
username + password, then renders the same UI you use locally.

**Cost:** ~$8/mo total — Render Starter plan ($7/mo) + 1 GB persistent disk
(~$0.25/mo, billed as ~$1) + Backblaze B2 free tier ($0/mo for the DB).

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

## Step 1 — Upload the database to Backblaze B2 (free, ~10 min)

The 619 MB SQLite DB is too large for git. We host it on Backblaze B2's free
tier (10 GB free storage; the DB downloads once at first deploy).

1. **Create a free Backblaze account** at https://www.backblaze.com/cloud-storage
   - Click "Get Free Object Storage" → sign up with email
   - Verify the email address

2. **Create a public bucket:**
   - Sign in to https://secure.backblaze.com/
   - Left nav → "Buckets" → "Create a Bucket"
   - **Bucket Unique Name:** `chicago-pipeline-data` (must be globally unique;
     prepend something like your initials if taken — e.g. `hh-chicago-pipeline-data`)
   - **Files in Bucket are:** Public
   - **Default Encryption:** Disabled (default)
   - **Object Lock:** Disabled (default)
   - Click "Create a Bucket"

3. **Upload `data/full.alt.db`:**
   - Click into the bucket you just created
   - Click "Upload/Download" → drag-and-drop `data/full.alt.db` from
     `/Users/hunterheyman/Claude/chicago-pipeline/data/`
   - Wait for the 619 MB upload to complete (~5–10 min depending on your
     connection)

4. **Get the public download URL:**
   - Once uploaded, click the file's name in the bucket listing
   - Look for the "Friendly URL" field — copy this URL. It looks like:
     `https://f005.backblazeb2.com/file/chicago-pipeline-data/full.alt.db`
   - Verify it works: paste the URL into a browser; it should start
     downloading the file. (Cancel the download once you've confirmed.)

5. **Save this URL** — you'll paste it into Render in Step 3.

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
   | `DB_DOWNLOAD_URL` | the Backblaze friendly URL from Step 1 |

   These values stay in Render's encrypted secret store. They're not in the
   repo and not in this guide for that reason.

5. **Click "Apply"** to provision the service.

---

## Step 4 — Watch the first deploy

The first deploy takes ~3–5 minutes:

1. **Build phase (~2 min):** Render pulls the repo, builds the Docker image
   (`pip install -r requirements.txt` is the slow step).
2. **Disk provisioning:** Render attaches a 1 GB persistent disk at `/data`.
3. **Container start:** the entrypoint runs `scripts/init_db.sh`, which
   downloads the 619 MB DB from Backblaze (~30–60 sec on Render's network),
   then starts gunicorn.
4. **Health check:** Render hits `GET /` to confirm the app is responding.

Watch the deploy log live in the Render dashboard. Successful boot looks
like:
```
[init_db] Downloading DB from https://f005.backblazeb2.com/... to /data/full.alt.db ...
[init_db] Downloaded 649069568 bytes to /data/full.alt.db
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
persists across restarts. To force a re-download (e.g. after re-running
`pipeline.score` and uploading a new DB to Backblaze):

1. Upload the new file to Backblaze (overwrite the existing one — same URL)
2. In Render dashboard → service → "Disks" → delete `chicago-pipeline-data`
   (it'll re-provision empty on the next deploy)
3. Trigger a manual deploy

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
| Backblaze B2 storage | 619 MB / 10 GB free | $0.00 |
| Backblaze B2 egress | ~620 MB on first deploy + redeploys | $0.00 (3× storage free) |
| **Total** | | **~$7.25/mo** |

Render bills monthly and pro-rates by the day. You can pause the service
in the Render dashboard at any time to stop billing (the disk + Backblaze
data persist; redeploy when you want it live again).

---

## Cleaning up later

If you want to tear it down:

1. Render dashboard → service → "Settings" → "Delete Web Service"
2. Render dashboard → "Disks" → delete `chicago-pipeline-data`
3. Backblaze → bucket → "Delete all files" → "Delete bucket"

Once all three are deleted, you stop being billed.
