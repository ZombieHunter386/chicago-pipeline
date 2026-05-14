# Chicago Pipeline Outreach (Plan 4) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up the existing outreach scaffolding (DB schema, `FEATURE_OUTREACH` flag, UI stubs) into a working single-touch email outreach loop: enter owner email → pick a template → review draft in a modal → send via Gmail API → record an outreach row → log future replies. The whole feature is local-only (code ships behind a flag, data lives only on the dev machine).

**Architecture:** Single Flask app, feature-gated. New write endpoints (`POST /api/contacts/upsert`, `POST /api/outreach/send`, `POST /api/outreach/<id>/mark-replied`, `POST /api/parcels/<pin>/stage`, `GET /api/oauth/start`, `GET /api/oauth/callback`) are only registered when `FEATURE_OUTREACH` is true; on Railway the flag is unset so the routes return 404. Outreach rows live in the same SQLite DB as the rest of the data — but the production DB on R2 is only ever rewritten from a fresh upstream fetch (or a sanitize script that drops outreach/contacts/waves rows), so outreach data never reaches Railway. Gmail credentials are stored in a gitignored `data/gmail_token.json`. The compose-and-confirm flow renders a modal in `index.html`, populated by a new `webapp/static/js/outreach.js` module that talks to the new endpoints.

**Tech Stack:** Existing — Flask 3, vanilla JS, SQLite, PyYAML, pytest. New — `google-auth`, `google-auth-oauthlib`, `google-api-python-client` for Gmail send. Email templates use simple `{{var}}` regex substitution (no Jinja2 — narrower surface area, predictable, easy to test).

---

## Scope & Out-of-Scope

**In scope (this plan):**
- Local-only safeguards: `FEATURE_OUTREACH` gates every write route at register-time; `data/gmail_token.json` + `data/gmail_oauth_client.json` are gitignored; `scripts/sanitize_db_for_r2.py` strips outreach rows before any R2 upload.
- One contact per parcel (we use the existing `contacts` table but only store one row per pin for now). User hand-enters email; no skip-trace integration.
- One-shot outreach (single touch). The `outreach.touch_number` column gets populated as `1`; we don't model multi-touch sequences in this plan.
- Compose-and-confirm UI: detail panel shows owner email field + outreach history + "Compose Email" button. Modal pre-fills subject/body from a YAML template; user reviews/edits; click Send → Gmail API → row recorded.
- Gmail OAuth 2.0 web-application flow with `http://localhost:5051/api/oauth/callback` as the redirect URI. Refresh-token persistence in JSON file.
- Manual response tracking: "Mark Replied" button on each outreach history row sets `response_date` + `response_type='responded'`.
- Stage management: scored → outreach automatically on first successful send. Manual dropdown for downstream transitions (responded / introduced / dead).

**Explicitly out of scope (deferred to later plans):**
- Lob / physical mail integration.
- Waves: batch outreach scheduling, cohort tracking, cadence reminders ("Due Today" banner stays empty for now).
- Multi-touch sequences (`touch_number > 1`), follow-up cadence rules.
- Contact enrichment: IL Sec of State LLC lookup, REISkip / paid skip-trace, Zillow scrape.
- Multiple contacts per parcel (one contact per pin in v1).
- Inbound reply auto-detection (Gmail watch / IMAP). User flags replies manually.
- Multi-tenant: this is single-user; no per-user OAuth tokens or row-level scoping.
- Feedback report (wave-level metrics dashboard).

---

## Risks & Gaps

1. **No owner emails in the data.** Cook County Assessor gives `mail_address` (physical) + `owner_name`. Email must be hand-entered by the user. **Mitigation:** the detail panel exposes an editable email field per parcel that persists to `contacts.email`. Without an email, the Compose button is disabled and shows a tooltip explaining why.

2. **Gmail OAuth client secrets are technically "secret" but Google treats installed-app clients as semi-public.** Standard practice for a single-user local-only app is .env / gitignored token file. **Mitigation:** `data/gmail_token.json` and `data/gmail_oauth_client.json` are in `.gitignore`; `.dockerignore` excludes them so even an accidental `docker build` locally won't ship them. The webapp refuses to register OAuth routes when `FEATURE_OUTREACH` is false, so even if the token file were somehow on the prod container, the flow is unreachable.

3. **Gmail API send limits.** Personal Gmail caps at 500 sends/day; Workspace at 2000/day. **Mitigation:** Single-user prospecting at <50/day in practice. We don't add throttling code; if you hit the limit, Gmail returns 429 and the UI surfaces the error. Future waves work can add per-day caps.

4. **CSRF.** The app gains write endpoints for the first time. **Decision:** No CSRF tokens. The app is single-user behind Basic Auth on Railway (where outreach is disabled anyway) and on localhost otherwise. Same-origin enforcement + Basic Auth covers the realistic threat. If the app ever becomes multi-user, add Flask-WTF or similar.

5. **Email subject injection.** A user-edited subject containing `\r\n` could inject headers when passed to the Gmail API. **Mitigation:** `pipeline/outreach.py:sanitize_subject()` strips newlines/CR before sending. Tested.

6. **Idempotency.** Double-clicking "Send" could send twice. **Mitigation:** Frontend disables the button on click. Backend logs both attempts (we don't dedupe — better to know about a double-send than silently swallow).

7. **Refreshing prod DB while outreach data exists.** If the user re-runs `fetch_all → cleanup → consolidate → condo_rollup → score` locally, then wants to push the refreshed DB to R2, outreach rows go with it unless stripped. **Mitigation:** `scripts/sanitize_db_for_r2.py` is documented in `DEPLOY.md` as the canonical "prep for R2" step.

8. **Schema is correct for our use as-is.** The existing `outreach` / `contacts` / `waves` tables in `pipeline/db.py` cover what we need; no migration. We just populate them.

---

## File Structure

**New files:**

```
chicago-pipeline/
  config/
    outreach_templates.yaml    # email templates with {{var}} merge fields
  pipeline/
    outreach.py                # domain: load templates, render, DB helpers
    gmail_client.py            # Gmail OAuth + send helpers
  scripts/
    sanitize_db_for_r2.py      # strip outreach/contacts/waves before R2 upload
  webapp/
    static/
      js/
        outreach.js            # detail panel sections + compose modal
  tests/
    test_pipeline_outreach.py
    test_pipeline_gmail_client.py
    test_webapp_outreach_routes.py
    test_scripts_sanitize_db.py
```

**Modified files:**

- `requirements.txt` — add `google-auth==2.36.0`, `google-auth-oauthlib==1.2.1`, `google-api-python-client==2.151.0`
- `.gitignore` — add `data/gmail_token.json`, `data/gmail_oauth_client.json`, `.env.local`
- `.dockerignore` — add same three patterns
- `.env.example` — document `GMAIL_OAUTH_CLIENT_PATH`, `GMAIL_TOKEN_PATH`, `GMAIL_SENDER_ADDRESS`
- `webapp/__main__.py` — fix `--outreach` help string (drop "not implemented" once shipped)
- `webapp/app.py` — wire new config keys for Gmail paths
- `webapp/routes.py` — register outreach read + write routes inside `if FEATURE_OUTREACH` guard
- `webapp/static/js/detail.js` — replace `sectionOutreachStub()` call with the new outreach sections from `outreach.js`
- `webapp/templates/index.html` — load `outreach.js`; add compose-modal markup behind `{% if feature_outreach %}`
- `webapp/static/css/style.css` — modal styles, form input styles, history list styles
- `DEPLOY.md` — add "preparing the DB for R2" workflow
- `README.md` — outreach section: how to enable, Gmail setup steps, template editing

**Responsibilities (one per file):**

- `pipeline/outreach.py` — pure logic. Load templates from YAML. Render subject/body with `{{var}}` substitution. CRUD helpers for `contacts` and `outreach` tables. No Flask, no Google libs.
- `pipeline/gmail_client.py` — Gmail OAuth flow + send. Stateless functions that read/write the token JSON file. No Flask.
- `webapp/routes.py` — thin HTTP handlers. Delegate to `pipeline/outreach.py` and `pipeline/gmail_client.py`. Feature flag check at register-time means routes don't exist when the flag is off.
- `webapp/static/js/outreach.js` — owns the outreach detail-panel sections + compose modal. Exports `renderOutreachSections(parcel, panel)` and `openComposeModal(parcel, contact)`.
- `scripts/sanitize_db_for_r2.py` — copy DB to a new path, run `DELETE FROM outreach; DELETE FROM contacts; DELETE FROM waves;`, return new path. Stdlib only.

---

## Task 1: Pre-flight — config files, gitignore, sanitize script

**Files:**
- Modify: `chicago-pipeline/requirements.txt`
- Modify: `chicago-pipeline/.gitignore`
- Create: `chicago-pipeline/.dockerignore` (if missing) or modify (if present)
- Modify: `chicago-pipeline/.env.example`
- Create: `chicago-pipeline/config/outreach_templates.yaml`
- Create: `chicago-pipeline/scripts/sanitize_db_for_r2.py`
- Create: `chicago-pipeline/tests/test_scripts_sanitize_db.py`
- Modify: `chicago-pipeline/DEPLOY.md`

- [ ] **Step 1: Add Google libs to requirements**

Append to `requirements.txt`:

```
google-auth==2.36.0
google-auth-oauthlib==1.2.1
google-api-python-client==2.151.0
```

Run: `cd /Users/hunterheyman/Claude/chicago-pipeline && .venv/bin/pip install -r requirements.txt`

Expected: `Successfully installed google-api-python-client-2.151.0 google-auth-2.36.0 google-auth-oauthlib-1.2.1 ...`

- [ ] **Step 2: Update .gitignore**

Append to `.gitignore`:

```
data/gmail_token.json
data/gmail_oauth_client.json
.env.local
```

- [ ] **Step 3: Update / create .dockerignore**

Check whether `.dockerignore` exists: `cat chicago-pipeline/.dockerignore`. If it exists, append the three patterns above. If not, create it with:

```
__pycache__
*.pyc
.pytest_cache
.venv
.git
.env
.env.local
data/gmail_token.json
data/gmail_oauth_client.json
data/*.db
data/*.csv
data/*.log
tests/
docs/
```

- [ ] **Step 4: Update .env.example**

Append to `.env.example`:

```
# ----- Outreach (local only — never set on Railway) -----
# Path to the Gmail OAuth client JSON downloaded from Google Cloud Console.
GMAIL_OAUTH_CLIENT_PATH=data/gmail_oauth_client.json
# Path to the persisted Gmail refresh-token JSON (created by the OAuth flow).
GMAIL_TOKEN_PATH=data/gmail_token.json
# The Gmail address sends from — should match the account that authorized OAuth.
GMAIL_SENDER_ADDRESS=your-address@gmail.com
```

- [ ] **Step 5: Create config/outreach_templates.yaml**

```yaml
# Email templates used by the outreach compose modal.
# Variables (rendered with {{var}} regex substitution):
#   {{owner_name}}, {{owner_first_name}}, {{address}}, {{ward}}, {{zip}},
#   {{score}}, {{property_class}}, {{year_built}}, {{building_sf}}, {{lot_size_sf}},
#   {{my_name}}, {{my_email}}, {{my_phone}}
# Missing variables render as literal "{{name}}" so you'll see them in preview.
templates:
  - name: initial-cold
    label: "Initial cold inquiry"
    subject: "Question about {{address}}"
    body: |
      Hi {{owner_first_name}},

      I'm reaching out about your property at {{address}}. I work with a small
      group that buys and renovates multifamily buildings on the South and West
      Sides, and your building caught our attention.

      No agents, no pressure — we just like to introduce ourselves to local owners
      directly. If you've ever thought about selling, I'd love to chat.

      Best,
      {{my_name}}
      {{my_phone}}
defaults:
  my_name: "Hunter"
  my_email: "hsheyman@gmail.com"
  my_phone: ""
```

- [ ] **Step 6: Write failing test for sanitize script**

Create `tests/test_scripts_sanitize_db.py`:

```python
"""Tests for scripts/sanitize_db_for_r2.py — the safety net that strips
outreach/contacts/waves rows before uploading the DB to R2."""
from __future__ import annotations
import sqlite3
import subprocess
import sys
from pathlib import Path


def _make_db(path: Path) -> None:
    """Build a minimal DB with outreach/contacts/waves rows + one parcel."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE parcels (pin TEXT PRIMARY KEY, owner_name TEXT);
        CREATE TABLE contacts (contact_id INTEGER PRIMARY KEY, pin TEXT, email TEXT);
        CREATE TABLE outreach (outreach_id INTEGER PRIMARY KEY, pin TEXT, sent_date TEXT);
        CREATE TABLE waves (wave_id INTEGER PRIMARY KEY, notes TEXT);
        INSERT INTO parcels VALUES ('123', 'Acme LLC');
        INSERT INTO contacts VALUES (1, '123', 'a@b.com');
        INSERT INTO outreach VALUES (1, '123', '2026-05-14');
        INSERT INTO waves VALUES (1, 'wave-1');
        """
    )
    conn.commit()
    conn.close()


def test_sanitize_strips_outreach_keeps_parcels(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    _make_db(src)
    dst = tmp_path / "out.db"

    result = subprocess.run(
        [sys.executable, "scripts/sanitize_db_for_r2.py", str(src), str(dst)],
        capture_output=True, text=True, check=True,
    )
    assert dst.exists(), f"output DB not created. stderr={result.stderr}"

    conn = sqlite3.connect(dst)
    try:
        assert conn.execute("SELECT COUNT(*) FROM parcels").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM outreach").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM waves").fetchone()[0] == 0
    finally:
        conn.close()


def test_sanitize_refuses_to_overwrite_source(tmp_path: Path) -> None:
    src = tmp_path / "src.db"
    _make_db(src)
    result = subprocess.run(
        [sys.executable, "scripts/sanitize_db_for_r2.py", str(src), str(src)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "same path" in (result.stderr + result.stdout).lower()
```

Run: `cd /Users/hunterheyman/Claude/chicago-pipeline && .venv/bin/python -m pytest tests/test_scripts_sanitize_db.py -v`

Expected: 2 failures (script doesn't exist yet).

- [ ] **Step 7: Implement scripts/sanitize_db_for_r2.py**

Create:

```python
"""Strip outreach / contacts / waves rows from a DB copy so it's safe to
upload to R2. The source DB is untouched; the destination is a fresh copy
with those three tables emptied.

Usage:
    python scripts/sanitize_db_for_r2.py <source.db> <destination.db>
"""
from __future__ import annotations
import shutil
import sqlite3
import sys
from pathlib import Path


def sanitize(src: Path, dst: Path) -> None:
    if src.resolve() == dst.resolve():
        raise SystemExit(
            "ERROR: source and destination must be different (got same path). "
            "Refusing to mutate the source DB in place."
        )
    if not src.exists():
        raise SystemExit(f"ERROR: source DB not found: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    conn = sqlite3.connect(dst)
    try:
        conn.executescript(
            "DELETE FROM outreach;\n"
            "DELETE FROM contacts;\n"
            "DELETE FROM waves;\n"
            "VACUUM;"
        )
        conn.commit()
    finally:
        conn.close()
    print(f"Sanitized DB written to {dst}")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: sanitize_db_for_r2.py <source.db> <destination.db>")
    sanitize(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
```

Run: `.venv/bin/python -m pytest tests/test_scripts_sanitize_db.py -v`

Expected: 2 passed.

- [ ] **Step 8: Update DEPLOY.md**

Append a new section. Open `DEPLOY.md` and add at the bottom:

```markdown
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
```

- [ ] **Step 9: Commit**

```bash
cd /Users/hunterheyman/Claude/chicago-pipeline
git add requirements.txt .gitignore .dockerignore .env.example \
        config/outreach_templates.yaml \
        scripts/sanitize_db_for_r2.py \
        tests/test_scripts_sanitize_db.py \
        DEPLOY.md
git commit -m "chore(outreach): pre-flight — deps, gitignore, sanitize script, templates"
```

---

## Task 2: Outreach domain module — templates + DB helpers

**Files:**
- Create: `chicago-pipeline/pipeline/outreach.py`
- Create: `chicago-pipeline/tests/test_pipeline_outreach.py`

- [ ] **Step 1: Write failing tests for template rendering**

Create `tests/test_pipeline_outreach.py`:

```python
"""Tests for pipeline/outreach.py — template rendering and DB helpers."""
from __future__ import annotations
import sqlite3
from pathlib import Path
from textwrap import dedent

import pytest

from pipeline.outreach import (
    load_templates,
    render_template,
    sanitize_subject,
    upsert_contact,
    list_outreach_for_parcel,
    create_outreach_record,
    mark_replied,
    parcel_context,
)


# ---------- template loading + rendering ----------

def test_load_templates_returns_named_dict(tmp_path: Path) -> None:
    yaml_text = dedent("""\
        templates:
          - name: t1
            label: First
            subject: "Hi {{owner_name}}"
            body: "Body 1"
          - name: t2
            label: Second
            subject: "Hey"
            body: "Body 2"
        defaults:
          my_name: Hunter
    """)
    p = tmp_path / "templates.yaml"
    p.write_text(yaml_text)
    out = load_templates(p)
    assert set(out["templates"].keys()) == {"t1", "t2"}
    assert out["templates"]["t1"]["subject"] == "Hi {{owner_name}}"
    assert out["defaults"]["my_name"] == "Hunter"


def test_render_template_substitutes_known_variables() -> None:
    text = "Hi {{owner_first_name}}, about {{address}} (score {{score}})."
    ctx = {"owner_first_name": "Jane", "address": "123 W Main", "score": "87.4"}
    assert render_template(text, ctx) == "Hi Jane, about 123 W Main (score 87.4)."


def test_render_template_keeps_unknown_variables_literal() -> None:
    """Missing vars stay as {{name}} so the user sees what they need to fill in."""
    text = "Hi {{owner_first_name}}, your {{mystery_field}} is interesting."
    ctx = {"owner_first_name": "Jane"}
    assert render_template(text, ctx) == \
        "Hi Jane, your {{mystery_field}} is interesting."


def test_render_template_tolerates_whitespace_in_braces() -> None:
    assert render_template("{{ name }}", {"name": "Alice"}) == "Alice"


def test_render_template_handles_non_string_values() -> None:
    assert render_template("Score: {{score}}", {"score": 87.4}) == "Score: 87.4"


def test_sanitize_subject_strips_newlines_and_cr() -> None:
    assert sanitize_subject("Hello\r\nBcc: evil@example.com") == \
        "HelloBcc: evil@example.com"
    assert sanitize_subject("Plain subject") == "Plain subject"


# ---------- DB helpers ----------

@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Minimal schema mirroring the relevant tables. Real schema lives in
    pipeline/db.py; we duplicate the parts we touch here to keep this test
    isolated from full-schema initialization."""
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE parcels (
            pin TEXT PRIMARY KEY, owner_name TEXT, address TEXT,
            stage TEXT DEFAULT 'scored', score REAL
        );
        CREATE TABLE contacts (
            contact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            pin TEXT, consolidation_group_id INTEGER,
            name TEXT, phone TEXT, email TEXT,
            mailing_address TEXT, role TEXT, source TEXT
        );
        CREATE TABLE outreach (
            outreach_id INTEGER PRIMARY KEY AUTOINCREMENT,
            wave_id INTEGER, pin TEXT, consolidation_group_id INTEGER,
            contact_id INTEGER, channel TEXT, touch_number INTEGER,
            sent_date TEXT, response_date TEXT, response_type TEXT,
            handed_off INTEGER DEFAULT 0, handed_off_date TEXT,
            draft_subject TEXT, draft_body TEXT, final_body TEXT,
            lob_tracking_id TEXT, lob_status TEXT, notes TEXT
        );
        INSERT INTO parcels (pin, owner_name, address, score)
            VALUES ('14210010010000', 'JOHN SMITH', '123 W Main St', 82.5);
    """)
    conn.commit()
    return conn


def test_upsert_contact_inserts_new(db: sqlite3.Connection) -> None:
    cid = upsert_contact(db, pin="14210010010000",
                         email="js@example.com", name="John Smith", source="manual")
    row = db.execute("SELECT * FROM contacts WHERE contact_id = ?", (cid,)).fetchone()
    assert row["email"] == "js@example.com"
    assert row["name"] == "John Smith"
    assert row["source"] == "manual"


def test_upsert_contact_updates_existing(db: sqlite3.Connection) -> None:
    cid1 = upsert_contact(db, pin="14210010010000", email="old@example.com")
    cid2 = upsert_contact(db, pin="14210010010000", email="new@example.com")
    assert cid1 == cid2  # same row, updated in place
    n = db.execute("SELECT COUNT(*) FROM contacts WHERE pin = ?",
                   ("14210010010000",)).fetchone()[0]
    assert n == 1
    row = db.execute("SELECT * FROM contacts WHERE contact_id = ?", (cid1,)).fetchone()
    assert row["email"] == "new@example.com"


def test_upsert_contact_preserves_unset_fields(db: sqlite3.Connection) -> None:
    """Upserting only the email shouldn't blank out the name."""
    upsert_contact(db, pin="14210010010000", email="x@y.com", name="John")
    upsert_contact(db, pin="14210010010000", email="x@y.com", phone="555-0100")
    row = db.execute("SELECT * FROM contacts WHERE pin = ?",
                     ("14210010010000",)).fetchone()
    assert row["name"] == "John"
    assert row["phone"] == "555-0100"


def test_create_outreach_record_inserts_and_returns_id(db: sqlite3.Connection) -> None:
    cid = upsert_contact(db, pin="14210010010000", email="js@example.com")
    oid = create_outreach_record(
        db, pin="14210010010000", contact_id=cid,
        channel="email", subject="Hi", body="Body text",
        sent_date="2026-05-14T12:34:56Z",
    )
    row = db.execute("SELECT * FROM outreach WHERE outreach_id = ?", (oid,)).fetchone()
    assert row["channel"] == "email"
    assert row["draft_subject"] == "Hi"
    assert row["final_body"] == "Body text"
    assert row["sent_date"] == "2026-05-14T12:34:56Z"
    assert row["touch_number"] == 1


def test_list_outreach_for_parcel_returns_in_reverse_chrono(db: sqlite3.Connection) -> None:
    create_outreach_record(db, pin="14210010010000", contact_id=None,
                           channel="email", subject="First", body="b1",
                           sent_date="2026-05-10T09:00:00Z")
    create_outreach_record(db, pin="14210010010000", contact_id=None,
                           channel="email", subject="Second", body="b2",
                           sent_date="2026-05-14T09:00:00Z")
    rows = list_outreach_for_parcel(db, "14210010010000")
    assert [r["draft_subject"] for r in rows] == ["Second", "First"]


def test_mark_replied_sets_response_fields(db: sqlite3.Connection) -> None:
    oid = create_outreach_record(db, pin="14210010010000", contact_id=None,
                                 channel="email", subject="Hi", body="b",
                                 sent_date="2026-05-14T09:00:00Z")
    mark_replied(db, oid, response_date="2026-05-15T11:00:00Z",
                 response_type="responded")
    row = db.execute("SELECT * FROM outreach WHERE outreach_id = ?", (oid,)).fetchone()
    assert row["response_date"] == "2026-05-15T11:00:00Z"
    assert row["response_type"] == "responded"


def test_parcel_context_builds_merge_vars(db: sqlite3.Connection) -> None:
    """parcel_context turns a parcel row into the variables that templates use."""
    parcel = dict(db.execute(
        "SELECT * FROM parcels WHERE pin = ?", ("14210010010000",)
    ).fetchone())
    defaults = {"my_name": "Hunter", "my_email": "h@example.com", "my_phone": "555"}
    ctx = parcel_context(parcel, defaults)
    assert ctx["owner_name"] == "JOHN SMITH"
    assert ctx["owner_first_name"] == "John"
    assert ctx["address"] == "123 W Main St"
    assert ctx["score"] == "82.5"
    assert ctx["my_name"] == "Hunter"


def test_parcel_context_handles_llc_owner_first_name(db: sqlite3.Connection) -> None:
    """For LLC owners, owner_first_name defaults to 'there' (no real first name)."""
    db.execute("UPDATE parcels SET owner_name = ? WHERE pin = ?",
               ("123 MAIN ST LLC", "14210010010000"))
    parcel = dict(db.execute(
        "SELECT * FROM parcels WHERE pin = ?", ("14210010010000",)
    ).fetchone())
    ctx = parcel_context(parcel, {})
    assert ctx["owner_first_name"] == "there"
```

Run: `.venv/bin/python -m pytest tests/test_pipeline_outreach.py -v`

Expected: all fail with `ImportError: cannot import name '...' from 'pipeline.outreach'`.

- [ ] **Step 2: Implement pipeline/outreach.py**

Create:

```python
"""Outreach domain logic — template loading, rendering, DB helpers.

Pure functions over a sqlite3.Connection. No Flask, no Google libs.
"""
from __future__ import annotations
import re
import sqlite3
from pathlib import Path
from typing import Any

import yaml


_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_LLC_TOKENS = {"LLC", "INC", "CORP", "CO", "LP", "LTD", "TRUST", "TRUSTEE", "PARTNERS"}


def load_templates(path: Path) -> dict[str, Any]:
    """Load outreach_templates.yaml. Returns a dict with keys:
      templates: {name -> {label, subject, body}}
      defaults: {my_name, my_email, my_phone, ...}
    """
    with Path(path).open() as f:
        data = yaml.safe_load(f) or {}
    templates_list = data.get("templates", []) or []
    templates = {t["name"]: t for t in templates_list if "name" in t}
    return {"templates": templates, "defaults": data.get("defaults") or {}}


def render_template(text: str, context: dict[str, Any]) -> str:
    """Replace {{var}} occurrences. Missing vars stay as literal {{var}} so the
    user can see what they need to fill in. Whitespace inside braces is tolerated.
    """
    def replace(m: re.Match[str]) -> str:
        var = m.group(1)
        if var in context and context[var] is not None:
            return str(context[var])
        return m.group(0)
    return _VAR_RE.sub(replace, text)


def sanitize_subject(subject: str) -> str:
    """Strip CR/LF from email subject lines (header-injection guard)."""
    return subject.replace("\r", "").replace("\n", "")


def _owner_first_name(owner_name: str | None) -> str:
    """Best-effort first name from a parsed Assessor owner string.

    Owner strings are ALL CAPS. Common forms:
      "JOHN SMITH"            -> "John"
      "SMITH, JOHN"           -> "John"
      "JOHN & MARY SMITH"     -> "John"
      "ACME PROPERTIES LLC"   -> "there"   (entity, no person)
    """
    if not owner_name:
        return "there"
    tokens = re.split(r"[\s,&/]+", owner_name.strip())
    tokens = [t for t in tokens if t]
    if not tokens:
        return "there"
    if any(t.upper() in _LLC_TOKENS for t in tokens):
        return "there"
    # "SMITH, JOHN" — comma form: first name is the token after the comma
    if "," in owner_name:
        parts = [p.strip() for p in owner_name.split(",", 1)]
        if len(parts) == 2 and parts[1]:
            first = parts[1].split()[0]
            return first.capitalize()
    # Otherwise first token is the first name.
    return tokens[0].capitalize()


def parcel_context(parcel: dict[str, Any], defaults: dict[str, Any]) -> dict[str, str]:
    """Build the merge-variable context for a parcel.

    String values only — render_template stringifies everything anyway; doing it
    here means tests don't have to assert on float repr quirks.
    """
    def s(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            # Keep small decimals readable.
            return f"{v:.1f}".rstrip("0").rstrip(".") or "0"
        return str(v)

    ctx: dict[str, str] = {
        "owner_name": s(parcel.get("owner_name")),
        "owner_first_name": _owner_first_name(parcel.get("owner_name")),
        "address": s(parcel.get("address")),
        "ward": s(parcel.get("ward_num")),
        "zip": s(parcel.get("zip_code")),
        "score": s(parcel.get("score")),
        "property_class": s(parcel.get("property_class")),
        "year_built": s(parcel.get("year_built")),
        "building_sf": s(parcel.get("building_sf")),
        "lot_size_sf": s(parcel.get("lot_size_sf")),
    }
    for k, v in defaults.items():
        ctx.setdefault(k, s(v))
    return ctx


def upsert_contact(
    conn: sqlite3.Connection,
    *,
    pin: str,
    email: str | None = None,
    name: str | None = None,
    phone: str | None = None,
    mailing_address: str | None = None,
    role: str | None = None,
    source: str | None = None,
) -> int:
    """Upsert by pin (one contact row per pin in v1). Returns the contact_id.

    Only non-None fields are written — passing email=None on an update preserves
    the existing email value. This matters because the UI may upsert just an
    email today, then just a phone later.
    """
    row = conn.execute(
        "SELECT contact_id FROM contacts WHERE pin = ? LIMIT 1", (pin,)
    ).fetchone()
    fields = {
        "email": email, "name": name, "phone": phone,
        "mailing_address": mailing_address, "role": role, "source": source,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    if row is None:
        cols = ["pin"] + list(fields.keys())
        placeholders = ",".join("?" * len(cols))
        params = [pin] + list(fields.values())
        cur = conn.execute(
            f"INSERT INTO contacts ({','.join(cols)}) VALUES ({placeholders})",
            params,
        )
        conn.commit()
        return int(cur.lastrowid)
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(
            f"UPDATE contacts SET {sets} WHERE contact_id = ?",
            list(fields.values()) + [row["contact_id"]],
        )
        conn.commit()
    return int(row["contact_id"])


def create_outreach_record(
    conn: sqlite3.Connection,
    *,
    pin: str,
    contact_id: int | None,
    channel: str,
    subject: str,
    body: str,
    sent_date: str,
    touch_number: int = 1,
) -> int:
    """Insert an outreach row. Returns the outreach_id."""
    cur = conn.execute(
        """
        INSERT INTO outreach
            (pin, contact_id, channel, touch_number, sent_date,
             draft_subject, draft_body, final_body)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (pin, contact_id, channel, touch_number, sent_date, subject, body, body),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_outreach_for_parcel(
    conn: sqlite3.Connection, pin: str
) -> list[sqlite3.Row]:
    """Return outreach rows for a parcel, most recent first."""
    return conn.execute(
        """
        SELECT * FROM outreach
        WHERE pin = ?
        ORDER BY COALESCE(sent_date, '') DESC, outreach_id DESC
        """,
        (pin,),
    ).fetchall()


def mark_replied(
    conn: sqlite3.Connection,
    outreach_id: int,
    *,
    response_date: str,
    response_type: str = "responded",
) -> None:
    """Mark an outreach row as replied."""
    conn.execute(
        "UPDATE outreach SET response_date = ?, response_type = ? "
        "WHERE outreach_id = ?",
        (response_date, response_type, outreach_id),
    )
    conn.commit()
```

Run: `.venv/bin/python -m pytest tests/test_pipeline_outreach.py -v`

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add pipeline/outreach.py tests/test_pipeline_outreach.py
git commit -m "feat(outreach): template rendering + contacts/outreach DB helpers"
```

---

## Task 3: Gmail OAuth + send helpers

**Files:**
- Create: `chicago-pipeline/pipeline/gmail_client.py`
- Create: `chicago-pipeline/tests/test_pipeline_gmail_client.py`

- [ ] **Step 1: Write failing tests for the Gmail client**

Create `tests/test_pipeline_gmail_client.py`:

```python
"""Tests for pipeline/gmail_client.py. The Google API is mocked end-to-end —
we never make a real HTTP call. The goal is to verify the wiring: token load /
save, message construction, header handling, error mapping.
"""
from __future__ import annotations
import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pipeline.gmail_client import (
    GmailNotConnectedError,
    build_authorization_url,
    exchange_code_for_token,
    is_connected,
    load_credentials,
    save_token,
    send_email,
)


# ---------- token storage ----------

def test_is_connected_false_when_no_token(tmp_path: Path) -> None:
    assert is_connected(tmp_path / "missing.json") is False


def test_save_and_load_token_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "token.json"
    save_token(p, {
        "refresh_token": "rt-abc",
        "client_id": "cid",
        "client_secret": "secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/gmail.send"],
    })
    assert p.exists()
    assert is_connected(p) is True
    data = json.loads(p.read_text())
    assert data["refresh_token"] == "rt-abc"


def test_save_token_writes_restrictive_permissions(tmp_path: Path) -> None:
    """The token file is a credential — make sure it's not world-readable."""
    p = tmp_path / "token.json"
    save_token(p, {"refresh_token": "rt"})
    mode = p.stat().st_mode & 0o777
    # owner-only read/write
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_load_credentials_raises_when_disconnected(tmp_path: Path) -> None:
    with pytest.raises(GmailNotConnectedError):
        load_credentials(tmp_path / "missing.json")


# ---------- OAuth flow ----------

def _client_secret_json(tmp_path: Path) -> Path:
    """Fake Google OAuth client JSON, web-app shape."""
    p = tmp_path / "client.json"
    p.write_text(json.dumps({
        "web": {
            "client_id": "cid.apps.googleusercontent.com",
            "client_secret": "secret",
            "redirect_uris": ["http://localhost:5051/api/oauth/callback"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }))
    return p


def test_build_authorization_url_returns_url_and_state(tmp_path: Path) -> None:
    client = _client_secret_json(tmp_path)
    with patch("pipeline.gmail_client.Flow") as flow_cls:
        flow = MagicMock()
        flow.authorization_url.return_value = ("https://accounts.google.com/auth?x=1",
                                                "state-abc")
        flow_cls.from_client_secrets_file.return_value = flow

        url, state = build_authorization_url(
            client_secrets_path=client,
            redirect_uri="http://localhost:5051/api/oauth/callback",
        )
    assert url == "https://accounts.google.com/auth?x=1"
    assert state == "state-abc"
    # We pass scopes and redirect to Flow.from_client_secrets_file
    args, kwargs = flow_cls.from_client_secrets_file.call_args
    assert kwargs["scopes"] == ["https://www.googleapis.com/auth/gmail.send"]
    assert kwargs["redirect_uri"] == "http://localhost:5051/api/oauth/callback"


def test_exchange_code_for_token_persists_refresh_token(tmp_path: Path) -> None:
    client = _client_secret_json(tmp_path)
    token_path = tmp_path / "token.json"
    with patch("pipeline.gmail_client.Flow") as flow_cls:
        flow = MagicMock()
        creds = MagicMock()
        creds.refresh_token = "rt-xyz"
        creds.client_id = "cid"
        creds.client_secret = "secret"
        creds.token_uri = "https://oauth2.googleapis.com/token"
        creds.scopes = ["https://www.googleapis.com/auth/gmail.send"]
        flow.credentials = creds
        flow_cls.from_client_secrets_file.return_value = flow

        exchange_code_for_token(
            client_secrets_path=client,
            redirect_uri="http://localhost:5051/api/oauth/callback",
            authorization_response_url="http://localhost:5051/api/oauth/callback?code=abc&state=s",
            token_path=token_path,
        )
    assert token_path.exists()
    data = json.loads(token_path.read_text())
    assert data["refresh_token"] == "rt-xyz"


# ---------- send ----------

def _saved_token(tmp_path: Path) -> Path:
    p = tmp_path / "token.json"
    save_token(p, {
        "refresh_token": "rt", "client_id": "cid", "client_secret": "secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/gmail.send"],
    })
    return p


def test_send_email_builds_mime_with_subject_and_body(tmp_path: Path) -> None:
    token = _saved_token(tmp_path)
    with patch("pipeline.gmail_client.build") as build_mock, \
         patch("pipeline.gmail_client.Credentials") as creds_cls:
        service = MagicMock()
        users = MagicMock()
        messages = MagicMock()
        send = MagicMock()
        execute = MagicMock(return_value={"id": "msg-123", "threadId": "thread-1"})
        send.execute = execute
        messages.send.return_value = send
        users.messages.return_value = messages
        service.users.return_value = users
        build_mock.return_value = service
        creds_cls.from_authorized_user_info.return_value = MagicMock()

        result = send_email(
            token_path=token,
            sender="me@example.com",
            to="them@example.com",
            subject="Hi there",
            body="Hello\nworld",
        )
    assert result == {"id": "msg-123", "threadId": "thread-1"}

    # Verify the message body the API was called with — decode the raw MIME.
    args, kwargs = messages.send.call_args
    raw_b64 = kwargs["body"]["raw"]
    raw = base64.urlsafe_b64decode(raw_b64).decode()
    assert "Subject: Hi there" in raw
    assert "From: me@example.com" in raw
    assert "To: them@example.com" in raw
    assert "Hello\nworld" in raw


def test_send_email_raises_when_disconnected(tmp_path: Path) -> None:
    with pytest.raises(GmailNotConnectedError):
        send_email(
            token_path=tmp_path / "nope.json",
            sender="me@example.com", to="t@example.com",
            subject="x", body="y",
        )
```

Run: `.venv/bin/python -m pytest tests/test_pipeline_gmail_client.py -v`

Expected: all fail (module not implemented).

- [ ] **Step 2: Implement pipeline/gmail_client.py**

Create:

```python
"""Gmail OAuth flow + send helpers.

Single-user, web-application OAuth flow with redirect URI
http://localhost:5051/api/oauth/callback. Refresh-token persisted to a JSON file
(default data/gmail_token.json, gitignored).

Public surface:
  build_authorization_url(client_secrets_path, redirect_uri) -> (url, state)
  exchange_code_for_token(...) -> None      # writes token file
  load_credentials(token_path) -> Credentials
  is_connected(token_path) -> bool
  send_email(token_path, sender, to, subject, body) -> {id, threadId}
"""
from __future__ import annotations
import base64
import json
import os
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build


SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


class GmailNotConnectedError(RuntimeError):
    """Raised when Gmail OAuth hasn't been completed (no token file yet)."""


def is_connected(token_path: Path) -> bool:
    return Path(token_path).exists()


def save_token(token_path: Path, info: dict[str, Any]) -> None:
    """Persist credential info to disk with owner-only permissions."""
    path = Path(token_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, indent=2))
    # Restrict permissions: read/write owner only. No-op on Windows.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_credentials(token_path: Path) -> Credentials:
    """Load credentials from disk; refresh access token if needed."""
    path = Path(token_path)
    if not path.exists():
        raise GmailNotConnectedError(
            f"Gmail not connected — no token at {path}. Visit /api/oauth/start."
        )
    info = json.loads(path.read_text())
    creds = Credentials.from_authorized_user_info(info, scopes=SCOPES)
    # Library refreshes automatically on API calls, but the explicit refresh
    # here means we surface auth errors before constructing the message.
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Persist any refreshed token data back to disk.
        save_token(path, json.loads(creds.to_json()))
    return creds


def build_authorization_url(
    *, client_secrets_path: Path, redirect_uri: str
) -> tuple[str, str]:
    """Step 1 of OAuth: return (authorization_url, state). Caller redirects."""
    flow = Flow.from_client_secrets_file(
        str(client_secrets_path), scopes=SCOPES, redirect_uri=redirect_uri,
    )
    # access_type=offline → we get a refresh_token on the first authorization.
    # prompt=consent → forces the consent screen even if we re-authorize, so
    # Google always gives us a refresh_token (otherwise it's only included
    # the very first time the user grants access).
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    return url, state


def exchange_code_for_token(
    *,
    client_secrets_path: Path,
    redirect_uri: str,
    authorization_response_url: str,
    token_path: Path,
) -> None:
    """Step 2 of OAuth: exchange the authorization code for tokens and persist."""
    flow = Flow.from_client_secrets_file(
        str(client_secrets_path), scopes=SCOPES, redirect_uri=redirect_uri,
    )
    flow.fetch_token(authorization_response=authorization_response_url)
    creds = flow.credentials
    save_token(token_path, {
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "token_uri": creds.token_uri,
        "scopes": list(creds.scopes or SCOPES),
    })


def send_email(
    *,
    token_path: Path,
    sender: str,
    to: str,
    subject: str,
    body: str,
) -> dict[str, str]:
    """Send a plain-text email via Gmail API. Returns {id, threadId}."""
    creds = load_credentials(token_path)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    sent = (
        service.users().messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
    return {"id": sent.get("id", ""), "threadId": sent.get("threadId", "")}
```

Run: `.venv/bin/python -m pytest tests/test_pipeline_gmail_client.py -v`

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add pipeline/gmail_client.py tests/test_pipeline_gmail_client.py
git commit -m "feat(outreach): Gmail OAuth flow + send helpers"
```

---

## Task 4: Backend routes — read endpoint + write endpoints (gated)

**Files:**
- Modify: `chicago-pipeline/webapp/app.py`
- Modify: `chicago-pipeline/webapp/__main__.py`
- Modify: `chicago-pipeline/webapp/routes.py`
- Create: `chicago-pipeline/tests/test_webapp_outreach_routes.py`

- [ ] **Step 1: Wire Gmail/template config into the app factory**

Modify `webapp/app.py` — currently:

```python
def create_app(db_path: Path, feature_outreach: bool = False,
               scoring_yaml_path: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path
    app.config["FEATURE_OUTREACH"] = feature_outreach
    app.config["SCORING_YAML_PATH"] = scoring_yaml_path
    ...
```

Add new config keys. Replace the constructor body's config block with:

```python
def create_app(
    db_path: Path,
    feature_outreach: bool = False,
    scoring_yaml_path: Path | None = None,
    outreach_templates_path: Path | None = None,
    gmail_client_secrets_path: Path | None = None,
    gmail_token_path: Path | None = None,
    gmail_sender_address: str | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path
    app.config["FEATURE_OUTREACH"] = feature_outreach
    app.config["SCORING_YAML_PATH"] = scoring_yaml_path
    app.config["OUTREACH_TEMPLATES_PATH"] = outreach_templates_path or (
        Path(__file__).resolve().parent.parent / "config" / "outreach_templates.yaml"
    )
    app.config["GMAIL_CLIENT_SECRETS_PATH"] = gmail_client_secrets_path or Path(
        "data/gmail_oauth_client.json"
    )
    app.config["GMAIL_TOKEN_PATH"] = gmail_token_path or Path("data/gmail_token.json")
    app.config["GMAIL_SENDER_ADDRESS"] = gmail_sender_address or ""
```

(Keep the rest of `create_app` exactly as-is.)

- [ ] **Step 2: Pass through from `__main__.py`**

Modify `webapp/__main__.py`. Replace the existing function body with:

```python
def main() -> None:
    parser = argparse.ArgumentParser(description="Chicago Pipeline Review UI")
    parser.add_argument("--db", type=Path, default=Path("data/smoke.db"),
                        help="Path to SQLite database (default: data/smoke.db)")
    parser.add_argument("--scoring-yaml", type=Path, default=None,
                        help="Path to scoring YAML for the score-breakdown panel "
                             "(default: config/scoring.yaml relative to project root)")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--outreach", action="store_true",
                        help="Enable outreach UI + write endpoints (local only)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable Flask debug mode (exposes Werkzeug debugger; localhost only)")
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"Database not found: {args.db}")

    # Outreach config reads from env so a developer can override paths without
    # touching code. All env vars are optional; defaults are baked into create_app.
    import os
    gmail_client = os.environ.get("GMAIL_OAUTH_CLIENT_PATH")
    gmail_token = os.environ.get("GMAIL_TOKEN_PATH")
    gmail_sender = os.environ.get("GMAIL_SENDER_ADDRESS")

    app = create_app(
        db_path=args.db, feature_outreach=args.outreach,
        scoring_yaml_path=args.scoring_yaml,
        gmail_client_secrets_path=Path(gmail_client) if gmail_client else None,
        gmail_token_path=Path(gmail_token) if gmail_token else None,
        gmail_sender_address=gmail_sender,
    )
    app.run(host="127.0.0.1", port=args.port, debug=args.debug)
```

- [ ] **Step 3: Write failing tests for outreach routes**

Create `tests/test_webapp_outreach_routes.py`:

```python
"""Tests for the outreach read/write endpoints.

Routes only exist when FEATURE_OUTREACH is true (Railway runs with it off,
so these endpoints return 404 in prod). Gmail API is mocked end-to-end.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.db import init_db
from webapp.app import create_app


@pytest.fixture
def outreach_db_path(tmp_path: Path) -> Path:
    """A fresh DB with the full schema and one seeded parcel. Named to avoid
    shadowing the global db_path fixture in tests/conftest.py, which seeds
    nothing."""
    path = tmp_path / "outreach.db"
    init_db(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO parcels (pin, owner_name, address, score, stage) "
        "VALUES (?, ?, ?, ?, ?)",
        ("14210010010000", "JOHN SMITH", "123 W Main St", 82.5, "scored"),
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def templates_path(tmp_path: Path) -> Path:
    p = tmp_path / "templates.yaml"
    p.write_text(
        "templates:\n"
        "  - name: t1\n"
        "    label: First\n"
        "    subject: \"Hi {{owner_first_name}}\"\n"
        "    body: \"About {{address}}\"\n"
        "defaults:\n"
        "  my_name: Hunter\n"
    )
    return p


@pytest.fixture
def app_on(outreach_db_path: Path, templates_path: Path, tmp_path: Path):
    return create_app(
        db_path=outreach_db_path, feature_outreach=True,
        outreach_templates_path=templates_path,
        gmail_client_secrets_path=tmp_path / "client.json",
        gmail_token_path=tmp_path / "token.json",
        gmail_sender_address="me@example.com",
    )


@pytest.fixture
def app_off(outreach_db_path: Path):
    return create_app(db_path=outreach_db_path, feature_outreach=False)


# ---------- feature flag gates the routes entirely ----------

def test_outreach_routes_return_404_when_flag_off(app_off) -> None:
    client = app_off.test_client()
    assert client.get("/api/parcels/14210010010000/outreach").status_code == 404
    assert client.post("/api/contacts/upsert").status_code == 404
    assert client.post("/api/outreach/send").status_code == 404
    assert client.get("/api/oauth/start").status_code == 404


# ---------- GET /api/parcels/<pin>/outreach ----------

def test_get_outreach_returns_empty_lists_for_new_parcel(app_on) -> None:
    client = app_on.test_client()
    resp = client.get("/api/parcels/14210010010000/outreach")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["pin"] == "14210010010000"
    assert data["contact"] is None
    assert data["outreach"] == []
    assert data["gmail_connected"] is False


def test_get_outreach_returns_404_for_unknown_pin(app_on) -> None:
    client = app_on.test_client()
    assert client.get("/api/parcels/99999999999999/outreach").status_code == 404


# ---------- POST /api/contacts/upsert ----------

def test_upsert_contact_creates_row(app_on) -> None:
    client = app_on.test_client()
    resp = client.post(
        "/api/contacts/upsert",
        json={"pin": "14210010010000", "email": "js@example.com"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["contact"]["email"] == "js@example.com"


def test_upsert_contact_rejects_bad_email(app_on) -> None:
    client = app_on.test_client()
    resp = client.post(
        "/api/contacts/upsert",
        json={"pin": "14210010010000", "email": "not-an-email"},
    )
    assert resp.status_code == 400


def test_upsert_contact_rejects_bad_pin(app_on) -> None:
    client = app_on.test_client()
    resp = client.post(
        "/api/contacts/upsert",
        json={"pin": "short", "email": "a@b.com"},
    )
    assert resp.status_code == 400


# ---------- GET /api/outreach/templates ----------

def test_get_templates_returns_list(app_on) -> None:
    client = app_on.test_client()
    resp = client.get("/api/outreach/templates")
    assert resp.status_code == 200
    data = resp.get_json()
    assert any(t["name"] == "t1" for t in data["templates"])


def test_get_templates_includes_rendered_preview_for_pin(app_on) -> None:
    """When ?pin= is supplied, templates come pre-rendered with that parcel's
    merge variables — that's what feeds the compose modal."""
    client = app_on.test_client()
    resp = client.get("/api/outreach/templates?pin=14210010010000")
    assert resp.status_code == 200
    data = resp.get_json()
    t = next(t for t in data["templates"] if t["name"] == "t1")
    # owner_first_name "John" comes from owner_name "JOHN SMITH"
    assert t["rendered_subject"] == "Hi John"
    assert t["rendered_body"] == "About 123 W Main St"


# ---------- POST /api/outreach/send ----------

def test_send_outreach_calls_gmail_and_records_row(app_on) -> None:
    client = app_on.test_client()
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "msg-1", "threadId": "thr-1"}
        resp = client.post(
            "/api/outreach/send",
            json={
                "pin": "14210010010000",
                "to": "js@example.com",
                "subject": "Hi",
                "body": "Body",
            },
        )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["outreach_id"] >= 1
    assert data["gmail_message_id"] == "msg-1"
    # Send was called with sanitized subject and the right addresses
    assert send_mock.call_count == 1
    kwargs = send_mock.call_args.kwargs
    assert kwargs["sender"] == "me@example.com"
    assert kwargs["to"] == "js@example.com"
    assert kwargs["subject"] == "Hi"


def test_send_outreach_flips_stage_to_outreach(app_on, outreach_db_path: Path) -> None:
    client = app_on.test_client()
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "m", "threadId": "t"}
        client.post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "s", "body": "b",
        })
    conn = sqlite3.connect(outreach_db_path)
    stage = conn.execute(
        "SELECT stage FROM parcels WHERE pin = ?", ("14210010010000",)
    ).fetchone()[0]
    conn.close()
    assert stage == "outreach"


def test_send_outreach_sanitizes_subject(app_on) -> None:
    client = app_on.test_client()
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "m", "threadId": "t"}
        client.post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "Hi\r\nBcc: evil@x.com", "body": "b",
        })
    assert send_mock.call_args.kwargs["subject"] == "HiBcc: evil@x.com"


def test_send_outreach_surfaces_gmail_error(app_on) -> None:
    from pipeline.gmail_client import GmailNotConnectedError
    client = app_on.test_client()
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.side_effect = GmailNotConnectedError("nope")
        resp = client.post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "s", "body": "b",
        })
    assert resp.status_code == 503
    assert "not connected" in resp.get_data(as_text=True).lower()


def test_send_outreach_rejects_missing_fields(app_on) -> None:
    client = app_on.test_client()
    resp = client.post("/api/outreach/send", json={
        "pin": "14210010010000", "to": "x@y.com", "subject": "s",  # body missing
    })
    assert resp.status_code == 400


# ---------- POST /api/outreach/<id>/mark-replied ----------

def test_mark_replied_updates_row(app_on, outreach_db_path: Path) -> None:
    client = app_on.test_client()
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "m", "threadId": "t"}
        resp = client.post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "s", "body": "b",
        })
    oid = resp.get_json()["outreach_id"]

    resp = client.post(f"/api/outreach/{oid}/mark-replied", json={
        "response_type": "responded"
    })
    assert resp.status_code == 200
    conn = sqlite3.connect(outreach_db_path)
    row = conn.execute(
        "SELECT response_date, response_type FROM outreach WHERE outreach_id = ?",
        (oid,),
    ).fetchone()
    conn.close()
    assert row[0] is not None
    assert row[1] == "responded"


# ---------- POST /api/parcels/<pin>/stage ----------

def test_set_stage_updates_parcel(app_on, outreach_db_path: Path) -> None:
    client = app_on.test_client()
    resp = client.post("/api/parcels/14210010010000/stage",
                       json={"stage": "dead"})
    assert resp.status_code == 200
    conn = sqlite3.connect(outreach_db_path)
    stage = conn.execute(
        "SELECT stage FROM parcels WHERE pin = ?", ("14210010010000",)
    ).fetchone()[0]
    conn.close()
    assert stage == "dead"


def test_set_stage_rejects_bad_value(app_on) -> None:
    client = app_on.test_client()
    resp = client.post("/api/parcels/14210010010000/stage",
                       json={"stage": "bogus"})
    assert resp.status_code == 400


# ---------- OAuth routes ----------

def test_oauth_start_redirects_to_google(app_on, tmp_path: Path) -> None:
    """OAuth start kicks the user over to Google's consent page."""
    # The client_secrets file needs to exist for the Flow library to read it,
    # but we mock Flow itself.
    (tmp_path / "client.json").write_text(json.dumps({
        "web": {
            "client_id": "cid", "client_secret": "s",
            "redirect_uris": ["http://localhost:5051/api/oauth/callback"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }))
    with patch("pipeline.gmail_client.build_authorization_url") as ba:
        ba.return_value = ("https://accounts.google.com/auth?x=1", "state-abc")
        client = app_on.test_client()
        resp = client.get("/api/oauth/start")
    assert resp.status_code == 302
    assert resp.location == "https://accounts.google.com/auth?x=1"


def test_oauth_start_404s_when_client_secrets_missing(app_on) -> None:
    """If the user hasn't placed the Google client JSON, we tell them so."""
    client = app_on.test_client()
    resp = client.get("/api/oauth/start")
    assert resp.status_code == 503
    assert "client" in resp.get_data(as_text=True).lower()
```

Run: `.venv/bin/python -m pytest tests/test_webapp_outreach_routes.py -v`

Expected: all fail (routes don't exist yet).

- [ ] **Step 4: Register outreach routes in webapp/routes.py**

Open `webapp/routes.py`. At the top of the file, add new imports after the existing ones:

```python
import re
from datetime import datetime, timezone
from pipeline import outreach as outreach_module, gmail_client
```

Inside `register(app)`, AFTER the existing routes (somewhere after `api_parcel_detail`), add a new block. The structure: ALL outreach routes wrapped in `if app.config["FEATURE_OUTREACH"]:` so they only get registered when the flag is on.

```python
    # ============================================================
    # Outreach (Plan 4) — registered only when FEATURE_OUTREACH is on
    # ============================================================
    if app.config["FEATURE_OUTREACH"]:

        EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
        ALLOWED_STAGES = {"scored", "outreach", "responded", "introduced", "dead"}
        ALLOWED_RESPONSE_TYPES = {"responded", "not_interested", "wrong_owner", "other"}

        def _now_iso() -> str:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _load_outreach_config() -> dict:
            return outreach_module.load_templates(
                Path(app.config["OUTREACH_TEMPLATES_PATH"])
            )

        def _parcel_or_404(conn, pin: str):
            if not pin.isdigit() or len(pin) != 14:
                abort(404)
            row = conn.execute(
                "SELECT * FROM parcels WHERE pin = ?", (pin,)
            ).fetchone()
            if row is None:
                abort(404)
            return dict(row)

        @app.get("/api/parcels/<pin>/outreach")
        def api_parcel_outreach(pin: str):
            with closing(_conn()) as conn:
                _parcel_or_404(conn, pin)
                contact = conn.execute(
                    "SELECT * FROM contacts WHERE pin = ? LIMIT 1", (pin,)
                ).fetchone()
                outreach_rows = outreach_module.list_outreach_for_parcel(conn, pin)
            return jsonify({
                "pin": pin,
                "contact": dict(contact) if contact else None,
                "outreach": [dict(r) for r in outreach_rows],
                "gmail_connected": gmail_client.is_connected(
                    Path(app.config["GMAIL_TOKEN_PATH"])
                ),
                "sender_address": app.config.get("GMAIL_SENDER_ADDRESS") or "",
            })

        @app.get("/api/outreach/templates")
        def api_outreach_templates():
            cfg = _load_outreach_config()
            pin = request.args.get("pin")
            templates = []
            ctx = {}
            if pin and pin.isdigit() and len(pin) == 14:
                with closing(_conn()) as conn:
                    row = conn.execute(
                        "SELECT * FROM parcels WHERE pin = ?", (pin,)
                    ).fetchone()
                    if row is not None:
                        ctx = outreach_module.parcel_context(dict(row), cfg["defaults"])
            for name, tpl in cfg["templates"].items():
                templates.append({
                    "name": name,
                    "label": tpl.get("label", name),
                    "subject": tpl.get("subject", ""),
                    "body": tpl.get("body", ""),
                    "rendered_subject": outreach_module.render_template(
                        tpl.get("subject", ""), ctx
                    ) if ctx else None,
                    "rendered_body": outreach_module.render_template(
                        tpl.get("body", ""), ctx
                    ) if ctx else None,
                })
            return jsonify({"templates": templates})

        @app.post("/api/contacts/upsert")
        def api_contacts_upsert():
            data = request.get_json(silent=True) or {}
            pin = data.get("pin", "")
            email = data.get("email")
            if not pin.isdigit() or len(pin) != 14:
                abort(400, "invalid pin")
            if email is not None and not EMAIL_RE.match(email):
                abort(400, "invalid email")
            with closing(_conn()) as conn:
                _parcel_or_404(conn, pin)
                cid = outreach_module.upsert_contact(
                    conn, pin=pin,
                    email=email,
                    name=data.get("name"),
                    phone=data.get("phone"),
                    role=data.get("role"),
                    source=data.get("source", "manual"),
                )
                row = conn.execute(
                    "SELECT * FROM contacts WHERE contact_id = ?", (cid,)
                ).fetchone()
            return jsonify({"contact": dict(row)})

        @app.post("/api/outreach/send")
        def api_outreach_send():
            data = request.get_json(silent=True) or {}
            pin = data.get("pin", "")
            to = data.get("to", "")
            subject = data.get("subject")
            body = data.get("body")
            if not pin.isdigit() or len(pin) != 14:
                abort(400, "invalid pin")
            if not EMAIL_RE.match(to):
                abort(400, "invalid recipient email")
            if not subject or body is None:
                abort(400, "subject and body are required")

            subject = outreach_module.sanitize_subject(subject)
            sender = app.config.get("GMAIL_SENDER_ADDRESS") or ""
            if not sender:
                abort(503, "GMAIL_SENDER_ADDRESS is not set")

            with closing(_conn()) as conn:
                _parcel_or_404(conn, pin)
                cid = outreach_module.upsert_contact(
                    conn, pin=pin, email=to, source="manual"
                )

                try:
                    result = gmail_client.send_email(
                        token_path=Path(app.config["GMAIL_TOKEN_PATH"]),
                        sender=sender, to=to,
                        subject=subject, body=body,
                    )
                except gmail_client.GmailNotConnectedError as e:
                    abort(503, f"Gmail not connected: {e}")

                oid = outreach_module.create_outreach_record(
                    conn, pin=pin, contact_id=cid,
                    channel="email", subject=subject, body=body,
                    sent_date=_now_iso(),
                )
                # Persist the Gmail message id in `notes` (cheap; avoids a
                # schema change to add a dedicated column).
                conn.execute(
                    "UPDATE outreach SET notes = ? WHERE outreach_id = ?",
                    (f"gmail_message_id={result.get('id','')}", oid),
                )
                # Auto-transition: scored → outreach on first successful send.
                conn.execute(
                    "UPDATE parcels SET stage = 'outreach' "
                    "WHERE pin = ? AND (stage IS NULL OR stage = 'scored')",
                    (pin,),
                )
                conn.commit()

            return jsonify({
                "outreach_id": oid,
                "gmail_message_id": result.get("id", ""),
                "gmail_thread_id": result.get("threadId", ""),
            })

        @app.post("/api/outreach/<int:outreach_id>/mark-replied")
        def api_outreach_mark_replied(outreach_id: int):
            data = request.get_json(silent=True) or {}
            response_type = data.get("response_type", "responded")
            if response_type not in ALLOWED_RESPONSE_TYPES:
                abort(400, "invalid response_type")
            with closing(_conn()) as conn:
                row = conn.execute(
                    "SELECT outreach_id, pin FROM outreach WHERE outreach_id = ?",
                    (outreach_id,),
                ).fetchone()
                if row is None:
                    abort(404)
                outreach_module.mark_replied(
                    conn, outreach_id,
                    response_date=_now_iso(),
                    response_type=response_type,
                )
                # If this was the first reply, also bump parcel stage to "responded".
                conn.execute(
                    "UPDATE parcels SET stage = 'responded' "
                    "WHERE pin = ? AND stage = 'outreach'",
                    (row["pin"],),
                )
                conn.commit()
            return jsonify({"ok": True})

        @app.post("/api/parcels/<pin>/stage")
        def api_parcel_set_stage(pin: str):
            data = request.get_json(silent=True) or {}
            stage = data.get("stage")
            if stage not in ALLOWED_STAGES:
                abort(400, "invalid stage")
            with closing(_conn()) as conn:
                _parcel_or_404(conn, pin)
                conn.execute(
                    "UPDATE parcels SET stage = ? WHERE pin = ?", (stage, pin)
                )
                conn.commit()
            return jsonify({"ok": True, "stage": stage})

        # ----- OAuth -----

        @app.get("/api/oauth/start")
        def api_oauth_start():
            client_path = Path(app.config["GMAIL_CLIENT_SECRETS_PATH"])
            if not client_path.exists():
                abort(503,
                      f"Gmail client secrets not found at {client_path}. "
                      "Download from Google Cloud Console and save the file there.")
            redirect_uri = url_for("api_oauth_callback", _external=True)
            url, _state = gmail_client.build_authorization_url(
                client_secrets_path=client_path, redirect_uri=redirect_uri,
            )
            return redirect(url)

        @app.get("/api/oauth/callback")
        def api_oauth_callback():
            client_path = Path(app.config["GMAIL_CLIENT_SECRETS_PATH"])
            if not client_path.exists():
                abort(503, "Gmail client secrets not found")
            redirect_uri = url_for("api_oauth_callback", _external=True)
            try:
                gmail_client.exchange_code_for_token(
                    client_secrets_path=client_path,
                    redirect_uri=redirect_uri,
                    authorization_response_url=request.url,
                    token_path=Path(app.config["GMAIL_TOKEN_PATH"]),
                )
            except Exception as e:
                return f"OAuth callback failed: {e}", 500
            # Send the user back to the main UI.
            return redirect(url_for("index"))
```

At the top of `routes.py`, the existing imports need `redirect, url_for` added to the Flask import line:

```python
from flask import Flask, abort, current_app, jsonify, redirect, render_template, request, url_for
```

Run: `.venv/bin/python -m pytest tests/test_webapp_outreach_routes.py -v`

Expected: all pass. (If any fail, fix in place before committing.)

- [ ] **Step 5: Run the full test suite to make sure nothing else broke**

Run: `.venv/bin/python -m pytest -q`

Expected: 217 + new tests pass (no regressions in existing routes / queries).

- [ ] **Step 6: Commit**

```bash
git add webapp/app.py webapp/__main__.py webapp/routes.py \
        tests/test_webapp_outreach_routes.py
git commit -m "feat(outreach): backend routes for contacts, send, stage transitions (gated)"
```

---

## Task 5: Frontend — detail panel sections (contact + outreach history)

**Files:**
- Create: `chicago-pipeline/webapp/static/js/outreach.js`
- Modify: `chicago-pipeline/webapp/static/js/detail.js`
- Modify: `chicago-pipeline/webapp/templates/index.html`
- Modify: `chicago-pipeline/webapp/static/css/style.css`

- [ ] **Step 1: Create webapp/static/js/outreach.js with the section renderers**

The new module exports two functions: `renderOutreachSections(parcel, panel)` which appends a contact section + history section to the detail panel; and `openComposeModal(parcel, contact)` which is wired up in Task 6.

```javascript
// Outreach detail-panel sections + compose modal logic.
// Loaded only when FEATURE_OUTREACH is true.

(function () {
  'use strict';

  // Helpers borrowed from detail.js — keep a small inline copy to avoid
  // making detail.js export them. If detail.js ever exposes a real namespace
  // we should reuse from there.
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtDate(iso) {
    if (!iso) return '—';
    // Sent dates are ISO 8601 UTC. Show local-time short form.
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
        hour: 'numeric', minute: '2-digit',
      });
    } catch (_) { return iso; }
  }

  async function fetchOutreach(pin) {
    const resp = await fetch(`/api/parcels/${encodeURIComponent(pin)}/outreach`);
    if (!resp.ok) throw new Error(`fetch outreach failed: ${resp.status}`);
    return resp.json();
  }

  async function upsertContact(pin, fields) {
    const resp = await fetch('/api/contacts/upsert', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin, ...fields }),
    });
    if (!resp.ok) throw new Error(await resp.text() || `HTTP ${resp.status}`);
    return resp.json();
  }

  async function markReplied(outreachId, responseType = 'responded') {
    const resp = await fetch(`/api/outreach/${outreachId}/mark-replied`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ response_type: responseType }),
    });
    if (!resp.ok) throw new Error(await resp.text() || `HTTP ${resp.status}`);
    return resp.json();
  }

  async function setStage(pin, stage) {
    const resp = await fetch(`/api/parcels/${encodeURIComponent(pin)}/stage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stage }),
    });
    if (!resp.ok) throw new Error(await resp.text() || `HTTP ${resp.status}`);
    return resp.json();
  }

  // ---------- Contact section ----------

  function renderContactSection(parcel, data) {
    const el = document.createElement('div');
    el.className = 'detail-section';
    const contact = data.contact || {};
    const email = contact.email || '';

    el.innerHTML = `
      <h3>Contact</h3>
      <div class="detail-grid" style="grid-template-columns: 1fr;">
        <div class="detail-item">
          <div class="label">Email</div>
          <div class="value">
            <input type="email" id="outreach-email-input"
                   class="outreach-input"
                   placeholder="owner@example.com"
                   value="${escapeHtml(email)}" />
            <span class="outreach-email-status" id="outreach-email-status"></span>
          </div>
        </div>
        <div class="detail-item">
          <div class="label">Owner (Assessor)</div>
          <div class="value">${escapeHtml(parcel.owner_name || '—')}</div>
        </div>
        <div class="detail-item">
          <div class="label">Mail address</div>
          <div class="value">${escapeHtml(parcel.mail_address || '—')}</div>
        </div>
        <div class="detail-item" style="display:flex; gap:8px; align-items:center;">
          <button type="button" class="btn btn-primary" id="outreach-compose-btn"
                  ${email ? '' : 'disabled'}
                  title="${email ? '' : 'Add an email above first'}">
            Compose email…
          </button>
          <span class="outreach-gmail-status" style="font-size:11px; color:#8b949e;">
            ${data.gmail_connected
              ? 'Gmail connected'
              : '<a href="/api/oauth/start">Connect Gmail</a>'}
          </span>
        </div>
      </div>
    `;

    // Wire the email input — save on blur if changed.
    const input = el.querySelector('#outreach-email-input');
    const status = el.querySelector('#outreach-email-status');
    let original = email;
    input.addEventListener('blur', async () => {
      const v = input.value.trim();
      if (v === original) return;
      if (v && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v)) {
        status.textContent = 'invalid email';
        status.style.color = '#f85149';
        return;
      }
      status.textContent = 'saving…';
      status.style.color = '#8b949e';
      try {
        await upsertContact(parcel.pin, { email: v || null });
        status.textContent = 'saved';
        status.style.color = '#3fb950';
        original = v;
        // Re-render the panel to re-enable the Compose button.
        window.dispatchEvent(new CustomEvent('outreach:refresh',
                                              { detail: { pin: parcel.pin } }));
      } catch (e) {
        status.textContent = 'error';
        status.style.color = '#f85149';
      }
    });

    // Compose button → open modal (wired up in Task 6).
    const btn = el.querySelector('#outreach-compose-btn');
    btn.addEventListener('click', () => {
      if (typeof window.__outreachOpenCompose === 'function') {
        window.__outreachOpenCompose(parcel, data.contact, data.sender_address);
      }
    });
    return el;
  }

  // ---------- History section ----------

  function renderHistorySection(parcel, data) {
    const el = document.createElement('div');
    el.className = 'detail-section';
    const rows = data.outreach || [];
    if (rows.length === 0) {
      el.innerHTML = '<h3>Outreach History</h3><div style="font-size:12px; color:#8b949e;">No outreach yet.</div>';
      return el;
    }
    const html = rows.map(r => {
      const replied = r.response_date
        ? `<span style="color:#3fb950; margin-left:8px;">✓ replied ${escapeHtml(fmtDate(r.response_date))}</span>`
        : `<button class="btn btn-sm" data-mark-replied="${r.outreach_id}" style="margin-left:8px;">Mark replied</button>`;
      const body = (r.final_body || r.draft_body || '').trim();
      return `
        <div class="outreach-item">
          <div class="outreach-item-head">
            <strong>${escapeHtml(r.draft_subject || '(no subject)')}</strong>
            <span style="color:#8b949e; font-size:11px; margin-left:8px;">
              ${escapeHtml(r.channel || 'email')} · ${escapeHtml(fmtDate(r.sent_date))}
            </span>
            ${replied}
          </div>
          <details>
            <summary style="font-size:11px; color:#8b949e; cursor:pointer;">Show body</summary>
            <pre style="white-space:pre-wrap; font-size:12px; padding:6px 0; color:#c9d1d9;">${escapeHtml(body)}</pre>
          </details>
        </div>
      `;
    }).join('');
    el.innerHTML = `<h3>Outreach History</h3>${html}`;
    // Wire mark-replied buttons.
    el.querySelectorAll('[data-mark-replied]').forEach(btn => {
      btn.addEventListener('click', async () => {
        btn.disabled = true; btn.textContent = '…';
        try {
          await markReplied(parseInt(btn.dataset.markReplied, 10));
          window.dispatchEvent(new CustomEvent('outreach:refresh',
                                                { detail: { pin: parcel.pin } }));
        } catch (e) {
          btn.disabled = false; btn.textContent = 'Mark replied';
        }
      });
    });
    return el;
  }

  // ---------- Stage section ----------

  function renderStageSection(parcel) {
    const el = document.createElement('div');
    el.className = 'detail-section';
    const stages = ['scored', 'outreach', 'responded', 'introduced', 'dead'];
    const cur = parcel.stage || 'scored';
    el.innerHTML = `
      <h3>Stage</h3>
      <div class="detail-grid" style="grid-template-columns: 1fr;">
        <div class="detail-item" style="display:flex; gap:8px; align-items:center;">
          <select id="outreach-stage-select" class="outreach-input">
            ${stages.map(s => `<option value="${s}"${s === cur ? ' selected' : ''}>${s}</option>`).join('')}
          </select>
          <span id="outreach-stage-status" style="font-size:11px; color:#8b949e;"></span>
        </div>
      </div>
    `;
    const sel = el.querySelector('#outreach-stage-select');
    const status = el.querySelector('#outreach-stage-status');
    sel.addEventListener('change', async () => {
      status.textContent = 'saving…'; status.style.color = '#8b949e';
      try {
        await setStage(parcel.pin, sel.value);
        status.textContent = 'saved'; status.style.color = '#3fb950';
      } catch (e) {
        status.textContent = 'error'; status.style.color = '#f85149';
      }
    });
    return el;
  }

  // ---------- Public API ----------

  async function renderOutreachSections(parcel, panel) {
    let data;
    try {
      data = await fetchOutreach(parcel.pin);
    } catch (_) {
      const err = document.createElement('div');
      err.className = 'detail-section';
      err.innerHTML = '<h3>Outreach</h3><div style="font-size:12px; color:#f85149;">Couldn’t load outreach data.</div>';
      panel.appendChild(err);
      return;
    }
    panel.appendChild(renderStageSection(parcel));
    panel.appendChild(renderContactSection(parcel, data));
    panel.appendChild(renderHistorySection(parcel, data));
  }

  // Re-render on the "outreach:refresh" custom event by re-selecting the
  // parcel. detail.js owns reloadDetail; we cooperate via the existing
  // parcelselect event.
  window.addEventListener('outreach:refresh', (e) => {
    const pin = e.detail && e.detail.pin;
    if (pin) {
      window.dispatchEvent(new CustomEvent('parcelselect', { detail: { pin } }));
    }
  });

  // Expose to detail.js
  window.__outreachRenderSections = renderOutreachSections;
})();
```

- [ ] **Step 2: Wire outreach.js into detail.js**

Modify `webapp/static/js/detail.js`. Replace the current outreach call in `renderDetail`:

```javascript
    if (window.FEATURE_OUTREACH) {
      panel.appendChild(sectionOutreachStub());
    }
```

with:

```javascript
    if (window.FEATURE_OUTREACH && typeof window.__outreachRenderSections === 'function') {
      window.__outreachRenderSections(p, panel);
    }
```

Then delete the `sectionOutreachStub()` function (lines around 551-559) — it's no longer used.

- [ ] **Step 3: Load outreach.js in index.html**

Modify `webapp/templates/index.html`. Find the script tags near the bottom and add `outreach.js` (only when `feature_outreach` is true):

```html
<script src="{{ url_for('static', filename='js/utils.js') }}"></script>
<script src="{{ url_for('static', filename='js/filters.js') }}"></script>
<script src="{{ url_for('static', filename='js/list.js') }}"></script>
<script src="{{ url_for('static', filename='js/map.js') }}"></script>
<script src="{{ url_for('static', filename='js/detail.js') }}"></script>
{% if feature_outreach %}
<script src="{{ url_for('static', filename='js/outreach.js') }}"></script>
{% endif %}
<script src="{{ url_for('static', filename='js/app.js') }}"></script>
```

- [ ] **Step 4: Add outreach CSS**

Append to `webapp/static/css/style.css`:

```css
/* ===== Outreach (Plan 4) ===== */

.outreach-input {
  background: #0d1117;
  color: #c9d1d9;
  border: 1px solid #30363d;
  border-radius: 4px;
  padding: 4px 8px;
  font-size: 12px;
  font-family: inherit;
}
.outreach-input:focus { outline: 1px solid #58a6ff; }

.outreach-email-status {
  font-size: 11px;
  margin-left: 8px;
}

.outreach-item {
  padding: 8px 0;
  border-bottom: 1px solid #30363d;
}
.outreach-item:last-child { border-bottom: none; }
.outreach-item-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  font-size: 12px;
  color: #c9d1d9;
}

.btn-sm {
  font-size: 11px;
  padding: 2px 8px;
}

/* Compose modal — added in Task 6 */
.outreach-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  z-index: 9000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.outreach-modal {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  width: min(640px, 90vw);
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  color: #c9d1d9;
}
.outreach-modal-head {
  padding: 12px 16px;
  border-bottom: 1px solid #30363d;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.outreach-modal-head h3 { margin: 0; font-size: 14px; }
.outreach-modal-body {
  padding: 12px 16px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.outreach-modal-body label {
  font-size: 11px;
  color: #8b949e;
}
.outreach-modal-body input,
.outreach-modal-body select,
.outreach-modal-body textarea {
  background: #0d1117;
  color: #c9d1d9;
  border: 1px solid #30363d;
  border-radius: 4px;
  padding: 6px 8px;
  font-size: 13px;
  font-family: inherit;
}
.outreach-modal-body textarea {
  min-height: 200px;
  resize: vertical;
}
.outreach-modal-foot {
  padding: 12px 16px;
  border-top: 1px solid #30363d;
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
.outreach-modal-error {
  color: #f85149;
  font-size: 12px;
  margin-right: auto;
  align-self: center;
}
```

- [ ] **Step 5: Manual verification (no automated test for raw DOM yet — Task 8 covers this in-browser)**

Skip ahead — we'll verify all four sections render in Task 8 once the modal is in place. For now, just confirm tests still pass.

Run: `.venv/bin/python -m pytest -q`

Expected: all existing tests pass; no new test failures.

- [ ] **Step 6: Commit**

```bash
git add webapp/static/js/outreach.js webapp/static/js/detail.js \
        webapp/templates/index.html webapp/static/css/style.css
git commit -m "feat(outreach): detail-panel sections — stage, contact, history"
```

---

## Task 6: Frontend — compose modal + send flow

**Files:**
- Modify: `chicago-pipeline/webapp/static/js/outreach.js`
- Modify: `chicago-pipeline/webapp/templates/index.html`

- [ ] **Step 1: Append compose modal logic to outreach.js**

In `webapp/static/js/outreach.js`, BEFORE the closing `})();`, add these functions inside the IIFE:

```javascript
  // ---------- Compose modal ----------

  async function fetchTemplates(pin) {
    const resp = await fetch(`/api/outreach/templates?pin=${encodeURIComponent(pin)}`);
    if (!resp.ok) throw new Error(`fetch templates failed: ${resp.status}`);
    return resp.json();
  }

  async function sendOutreach(payload) {
    const resp = await fetch('/api/outreach/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(text || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  function closeModal() {
    const m = document.getElementById('outreach-modal-root');
    if (m) m.remove();
  }

  async function openComposeModal(parcel, contact, senderAddress) {
    // Fetch templates with rendered preview for this pin.
    let tplResp;
    try { tplResp = await fetchTemplates(parcel.pin); }
    catch (_) {
      alert("Couldn't load outreach templates.");
      return;
    }
    const templates = tplResp.templates || [];
    if (templates.length === 0) {
      alert('No outreach templates configured. Edit config/outreach_templates.yaml.');
      return;
    }

    // Build modal DOM.
    const root = document.createElement('div');
    root.id = 'outreach-modal-root';
    root.className = 'outreach-modal-backdrop';
    root.innerHTML = `
      <div class="outreach-modal" role="dialog" aria-modal="true" aria-label="Compose email">
        <div class="outreach-modal-head">
          <h3>Compose email — ${escapeHtml(parcel.address || parcel.pin)}</h3>
          <button type="button" class="btn btn-sm" id="outreach-modal-close">Close</button>
        </div>
        <div class="outreach-modal-body">
          <div>
            <label for="cm-template">Template</label><br>
            <select id="cm-template">
              ${templates.map((t, i) => `<option value="${i}">${escapeHtml(t.label || t.name)}</option>`).join('')}
            </select>
          </div>
          <div>
            <label for="cm-from">From</label><br>
            <input type="text" id="cm-from" value="${escapeHtml(senderAddress || '')}" disabled />
          </div>
          <div>
            <label for="cm-to">To</label><br>
            <input type="email" id="cm-to" value="${escapeHtml(contact && contact.email || '')}" />
          </div>
          <div>
            <label for="cm-subject">Subject</label><br>
            <input type="text" id="cm-subject" value="" />
          </div>
          <div>
            <label for="cm-body">Body</label><br>
            <textarea id="cm-body"></textarea>
          </div>
        </div>
        <div class="outreach-modal-foot">
          <span class="outreach-modal-error" id="cm-error"></span>
          <button type="button" class="btn" id="cm-cancel">Cancel</button>
          <button type="button" class="btn btn-primary" id="cm-send">Send</button>
        </div>
      </div>
    `;
    document.body.appendChild(root);

    const subjectInput = root.querySelector('#cm-subject');
    const bodyInput = root.querySelector('#cm-body');
    const tplSelect = root.querySelector('#cm-template');
    const errSpan = root.querySelector('#cm-error');
    const sendBtn = root.querySelector('#cm-send');

    function applyTemplate(idx) {
      const t = templates[idx];
      if (!t) return;
      subjectInput.value = t.rendered_subject || t.subject || '';
      bodyInput.value = t.rendered_body || t.body || '';
    }
    applyTemplate(0);
    tplSelect.addEventListener('change', () => applyTemplate(parseInt(tplSelect.value, 10)));

    function onClose() { closeModal(); }
    root.querySelector('#outreach-modal-close').addEventListener('click', onClose);
    root.querySelector('#cm-cancel').addEventListener('click', onClose);
    // Click outside dialog to close.
    root.addEventListener('click', (e) => { if (e.target === root) onClose(); });
    document.addEventListener('keydown', function onKey(ev) {
      if (ev.key === 'Escape') {
        document.removeEventListener('keydown', onKey);
        closeModal();
      }
    });

    sendBtn.addEventListener('click', async () => {
      const to = root.querySelector('#cm-to').value.trim();
      const subject = subjectInput.value.trim();
      const body = bodyInput.value;
      errSpan.textContent = '';
      if (!to || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(to)) {
        errSpan.textContent = 'invalid recipient email'; return;
      }
      if (!subject) { errSpan.textContent = 'subject required'; return; }
      sendBtn.disabled = true; sendBtn.textContent = 'Sending…';
      try {
        await sendOutreach({ pin: parcel.pin, to, subject, body });
        closeModal();
        window.dispatchEvent(new CustomEvent('outreach:refresh',
                                              { detail: { pin: parcel.pin } }));
      } catch (e) {
        errSpan.textContent = e.message || 'send failed';
        sendBtn.disabled = false; sendBtn.textContent = 'Send';
      }
    });
  }

  // Wire the compose button trigger from renderContactSection.
  window.__outreachOpenCompose = openComposeModal;
```

- [ ] **Step 2: Run full test suite (sanity check on the JS-adjacent backend)**

Run: `.venv/bin/python -m pytest -q`

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add webapp/static/js/outreach.js
git commit -m "feat(outreach): compose-and-confirm modal with template picker"
```

---

## Task 7: Browser verification + Gmail OAuth walkthrough docs

**Files:**
- Create: `chicago-pipeline/.claude/launch.json`
- Modify: `chicago-pipeline/README.md`

- [ ] **Step 1: Create .claude/launch.json so the preview tools can drive the dev server**

Path: `chicago-pipeline/.claude/launch.json`

```json
{
  "version": "0.0.1",
  "configurations": [
    {
      "name": "webapp-outreach",
      "runtimeExecutable": ".venv/bin/python",
      "runtimeArgs": ["-m", "webapp", "--db", "data/full.alt.db", "--port", "5051", "--outreach"],
      "port": 5051
    }
  ]
}
```

- [ ] **Step 2: Start the dev server and verify smoke flow**

Run `preview_start` with name `webapp-outreach`.

Open `http://localhost:5051/` in the preview, log in, click any parcel. Verify:

1. The detail panel now shows three new sections: **Stage**, **Contact**, **Outreach History** ("No outreach yet.")
2. **Connect Gmail** link appears next to the Compose button when no token exists
3. Typing an email in the Contact section and clicking outside saves; status updates to "saved"
4. The Compose button becomes enabled once an email is present
5. Clicking Compose opens the modal with the template applied — owner first name appears in the subject

Capture a screenshot of the modal open. (We're NOT going to actually send a test email in CI — Gmail OAuth requires manual user consent. Document the next step instead.)

- [ ] **Step 3: Verify FEATURE_OUTREACH=false hides everything**

Run the app without `--outreach`:

```bash
.venv/bin/python -m webapp --db data/full.alt.db --port 5052
```

Visit `http://localhost:5052/`. Verify:

1. Detail panel does NOT show Stage, Contact, or Outreach History sections
2. `curl http://localhost:5052/api/parcels/<any-pin>/outreach` returns 404
3. `curl -X POST http://localhost:5052/api/contacts/upsert -d '{"pin":"...","email":"a@b.com"}' -H 'Content-Type: application/json'` returns 404

Kill the second server.

- [ ] **Step 4: Add an "Outreach (local-only)" section to README.md**

Append to `README.md`:

```markdown
## Outreach (Plan 4 — local only)

The outreach feature lets you send single-touch cold emails through your own
Gmail and track outreach + responses per parcel. It is **local-only by
design** — the code ships to Railway but is gated behind `FEATURE_OUTREACH`,
which is never set in production. Outreach rows live only in your local DB.

### Enabling locally

```bash
.venv/bin/python -m webapp --db data/full.alt.db --port 5051 --outreach
```

### One-time Gmail setup

1. Create a Google Cloud project at <https://console.cloud.google.com/>.
2. Enable the Gmail API for that project.
3. Configure the OAuth consent screen as "External" + "Testing" mode, and
   add your own Gmail address to the test users list.
4. Create an OAuth 2.0 Client ID — type **Web application**. Add
   `http://localhost:5051/api/oauth/callback` to the Authorized redirect URIs.
5. Download the JSON. Save it to `data/gmail_oauth_client.json`.
6. Copy `.env.example` to `.env` and set `GMAIL_SENDER_ADDRESS` to the
   Gmail address you'll send from.
7. Start the webapp with `--outreach`. In any parcel detail panel, click
   **Connect Gmail**. Approve the consent screen. You'll land back on the
   review UI. The status indicator next to the Compose button now reads
   "Gmail connected".

The refresh token is persisted to `data/gmail_token.json` (gitignored). Both
files are also in `.dockerignore` so they never ship in a container build.

### Editing email templates

Templates live in `config/outreach_templates.yaml`. Variables are written
`{{var}}` and substituted with the selected parcel's data. Missing
variables stay literal so you can spot what's not wired up.

### Before re-uploading the DB to R2

See [DEPLOY.md](DEPLOY.md#refreshing-the-production-db-on-r2) — always run
`scripts/sanitize_db_for_r2.py` to strip outreach/contacts/waves rows.
```

- [ ] **Step 5: Run the full test suite one more time**

Run: `.venv/bin/python -m pytest -q`

Expected: all pass.

- [ ] **Step 6: Final commit**

```bash
git add .claude/launch.json README.md
git commit -m "docs(outreach): README setup + dev-server launch config"
```

---

## Self-review (run after completing all tasks)

Before declaring this done:

1. **Run the full test suite locally**: `.venv/bin/python -m pytest -q` — must be green.
2. **Manual smoke flow** with the dev server + `--outreach`:
   - Pick a real parcel, enter your own email, click Compose
   - Send to yourself, verify the email arrives
   - Confirm the parcel's stage flipped to `outreach`
   - Reload the page, confirm the outreach row appears in history
   - Click "Mark replied", confirm status updates and stage moves to `responded`
3. **Sanitize roundtrip**: copy `data/full.alt.db` to `data/test_sanitize.db`, run a few outreach actions, then run `scripts/sanitize_db_for_r2.py data/test_sanitize.db data/test_clean.db` and verify outreach/contacts/waves rows are gone but parcels are intact. Delete the temp files.
4. **Flag-off check**: run the webapp without `--outreach` once more, confirm all outreach endpoints return 404 and no new sections appear in the detail panel.
5. **Commit log review**: `git log --oneline master..HEAD` should show ~7 commits, one per task, each self-contained.

Once green: push to master so Railway picks up the gated code (dormant on prod) and you can continue using outreach locally.
