# Outreach Cadence (Phase A) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the 7-touch cadence engine + Due Today UI + daily digest + manual-touch logging on top of the shipped single-touch outreach, all local-only (no Railway migration). Aligns with the spec at `docs/superpowers/specs/2026-05-15-outreach-cadence-design.md` (Phase A scope).

**Architecture:** Stateless cadence — Flask app computes "what's due" on every page load by reading `outreach.sent_date` + a YAML cadence config. Pure-function cadence engine in `pipeline/cadence.py` (4 functions, 3 pure + 1 DB orchestrator). New routes are gated by `FEATURE_OUTREACH` like the existing outreach block. A `python -m pipeline.due_digest` CLI emails a daily summary; wired to local launchd. UI gains a Due Today banner at the top, a sequence timeline in the detail panel, and channel-aware compose for phone + mail touches (manual mark-complete stubs; Lob deferred).

**Tech Stack:** Existing — Flask 3, vanilla JS, SQLite, PyYAML, pytest. The Gmail send path from the previous outreach plan is reused for the digest. No new dependencies.

---

## Spec → file map

| Spec section | New/modified file(s) |
|---|---|
| Cadence YAML config | Create: `config/outreach_cadence.yaml` |
| 6 new templates | Modify: `config/outreach_templates.yaml` |
| Schema: pause flag + unique index | Modify: `pipeline/db.py` |
| Cadence engine (pure + orchestrator) | Create: `pipeline/cadence.py`; Test: `tests/test_pipeline_cadence.py` |
| Outreach module: touch_number param | Modify: `pipeline/outreach.py`; Test: `tests/test_pipeline_outreach.py` |
| Read routes (GET due, GET config) | Modify: `webapp/routes.py`; Test: `tests/test_webapp_cadence_routes.py` |
| Write routes (log-manual-touch, pause) | Modify: `webapp/routes.py`; Test: same |
| Modified routes (send touch_number, GET outreach sequence block) | Modify: `webapp/routes.py`, `webapp/app.py`; Test: same + existing |
| Due Today banner UI | Modify: `webapp/templates/index.html`, `webapp/static/js/outreach.js`, `webapp/static/css/style.css` |
| Sequence timeline + Stage controls | Modify: `webapp/static/js/outreach.js`, `webapp/static/css/style.css` |
| Channel-aware compose | Modify: `webapp/static/js/outreach.js`, `webapp/static/css/style.css` |
| Daily digest CLI | Create: `pipeline/due_digest.py`; Test: `tests/test_pipeline_due_digest.py` |
| Launchd installer + docs | Create: `scripts/install_due_digest_launchd.sh`, `scripts/com.chicagopipeline.duedigest.plist.template`; Modify: `README.md` |

---

## Task ordering and dependency notes

- T1 (config YAML) is needed before T3 (engine pure functions)
- T2 (schema) is needed before T5 (outreach.py mods) and T7 (manual-touch writes use the unique index)
- T3 (pure) is needed before T4 (orchestrator)
- T4 is needed before T6 (GET due returns the orchestrator's output) and T12 (digest uses the orchestrator)
- T5 (outreach module touch_number support) is needed before T7 + T8
- T6/T7/T8 (all routes) are needed before T9-T11 (frontend)
- T12 (digest CLI) is needed before T13 (launchd)

Order: T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14.

Each task ends with a single commit. After T14 the branch is ready to merge.

---

## Task 1: Cadence config YAML + 6 template placeholders

**Files:**
- Create: `chicago-pipeline/config/outreach_cadence.yaml`
- Modify: `chicago-pipeline/config/outreach_templates.yaml`

- [ ] **Step 1: Create the cadence config**

Create `chicago-pipeline/config/outreach_cadence.yaml`:

```yaml
# Per-touch cadence config. The Flask app reads this on every page load,
# so editing this file changes future schedule computations immediately —
# no restart required. Note: mid-flight edits shift in-progress parcels
# (cadence_version snapshotting is deferred); document any change here in
# this header so future-you remembers when the rules shifted.
#
# Schema per item:
#   touch:       integer (1..7) — the touch number; unique across the sequence
#   day_offset:  integer — days from touch_1.sent_date when this touch is due
#   channel:     "email" | "phone" | "mail"
#   template:    name of the template in config/outreach_templates.yaml
#   requires:    "email" | "phone" | "mail_address" — which contact field
#                must be present for this touch to surface. mail_address is
#                always present from assessor data.
sequence:
  - touch: 1
    day_offset: 0
    channel: email
    template: initial-cold
    requires: email
  - touch: 2
    day_offset: 3
    channel: email
    template: email-followup-3day
    requires: email
  - touch: 3
    day_offset: 7
    channel: phone
    template: phone-script
    requires: phone
  - touch: 4
    day_offset: 14
    channel: mail
    template: letter-day-14
    requires: mail_address
  - touch: 5
    day_offset: 19
    channel: email
    template: email-new-angle-19
    requires: email
  - touch: 6
    day_offset: 24
    channel: mail
    template: postcard-day-24
    requires: mail_address
  - touch: 7
    day_offset: 30
    channel: email
    template: email-warm-close-30
    requires: email
end_of_sequence_action: surface_for_dead
end_of_sequence_grace_days: 0
```

- [ ] **Step 2: Add 6 new templates to outreach_templates.yaml**

Edit `chicago-pipeline/config/outreach_templates.yaml`. The file currently has one template (`initial-cold`) and a `defaults` block. Add the 6 new templates after `initial-cold` and before `defaults`. The final structure:

```yaml
templates:
  - name: initial-cold
    label: Initial cold inquiry
    subject: Question about {{address}}
    body: |
      Hi {{owner_first_name}},

      I'm Hunter. I buy and develop apartment buildings in Lakeview, and I wanted to reach out about {{address}}.

      I'm not an agent, just a local property owner working directly with other owners. If selling has crossed your mind, let me know. If not, no problem.

      Hunter
      {{my_phone}}
  - name: email-followup-3day
    label: Day 3 — short follow-up
    subject: Re: Question about {{address}}
    body: |
      Hi {{owner_first_name}},

      Wanted to make sure my note from earlier this week didn't get lost in your inbox.

      The reason I reached out about {{address}}: I focus on buildings like yours in Lakeview, and I'd rather work with the owner directly than wait until something hits the market. If you've thought about selling, even casually, I'd be glad to talk through what that might look like.

      Hunter
      {{my_phone}}
  - name: phone-script
    label: Day 7 — phone script
    subject: ""
    body: |
      Hi, is this {{owner_first_name}}?

      This is Hunter Heyman. I emailed you a couple of times over the last week about {{address}}.

      I buy and develop apartment buildings here in Lakeview. Wanted to introduce myself directly in case my notes got buried in your inbox.

      I'm not asking for anything today, just wanted to put a face on the name. If selling ever comes up for you, I'd love to be on the short list of people you talk to. If it doesn't, no worries.

      How does that sound?
  - name: letter-day-14
    label: Day 14 — letter
    subject: ""
    body: |
      Hi {{owner_first_name}},

      I'm writing instead of emailing because I figured an actual letter has a better chance of getting your attention than another email in your inbox.

      My name is Hunter Heyman. I live and work in Lakeview, where I buy and develop apartment buildings. I've reached out about {{address}} a couple times by phone and email already.

      I'm not a broker or a wholesaler. I'm a local owner who would rather have direct conversations with other owners than fight for deals on the open market. When a building like yours comes up, I want to be on the short list of people the seller calls first.

      If you've ever thought about selling, I'd be glad to walk through what that conversation might look like, what I'd offer, what timeline would work for you. There's no obligation in any of it.

      If selling isn't on the table, I understand. You can keep this letter or toss it. If anything changes in the next year or two, my number is at the bottom.

      Best,
      Hunter
      {{my_phone}}
  - name: email-new-angle-19
    label: Day 19 — market angle
    subject: Lakeview market — quick thought on {{address}}
    body: |
      Hi {{owner_first_name}},

      I've been thinking about your building at {{address}} since my last note.

      A few things I've noticed in Lakeview this year: rents on smaller buildings are holding up better than people expected, but operating costs are climbing fast, especially insurance and property tax. The owners I talk to who are quietly considering selling tend to come back to those numbers more than anything else.

      I'm not assuming any of this applies to you. Just thought it was worth sharing where my head is at, in case it matches where yours is.

      Same offer as before: if selling has been on your mind, I'd be glad to talk. If not, you won't hear from me much longer.

      Hunter
      {{my_phone}}
  - name: postcard-day-24
    label: Day 24 — postcard
    subject: ""
    body: |
      Hi {{owner_first_name}},

      Quick note since the rest of my outreach has been emails and a letter: I'm still here, still interested in {{address}} if you ever want to talk about selling.

      No pressure. If a conversation ever makes sense, my number is below.

      Hunter
      {{my_phone}}
  - name: email-warm-close-30
    label: Day 30 — warm close
    subject: Last note from me — {{address}}
    body: |
      Hi {{owner_first_name}},

      This is the last note from me on {{address}} for now. I wanted to circle back one more time in case timing has shifted since I first reached out.

      If selling ever becomes something you're open to, I'd be glad to talk. Otherwise I'll stop knocking and let you get back to running your building.

      Either way, thanks for the time.

      Hunter
      {{my_phone}}
defaults:
  my_name: Hunter
  my_email: ''
  my_phone: ''
```

Note: the existing comment header at the top of the file (the `# Email templates...` block) must stay — the existing save-template flow rebuilds it on every write, so leave it as-is when you edit by hand.

- [ ] **Step 3: Sanity-check the YAML loads**

```bash
cd /Users/hunterheyman/Claude/chicago-pipeline
.venv/bin/python -c "
import yaml
c = yaml.safe_load(open('config/outreach_cadence.yaml'))
assert len(c['sequence']) == 7
assert [t['touch'] for t in c['sequence']] == [1,2,3,4,5,6,7]
print('cadence YAML ok')

t = yaml.safe_load(open('config/outreach_templates.yaml'))
names = {x['name'] for x in t['templates']}
expected = {'initial-cold','email-followup-3day','phone-script','letter-day-14',
            'email-new-angle-19','postcard-day-24','email-warm-close-30'}
assert names == expected, f'mismatch: got {names}, want {expected}'
print('templates YAML ok')
"
```

Expected: prints both `ok` lines.

- [ ] **Step 4: Run the existing suite to confirm no regressions**

```bash
.venv/bin/python -m pytest -q
```

Expected: 276 passed (no test changes yet).

- [ ] **Step 5: Commit**

```bash
git add config/outreach_cadence.yaml config/outreach_templates.yaml
git commit -m "feat(cadence): cadence YAML + 6 new touch templates"
```

---

## Task 2: Schema additions — pause flag + gmail_message_id column + partial unique index

**Files:**
- Modify: `chicago-pipeline/pipeline/db.py`
- Test: `chicago-pipeline/tests/test_db.py`

- [ ] **Step 1: Write failing tests**

Open `tests/test_db.py` and append three tests at the end:

```python
def test_init_db_creates_outreach_paused_column(tmp_path):
    """init_db should add an outreach_paused column to parcels (via the
    _LATER_COLUMNS migration). Defaults to 0."""
    from pipeline.db import init_db
    import sqlite3
    p = tmp_path / "t.db"
    init_db(p)
    conn = sqlite3.connect(p)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(parcels)")}
    assert "outreach_paused" in cols
    conn.close()


def test_init_db_creates_outreach_gmail_message_id_column(tmp_path):
    """init_db should add a gmail_message_id column to outreach. Replaces
    the prior 'shove it into notes' hack — clean column with one purpose."""
    from pipeline.db import init_db
    import sqlite3
    p = tmp_path / "t.db"
    init_db(p)
    conn = sqlite3.connect(p)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(outreach)")}
    assert "gmail_message_id" in cols
    conn.close()


def test_init_db_creates_outreach_unique_touch_index(tmp_path):
    """init_db should create a partial unique index on outreach(pin, touch_number)
    so two rows with the same (pin, touch_number) can't coexist."""
    from pipeline.db import init_db
    import sqlite3
    p = tmp_path / "t.db"
    init_db(p)
    conn = sqlite3.connect(p)
    # Insert a parcel + first outreach row
    conn.execute("INSERT INTO parcels (pin) VALUES (?)", ("14210010010000",))
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, sent_date) VALUES (?, ?, ?)",
        ("14210010010000", 1, "2026-05-15T09:00:00Z"),
    )
    conn.commit()
    # Attempting a duplicate (same pin, same touch) must fail
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO outreach (pin, touch_number, sent_date) VALUES (?, ?, ?)",
            ("14210010010000", 1, "2026-05-15T10:00:00Z"),
        )
        conn.commit()
    # But a different touch_number is fine
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, sent_date) VALUES (?, ?, ?)",
        ("14210010010000", 2, "2026-05-18T09:00:00Z"),
    )
    conn.commit()
    # And NULL touch_number rows can repeat (partial index)
    conn.execute("INSERT INTO outreach (pin) VALUES (?)", ("14210010010000",))
    conn.execute("INSERT INTO outreach (pin) VALUES (?)", ("14210010010000",))
    conn.commit()
    conn.close()
```

Run: `.venv/bin/python -m pytest tests/test_db.py -k "outreach_paused or gmail_message_id or outreach_unique" -v`

Expected: all three FAIL (column + column + index don't exist yet).

- [ ] **Step 2: Add the pause column AND the gmail_message_id column to `_LATER_COLUMNS`**

In `pipeline/db.py`, find the `_LATER_COLUMNS` dict. Append to the `"parcels"` tuple — just before the closing `)` of the parcels tuple — add:

```python
        # outreach_paused=1 stops the cadence engine from surfacing this parcel
        # in Due Today. Manually toggled via POST /api/parcels/<pin>/pause.
        ("outreach_paused", "INTEGER DEFAULT 0"),
```

Then add a new top-level entry for the `outreach` table after the existing entries:

```python
    # Gmail message id from successful sends; null for manual touches.
    # Replaces the prior "shove it into notes" hack — clean column, one
    # purpose, easy to query.
    "outreach": (
        ("gmail_message_id", "TEXT"),
    ),
```

(Place this between the `"parcels"` block and the `"consolidation_groups"` block, or anywhere within `_LATER_COLUMNS` — order doesn't matter.)

- [ ] **Step 3: Add the partial unique index to init_db**

In `pipeline/db.py`, locate the `init_db` function. After the `_LATER_COLUMNS` migration loop and before `conn.commit()`, add:

```python
        # Partial unique index on outreach(pin, touch_number). Partial WHERE
        # touch_number IS NOT NULL so legacy rows pre-cadence don't conflict.
        # Prevents race-duplicates from concurrent send/log endpoints.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_outreach_pin_touch_unique "
            "ON outreach(pin, touch_number) WHERE touch_number IS NOT NULL"
        )
```

The block now ends:

```python
        for table, columns in _LATER_COLUMNS.items():
            for col, sql_type in columns:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}")
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_outreach_pin_touch_unique "
            "ON outreach(pin, touch_number) WHERE touch_number IS NOT NULL"
        )
        conn.commit()
```

- [ ] **Step 4: Run new tests + full suite**

```bash
.venv/bin/python -m pytest tests/test_db.py -v 2>&1 | tail -10
.venv/bin/python -m pytest -q
```

Expected: 279 passed (276 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add pipeline/db.py tests/test_db.py
git commit -m "feat(cadence): pause flag, gmail_message_id col, partial unique index"
```

---

## Task 3: Cadence engine — pure functions (TDD)

**Files:**
- Create: `chicago-pipeline/pipeline/cadence.py`
- Create: `chicago-pipeline/tests/test_pipeline_cadence.py`

- [ ] **Step 1: Write failing tests for the pure functions**

Create `tests/test_pipeline_cadence.py`:

```python
"""Tests for pipeline/cadence.py — pure cadence engine functions."""
from __future__ import annotations
from datetime import date
from pathlib import Path

import pytest

from pipeline.cadence import (
    load_cadence_config,
    next_due_touches_for_parcel,
    is_end_of_sequence,
)


# ---------- load_cadence_config ----------

def _write_config(path: Path, sequence_yaml: str) -> Path:
    path.write_text(sequence_yaml)
    return path


def test_load_cadence_config_parses_minimal(tmp_path):
    p = _write_config(tmp_path / "c.yaml", """
sequence:
  - touch: 1
    day_offset: 0
    channel: email
    template: tpl-1
    requires: email
""")
    cfg = load_cadence_config(p)
    assert len(cfg["sequence"]) == 1
    assert cfg["sequence"][0]["touch"] == 1
    assert cfg["end_of_sequence_grace_days"] == 0


def test_load_cadence_config_sorts_by_touch(tmp_path):
    p = _write_config(tmp_path / "c.yaml", """
sequence:
  - {touch: 2, day_offset: 3, channel: email, template: t2, requires: email}
  - {touch: 1, day_offset: 0, channel: email, template: t1, requires: email}
""")
    cfg = load_cadence_config(p)
    assert [t["touch"] for t in cfg["sequence"]] == [1, 2]


def test_load_cadence_config_rejects_empty_sequence(tmp_path):
    p = _write_config(tmp_path / "c.yaml", "sequence: []\n")
    with pytest.raises(ValueError, match="non-empty"):
        load_cadence_config(p)


def test_load_cadence_config_rejects_unknown_channel(tmp_path):
    p = _write_config(tmp_path / "c.yaml", """
sequence:
  - {touch: 1, day_offset: 0, channel: smoke_signal, template: t, requires: email}
""")
    with pytest.raises(ValueError, match="channel"):
        load_cadence_config(p)


def test_load_cadence_config_rejects_unknown_requires(tmp_path):
    p = _write_config(tmp_path / "c.yaml", """
sequence:
  - {touch: 1, day_offset: 0, channel: email, template: t, requires: fax}
""")
    with pytest.raises(ValueError, match="requires"):
        load_cadence_config(p)


def test_load_cadence_config_rejects_missing_field(tmp_path):
    p = _write_config(tmp_path / "c.yaml", """
sequence:
  - {touch: 1, day_offset: 0, channel: email, requires: email}
""")
    with pytest.raises(ValueError, match="template"):
        load_cadence_config(p)


# ---------- next_due_touches_for_parcel ----------

# A standard 3-touch fixture for the engine tests.
@pytest.fixture
def cfg():
    return {
        "sequence": [
            {"touch": 1, "day_offset": 0, "channel": "email",
             "template": "t1", "requires": "email"},
            {"touch": 2, "day_offset": 3, "channel": "email",
             "template": "t2", "requires": "email"},
            {"touch": 3, "day_offset": 7, "channel": "phone",
             "template": "t3", "requires": "phone"},
        ],
        "end_of_sequence_action": "surface_for_dead",
        "end_of_sequence_grace_days": 0,
    }


def test_no_touch_1_means_no_due_touches(cfg):
    """A parcel with no touch_1 row hasn't entered cadence — empty result."""
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=[],
        contact={"email": "a@b.com"},
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 15),
    )
    assert out == []


def test_touch_2_due_3_days_after_touch_1(cfg):
    rows = [{"touch_number": 1, "sent_date": "2026-05-12T09:00:00Z"}]
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=rows,
        contact={"email": "a@b.com"},
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 15),  # exactly day 3
    )
    assert len(out) == 1
    assert out[0]["touch"] == 2
    assert out[0]["target_date"] == "2026-05-15"
    assert out[0]["days_overdue"] == 0


def test_overdue_touch_includes_days_overdue_count(cfg):
    rows = [{"touch_number": 1, "sent_date": "2026-05-08T09:00:00Z"}]
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=rows,
        contact={"email": "a@b.com", "phone": "555-0100"},
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 16),  # touch 2 was due 5-11, touch 3 was due 5-15
    )
    touches = {t["touch"]: t for t in out}
    assert 2 in touches and 3 in touches
    assert touches[2]["days_overdue"] == 5
    assert touches[3]["days_overdue"] == 1


def test_touch_3_skipped_when_no_phone(cfg):
    rows = [{"touch_number": 1, "sent_date": "2026-05-08T09:00:00Z"}]
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=rows,
        contact={"email": "a@b.com"},  # no phone
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 16),
    )
    touches = {t["touch"] for t in out}
    assert touches == {2}  # touch 3 silently skipped


def test_completed_touch_doesnt_resurface(cfg):
    rows = [
        {"touch_number": 1, "sent_date": "2026-05-08T09:00:00Z"},
        {"touch_number": 2, "sent_date": "2026-05-12T09:00:00Z"},
    ]
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=rows,
        contact={"email": "a@b.com", "phone": "555-0100"},
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 16),
    )
    touches = {t["touch"] for t in out}
    assert touches == {3}  # touch 2 already done


def test_future_touch_not_yet_due(cfg):
    rows = [{"touch_number": 1, "sent_date": "2026-05-14T09:00:00Z"}]
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=rows,
        contact={"email": "a@b.com", "phone": "555-0100"},
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 15),  # only day 1, touch 2 is day 3
    )
    assert out == []


def test_target_dates_anchored_to_touch_1_not_shifted_by_late_touches(cfg):
    """Even if touch 2 was sent late, touch 3's target is still touch_1 + 7."""
    rows = [
        {"touch_number": 1, "sent_date": "2026-05-01T09:00:00Z"},
        {"touch_number": 2, "sent_date": "2026-05-10T09:00:00Z"},  # 9 days late
    ]
    out = next_due_touches_for_parcel(
        cadence_config=cfg, outreach_rows=rows,
        contact={"email": "a@b.com", "phone": "555-0100"},
        parcel_mail_address="100 Main St",
        today=date(2026, 5, 12),
    )
    # touch 3 target_date should be 2026-05-08 (anchor + 7 days), NOT 2026-05-17
    assert len(out) == 1
    assert out[0]["touch"] == 3
    assert out[0]["target_date"] == "2026-05-08"


# ---------- is_end_of_sequence ----------

def test_end_of_sequence_false_when_touches_incomplete(cfg):
    rows = [
        {"touch_number": 1, "sent_date": "2026-04-15T09:00:00Z"},
        {"touch_number": 2, "sent_date": "2026-04-18T09:00:00Z"},
    ]
    assert is_end_of_sequence(
        cadence_config=cfg, outreach_rows=rows, today=date(2026, 5, 15),
    ) is False


def test_end_of_sequence_true_when_all_done_past_grace(cfg):
    rows = [
        {"touch_number": 1, "sent_date": "2026-04-15T09:00:00Z"},
        {"touch_number": 2, "sent_date": "2026-04-18T09:00:00Z"},
        {"touch_number": 3, "sent_date": "2026-04-22T09:00:00Z"},
    ]
    assert is_end_of_sequence(
        cadence_config=cfg, outreach_rows=rows, today=date(2026, 5, 15),
    ) is True


def test_end_of_sequence_respects_grace_days(cfg):
    cfg = {**cfg, "end_of_sequence_grace_days": 30}
    rows = [
        {"touch_number": 1, "sent_date": "2026-04-15T09:00:00Z"},
        {"touch_number": 2, "sent_date": "2026-04-18T09:00:00Z"},
        {"touch_number": 3, "sent_date": "2026-05-10T09:00:00Z"},  # 5 days ago
    ]
    assert is_end_of_sequence(
        cadence_config=cfg, outreach_rows=rows, today=date(2026, 5, 15),
    ) is False  # only 5 days since last, need 30
```

Run: `.venv/bin/python -m pytest tests/test_pipeline_cadence.py -v`

Expected: all FAIL (module not implemented).

- [ ] **Step 2: Implement pipeline/cadence.py — pure functions**

Create `pipeline/cadence.py`:

```python
"""Cadence engine — pure functions over outreach state.

Three pure functions (no DB) for the cadence rules, plus an orchestrator
(in this same file, added in Task 4) that hits the DB. The pure functions
are trivially unit-testable; the orchestrator is the only function that
needs a DB fixture.
"""
from __future__ import annotations
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml


# Channels recognized by the engine. Adding new channels means updating the
# UI (channel-aware compose) and the digest format.
CHANNELS = {"email", "phone", "mail"}

# Fields the `requires` value can name. `mail_address` is sourced from
# parcels.mail_address (always present from assessor); the others from
# contacts.
REQUIRES_FIELDS = {"email", "phone", "mail_address"}


def load_cadence_config(path: Path) -> dict[str, Any]:
    """Load and validate config/outreach_cadence.yaml.

    Returns:
        {
          "sequence": [
              {"touch": int, "day_offset": int, "channel": str,
               "template": str, "requires": str},
              ...
          ],
          "end_of_sequence_action": str,
          "end_of_sequence_grace_days": int,
        }

    Raises ValueError on structural problems.
    """
    with Path(path).open() as f:
        data = yaml.safe_load(f) or {}
    sequence = data.get("sequence") or []
    if not isinstance(sequence, list) or not sequence:
        raise ValueError("cadence config must have a non-empty 'sequence' list")
    for tpl in sequence:
        for k in ("touch", "day_offset", "channel", "template", "requires"):
            if k not in tpl:
                raise ValueError(f"cadence touch missing required field {k!r}")
        if tpl["channel"] not in CHANNELS:
            raise ValueError(
                f"unknown channel {tpl['channel']!r}; allowed: {sorted(CHANNELS)}"
            )
        if tpl["requires"] not in REQUIRES_FIELDS:
            raise ValueError(
                f"unknown requires {tpl['requires']!r}; allowed: {sorted(REQUIRES_FIELDS)}"
            )
    sequence = sorted(sequence, key=lambda t: t["touch"])
    return {
        "sequence": sequence,
        "end_of_sequence_action": data.get("end_of_sequence_action") or "surface_for_dead",
        "end_of_sequence_grace_days": int(data.get("end_of_sequence_grace_days", 0)),
    }


def _parse_iso_date(s: str) -> date:
    """Parse 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SSZ' into a date."""
    return date.fromisoformat(s[:10])


def _contact_has(
    contact: dict | None,
    parcel_mail_address: str | None,
    field: str,
) -> bool:
    """True iff the named contact field is non-empty.

    mail_address comes from the parcel (assessor data, always present in
    practice); email/phone come from the contact row.
    """
    if field == "mail_address":
        return bool(parcel_mail_address)
    if not contact:
        return False
    return bool(contact.get(field))


def next_due_touches_for_parcel(
    *,
    cadence_config: dict,
    outreach_rows: list[dict],
    contact: dict | None,
    parcel_mail_address: str | None,
    today: date,
) -> list[dict]:
    """Return touch configs that are due/overdue for this parcel.

    Anchor = sent_date on the row where touch_number == 1. No touch_1 row →
    parcel hasn't entered cadence → empty result.

    For each touch in cadence_config["sequence"]:
      - skip if already done (a row exists with that touch_number)
      - skip if target_date > today (not yet due)
      - skip if `requires` field not satisfied

    Each returned item is the touch config dict augmented with:
      target_date  (ISO date string)
      days_overdue (int, 0 if due today, positive if past)
    """
    by_touch = {
        r["touch_number"]: r
        for r in outreach_rows
        if r.get("touch_number") is not None
    }
    anchor_row = by_touch.get(1)
    if not anchor_row or not anchor_row.get("sent_date"):
        return []
    anchor = _parse_iso_date(anchor_row["sent_date"])

    out = []
    for tpl in cadence_config["sequence"]:
        if tpl["touch"] in by_touch:
            continue
        target = anchor + timedelta(days=tpl["day_offset"])
        if target > today:
            continue
        if not _contact_has(contact, parcel_mail_address, tpl["requires"]):
            continue
        out.append({
            **tpl,
            "target_date": target.isoformat(),
            "days_overdue": (today - target).days,
        })
    return out


def is_end_of_sequence(
    *,
    cadence_config: dict,
    outreach_rows: list[dict],
    today: date,
) -> bool:
    """True iff every touch in the sequence has been completed AND the grace
    period has elapsed since the last one was sent. Surfaces the 'mark dead?'
    prompt in the digest and UI."""
    by_touch = {
        r["touch_number"]: r
        for r in outreach_rows
        if r.get("touch_number") is not None
    }
    sequence = cadence_config["sequence"]
    if not all(t["touch"] in by_touch for t in sequence):
        return False
    last_touch_num = max(t["touch"] for t in sequence)
    last_row = by_touch[last_touch_num]
    if not last_row.get("sent_date"):
        return False
    last_date = _parse_iso_date(last_row["sent_date"])
    grace = cadence_config.get("end_of_sequence_grace_days", 0)
    return (today - last_date).days >= grace
```

Run: `.venv/bin/python -m pytest tests/test_pipeline_cadence.py -v`

Expected: all PASS (load_cadence_config + next_due_touches_for_parcel + is_end_of_sequence tests, ~13 tests).

- [ ] **Step 3: Run full suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: 278 + 13 = 291 passed.

- [ ] **Step 4: Commit**

```bash
git add pipeline/cadence.py tests/test_pipeline_cadence.py
git commit -m "feat(cadence): pure cadence engine — load config, due touches, end-of-sequence"
```

---

## Task 4: Cadence engine — DB orchestrator `all_due_touches`

**Files:**
- Modify: `chicago-pipeline/pipeline/cadence.py`
- Modify: `chicago-pipeline/tests/test_pipeline_cadence.py`

- [ ] **Step 1: Write failing tests for the orchestrator**

Append to `tests/test_pipeline_cadence.py`:

```python
# ---------- all_due_touches (DB orchestrator) ----------

import sqlite3
from pipeline.cadence import all_due_touches


@pytest.fixture
def db(tmp_path):
    """Minimal schema mirroring parcels/contacts/outreach for orchestrator
    tests. Real schema lives in pipeline/db.py; we duplicate the parts we
    touch here to keep these tests isolated from full init_db."""
    conn = sqlite3.connect(tmp_path / "t.db")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE parcels (
            pin TEXT PRIMARY KEY, address TEXT, owner_name TEXT,
            mail_address TEXT, score REAL, stage TEXT DEFAULT 'scored',
            outreach_paused INTEGER DEFAULT 0
        );
        CREATE TABLE contacts (
            contact_id INTEGER PRIMARY KEY AUTOINCREMENT,
            pin TEXT, name TEXT, phone TEXT, email TEXT,
            mailing_address TEXT, role TEXT, source TEXT
        );
        CREATE TABLE outreach (
            outreach_id INTEGER PRIMARY KEY AUTOINCREMENT,
            wave_id INTEGER, pin TEXT, contact_id INTEGER, channel TEXT,
            touch_number INTEGER, sent_date TEXT, response_date TEXT,
            response_type TEXT, draft_subject TEXT, draft_body TEXT,
            final_body TEXT, notes TEXT
        );
    """)
    conn.commit()
    return conn


def test_all_due_touches_empty_when_no_outreach_parcels(db, cfg):
    db.execute(
        "INSERT INTO parcels (pin, stage) VALUES (?, ?)",
        ("14210010010000", "scored"),
    )
    db.commit()
    out = all_due_touches(db, cfg, date(2026, 5, 15))
    assert out["today"] == "2026-05-15"
    assert out["groups"] == []


def test_all_due_touches_groups_by_channel(db, cfg):
    """Two parcels in outreach stage: parcel A has touch 2 due (email),
    parcel B has touch 3 due (phone). Result has two groups, one per channel."""
    db.executescript("""
        INSERT INTO parcels (pin, address, owner_name, mail_address, stage)
        VALUES ('14210010010000', '123 W Main', 'JANE DOE', '500 N Main', 'outreach');
        INSERT INTO parcels (pin, address, owner_name, mail_address, stage)
        VALUES ('14210010020000', '456 W Halsted', 'JOHN ROE', '600 W Halsted', 'outreach');
        INSERT INTO contacts (pin, email, phone) VALUES ('14210010010000', 'jane@example.com', NULL);
        INSERT INTO contacts (pin, email, phone) VALUES ('14210010020000', 'john@example.com', '555-0123');
        INSERT INTO outreach (pin, touch_number, channel, sent_date)
        VALUES ('14210010010000', 1, 'email', '2026-05-08T09:00:00Z');
        INSERT INTO outreach (pin, touch_number, channel, sent_date)
        VALUES ('14210010020000', 1, 'email', '2026-05-01T09:00:00Z');
        INSERT INTO outreach (pin, touch_number, channel, sent_date)
        VALUES ('14210010020000', 2, 'email', '2026-05-05T09:00:00Z');
    """)
    db.commit()
    out = all_due_touches(db, cfg, date(2026, 5, 11))
    channels = {g["channel"]: g for g in out["groups"]}
    # A: touch 2 (email) due 5-11 (anchor 5-8 + 3 days)
    # B: touch 3 (phone) due 5-8 (anchor 5-1 + 7 days), overdue 3 days
    assert "email" in channels
    assert "phone" in channels
    assert channels["email"]["count"] == 1
    assert channels["email"]["items"][0]["pin"] == "14210010010000"
    assert channels["phone"]["count"] == 1
    assert channels["phone"]["items"][0]["pin"] == "14210010020000"
    assert channels["phone"]["items"][0]["days_overdue"] == 3


def test_all_due_touches_skips_paused(db, cfg):
    db.executescript("""
        INSERT INTO parcels (pin, address, mail_address, stage, outreach_paused)
        VALUES ('14210010010000', '123 W Main', '500 N Main', 'outreach', 1);
        INSERT INTO contacts (pin, email) VALUES ('14210010010000', 'a@b.com');
        INSERT INTO outreach (pin, touch_number, channel, sent_date)
        VALUES ('14210010010000', 1, 'email', '2026-05-08T09:00:00Z');
    """)
    db.commit()
    out = all_due_touches(db, cfg, date(2026, 5, 12))
    assert out["groups"] == []


def test_all_due_touches_skips_responded(db, cfg):
    db.executescript("""
        INSERT INTO parcels (pin, address, mail_address, stage)
        VALUES ('14210010010000', '123 W Main', '500 N Main', 'responded');
        INSERT INTO contacts (pin, email) VALUES ('14210010010000', 'a@b.com');
        INSERT INTO outreach (pin, touch_number, channel, sent_date)
        VALUES ('14210010010000', 1, 'email', '2026-05-08T09:00:00Z');
    """)
    db.commit()
    out = all_due_touches(db, cfg, date(2026, 5, 12))
    assert out["groups"] == []


def test_all_due_touches_surfaces_end_of_sequence(db, cfg):
    """Parcel with all 3 touches completed (cfg has 3 touches) → appears in
    end_of_sequence group with suggest=mark_dead."""
    db.executescript("""
        INSERT INTO parcels (pin, address, mail_address, stage)
        VALUES ('14210010010000', '123 W Main', '500 N Main', 'outreach');
        INSERT INTO contacts (pin, email, phone) VALUES ('14210010010000', 'a@b.com', '555-0100');
        INSERT INTO outreach (pin, touch_number, channel, sent_date) VALUES
            ('14210010010000', 1, 'email', '2026-04-01T09:00:00Z'),
            ('14210010010000', 2, 'email', '2026-04-04T09:00:00Z'),
            ('14210010010000', 3, 'phone', '2026-04-08T09:00:00Z');
    """)
    db.commit()
    out = all_due_touches(db, cfg, date(2026, 5, 15))
    channels = {g["channel"]: g for g in out["groups"]}
    assert "end_of_sequence" in channels
    item = channels["end_of_sequence"]["items"][0]
    assert item["pin"] == "14210010010000"
    assert item["suggest"] == "mark_dead"
    assert item["days_since_last"] > 30
```

Run: `.venv/bin/python -m pytest tests/test_pipeline_cadence.py -v`

Expected: 13 prior pass + 5 new FAIL (`all_due_touches` undefined).

- [ ] **Step 2: Implement all_due_touches in pipeline/cadence.py**

Append to `pipeline/cadence.py`:

```python
def all_due_touches(conn, cadence_config: dict, today: date) -> dict:
    """DB-touching orchestrator. Queries parcels in `outreach` stage (and
    not paused), joins contacts + outreach history, runs the pure functions,
    returns the JSON-shaped structure documented in the spec.
    """
    # Import here to avoid a circular import between outreach.py and cadence.py.
    from pipeline.outreach import parcel_context

    rows = conn.execute(
        """
        SELECT pin, address, owner_name, mail_address, score,
               COALESCE(outreach_paused, 0) AS outreach_paused
        FROM parcels
        WHERE stage = 'outreach'
          AND COALESCE(outreach_paused, 0) = 0
        """
    ).fetchall()

    groups = {"email": [], "phone": [], "mail": [], "end_of_sequence": []}

    for p in rows:
        pin = p["pin"]
        contact_row = conn.execute(
            "SELECT * FROM contacts WHERE pin = ? LIMIT 1", (pin,)
        ).fetchone()
        contact = dict(contact_row) if contact_row else None
        outreach_rows = [
            dict(r) for r in conn.execute(
                "SELECT * FROM outreach WHERE pin = ? ORDER BY touch_number",
                (pin,),
            )
        ]
        owner_first = parcel_context(dict(p), {})["owner_first_name"]

        due = next_due_touches_for_parcel(
            cadence_config=cadence_config,
            outreach_rows=outreach_rows,
            contact=contact,
            parcel_mail_address=p["mail_address"],
            today=today,
        )
        for d in due:
            item = {
                "pin": pin,
                "address": p["address"],
                "owner_name": p["owner_name"],
                "owner_first_name": owner_first,
                "touch": d["touch"],
                "template": d["template"],
                "target_date": d["target_date"],
                "days_overdue": d["days_overdue"],
            }
            if d["channel"] == "email" and contact:
                item["to_email"] = contact.get("email")
            elif d["channel"] == "phone" and contact:
                item["to_phone"] = contact.get("phone")
            groups[d["channel"]].append(item)

        if is_end_of_sequence(
            cadence_config=cadence_config,
            outreach_rows=outreach_rows,
            today=today,
        ):
            by_touch = {
                r["touch_number"]: r for r in outreach_rows
                if r.get("touch_number") is not None
            }
            last_n = max(by_touch)
            last_row = by_touch[last_n]
            last_date = last_row["sent_date"][:10] if last_row.get("sent_date") else None
            days_since = (
                (today - _parse_iso_date(last_row["sent_date"])).days
                if last_row.get("sent_date") else 0
            )
            groups["end_of_sequence"].append({
                "pin": pin,
                "address": p["address"],
                "owner_first_name": owner_first,
                "last_touch_date": last_date,
                "days_since_last": days_since,
                "suggest": "mark_dead",
            })

    response_groups = []
    for channel in ("email", "phone", "mail", "end_of_sequence"):
        items = groups[channel]
        if items:
            response_groups.append({
                "channel": channel,
                "count": len(items),
                "items": items,
            })
    return {"today": today.isoformat(), "groups": response_groups}
```

- [ ] **Step 3: Run cadence tests + full suite**

```bash
.venv/bin/python -m pytest tests/test_pipeline_cadence.py -v
.venv/bin/python -m pytest -q
```

Expected: 18 cadence tests pass; full suite 291 + 5 = 296.

- [ ] **Step 4: Commit**

```bash
git add pipeline/cadence.py tests/test_pipeline_cadence.py
git commit -m "feat(cadence): all_due_touches DB orchestrator with channel grouping"
```

---

## Task 5: Outreach module — `touch_number` support

**Files:**
- Modify: `chicago-pipeline/pipeline/outreach.py`
- Modify: `chicago-pipeline/tests/test_pipeline_outreach.py`

The existing `create_outreach_record` already accepts `touch_number` as a kwarg with default `1`. Task 5 adds a tiny helper `validate_next_due_touch` so the routes can refuse out-of-order sends.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_pipeline_outreach.py`:

```python
from pipeline.outreach import validate_next_due_touch


def test_validate_next_due_touch_accepts_touch_1_when_no_history():
    """Sending touch 1 is valid when the parcel has no prior outreach rows."""
    validate_next_due_touch(outreach_rows=[], touch_number=1)


def test_validate_next_due_touch_rejects_skip_ahead():
    """Sending touch 3 when touch 2 hasn't been done is invalid."""
    rows = [{"touch_number": 1, "sent_date": "2026-05-08T09:00:00Z"}]
    with pytest.raises(ValueError, match="next-due"):
        validate_next_due_touch(outreach_rows=rows, touch_number=3)


def test_validate_next_due_touch_rejects_already_done():
    """Sending touch 1 again is invalid."""
    rows = [{"touch_number": 1, "sent_date": "2026-05-08T09:00:00Z"}]
    with pytest.raises(ValueError, match="already"):
        validate_next_due_touch(outreach_rows=rows, touch_number=1)


def test_validate_next_due_touch_accepts_in_order():
    """After touch 1, touch 2 is the next-due touch."""
    rows = [{"touch_number": 1, "sent_date": "2026-05-08T09:00:00Z"}]
    validate_next_due_touch(outreach_rows=rows, touch_number=2)
```

Run: `.venv/bin/python -m pytest tests/test_pipeline_outreach.py -v 2>&1 | tail -10`

Expected: 4 new tests FAIL (`validate_next_due_touch` undefined).

- [ ] **Step 2: Implement validate_next_due_touch**

Append to `pipeline/outreach.py`:

```python
def validate_next_due_touch(
    *,
    outreach_rows: list[dict],
    touch_number: int,
) -> None:
    """Raise ValueError if `touch_number` is not the next-due touch for this
    parcel's outreach history. The route layer calls this before writing a
    new outreach row to catch out-of-order or duplicate sends server-side
    (the partial unique index on (pin, touch_number) catches races; this
    catches clean-but-wrong client requests with a clearer error)."""
    done = {
        r["touch_number"]
        for r in outreach_rows
        if r.get("touch_number") is not None
    }
    if touch_number in done:
        raise ValueError(f"touch {touch_number} already completed for this parcel")
    expected = max(done) + 1 if done else 1
    if touch_number != expected:
        raise ValueError(
            f"touch {touch_number} is not the next-due touch "
            f"(expected {expected})"
        )
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/python -m pytest tests/test_pipeline_outreach.py -v 2>&1 | tail -10
.venv/bin/python -m pytest -q
```

Expected: outreach tests now show 4 new passes; full suite 296 + 4 = 300.

- [ ] **Step 4: Commit**

```bash
git add pipeline/outreach.py tests/test_pipeline_outreach.py
git commit -m "feat(cadence): validate_next_due_touch helper for route-level send guards"
```

---

## Task 6: Backend read routes — `GET /api/outreach/due` and `GET /api/cadence/config`

**Files:**
- Modify: `chicago-pipeline/webapp/app.py`
- Modify: `chicago-pipeline/webapp/routes.py`
- Create: `chicago-pipeline/tests/test_webapp_cadence_routes.py`

- [ ] **Step 1: Wire OUTREACH_CADENCE_PATH and CLOCK into the app factory**

In `webapp/app.py`, inside `create_app`, add new config keys alongside the existing outreach config. After the line `app.config["OUTREACH_TEMPLATES_PATH"] = ...`, add:

```python
    app.config["OUTREACH_CADENCE_PATH"] = outreach_cadence_path or (
        Path(__file__).resolve().parent.parent / "config" / "outreach_cadence.yaml"
    )
    # Clock dependency: production code calls app.config["CLOCK"]() to get
    # today's date. Tests override this to pin a specific date — no URL
    # parameter needed, no production "?today=" surface.
    from datetime import date as _date
    app.config["CLOCK"] = clock or _date.today
```

And add two new params to the `create_app` signature (after `outreach_templates_path`):

```python
def create_app(
    db_path: Path,
    feature_outreach: bool = False,
    scoring_yaml_path: Path | None = None,
    outreach_templates_path: Path | None = None,
    outreach_cadence_path: Path | None = None,
    clock: "Callable[[], date] | None" = None,
    gmail_client_secrets_path: Path | None = None,
    gmail_token_path: Path | None = None,
    gmail_sender_address: str | None = None,
) -> Flask:
```

Add `from datetime import date` and `from typing import Callable` to the imports at the top of `app.py` if not already present (the quoted annotation avoids requiring an import for the type hint to be valid).

- [ ] **Step 2: Write failing route tests**

Create `tests/test_webapp_cadence_routes.py`:

```python
"""Tests for the cadence read endpoints (GET /api/outreach/due, GET /api/cadence/config)."""
from __future__ import annotations
import sqlite3
from pathlib import Path

import pytest

from pipeline.db import init_db
from webapp.app import create_app


CADENCE_YAML = """
sequence:
  - {touch: 1, day_offset: 0, channel: email, template: t1, requires: email}
  - {touch: 2, day_offset: 3, channel: email, template: t2, requires: email}
  - {touch: 3, day_offset: 7, channel: phone, template: t3, requires: phone}
end_of_sequence_grace_days: 0
"""

TEMPLATES_YAML = """
templates:
  - {name: t1, label: First, subject: "Hi", body: "B1"}
  - {name: t2, label: Second, subject: "Hi 2", body: "B2"}
  - {name: t3, label: Phone, subject: "", body: "Script"}
defaults: {my_name: Hunter}
"""


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "t.db"
    init_db(p)
    conn = sqlite3.connect(p)
    # One parcel in outreach stage with touch 1 sent + email contact
    conn.execute(
        "INSERT INTO parcels (pin, address, owner_name, mail_address, stage) "
        "VALUES (?, ?, ?, ?, ?)",
        ("14210010010000", "123 W Main St", "JOHN SMITH", "500 N Main",
         "outreach"),
    )
    conn.execute(
        "INSERT INTO contacts (pin, email, source) VALUES (?, ?, ?)",
        ("14210010010000", "js@example.com", "manual"),
    )
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 1, "email", "2026-05-08T09:00:00Z"),
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def cadence_path(tmp_path):
    p = tmp_path / "cadence.yaml"
    p.write_text(CADENCE_YAML)
    return p


@pytest.fixture
def templates_path(tmp_path):
    p = tmp_path / "templates.yaml"
    p.write_text(TEMPLATES_YAML)
    return p


@pytest.fixture
def app_on(db_path, cadence_path, templates_path, tmp_path):
    from datetime import date
    return create_app(
        db_path=db_path, feature_outreach=True,
        outreach_templates_path=templates_path,
        outreach_cadence_path=cadence_path,
        clock=lambda: date(2026, 5, 11),  # pinned for deterministic tests
        gmail_client_secrets_path=tmp_path / "client.json",
        gmail_token_path=tmp_path / "token.json",
        gmail_sender_address="me@example.com",
    )


@pytest.fixture
def app_off(db_path):
    return create_app(db_path=db_path, feature_outreach=False)


def test_get_due_404_when_flag_off(app_off):
    assert app_off.test_client().get("/api/outreach/due").status_code == 404


def test_get_due_groups_by_channel(app_on):
    """With one parcel that has touch 1 sent on 2026-05-08 and the test
    clock pinned to 2026-05-11, touch 2 is due today."""
    resp = app_on.test_client().get("/api/outreach/due")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["today"] == "2026-05-11"
    channels = {g["channel"]: g for g in data["groups"]}
    assert "email" in channels
    assert channels["email"]["count"] == 1
    item = channels["email"]["items"][0]
    assert item["pin"] == "14210010010000"
    assert item["touch"] == 2
    assert item["to_email"] == "js@example.com"


def test_get_cadence_config_returns_yaml_as_json(app_on):
    resp = app_on.test_client().get("/api/cadence/config")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["sequence"]) == 3
    assert data["sequence"][0]["template"] == "t1"


def test_get_cadence_config_404_when_flag_off(app_off):
    assert app_off.test_client().get("/api/cadence/config").status_code == 404
```

Run: `.venv/bin/python -m pytest tests/test_webapp_cadence_routes.py -v`

Expected: all FAIL (routes not implemented).

- [ ] **Step 3: Register the read routes in webapp/routes.py**

Open `webapp/routes.py`. Inside the existing `if app.config["FEATURE_OUTREACH"]:` block (the same block where `api_parcel_outreach` lives), find a good insertion point — after `api_outreach_save_template` is a natural spot.

Add imports near the top of the file alongside the existing pipeline imports:

```python
from pipeline import cadence as cadence_module
```

Then add a helper function inside the `register` function (near `_now_iso`):

```python
        def _today():
            """Returns the current date via the injected clock. Tests
            override app.config["CLOCK"] to a fixed lambda."""
            return app.config["CLOCK"]()

        def _load_cadence():
            return cadence_module.load_cadence_config(
                Path(app.config["OUTREACH_CADENCE_PATH"])
            )
```

Now add the two routes (immediately after the save-template route or wherever convenient):

```python
        @app.get("/api/outreach/due")
        def api_outreach_due():
            cadence = _load_cadence()
            with closing(_conn()) as conn:
                return jsonify(
                    cadence_module.all_due_touches(conn, cadence, _today())
                )

        @app.get("/api/cadence/config")
        def api_cadence_config():
            return jsonify(_load_cadence())
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_webapp_cadence_routes.py -v
.venv/bin/python -m pytest -q
```

Expected: 4 cadence route tests pass; full suite 301 + 4 = 305 (+1 from the T2 gmail_message_id test).

- [ ] **Step 5: Commit**

```bash
git add webapp/app.py webapp/routes.py tests/test_webapp_cadence_routes.py
git commit -m "feat(cadence): GET /api/outreach/due + GET /api/cadence/config"
```

---

## Task 7: Backend write routes — log manual touch + pause

**Files:**
- Modify: `chicago-pipeline/webapp/routes.py`
- Modify: `chicago-pipeline/tests/test_webapp_cadence_routes.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_webapp_cadence_routes.py`:

```python
import sqlite3


def test_log_manual_touch_records_phone_touch(app_on, db_path):
    """Posting a phone touch (touch 3) when touch 2 has been sent records
    an outreach row with channel='phone' and the right touch_number."""
    # Send touch 2 first to make touch 3 valid
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 2, "email", "2026-05-11T09:00:00Z"),
    )
    conn.commit()
    conn.close()

    resp = app_on.test_client().post(
        "/api/outreach/log-manual-touch",
        json={"pin": "14210010010000", "touch_number": 3,
              "channel": "phone", "notes": "Left voicemail at 2pm."},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["outreach_id"] > 0
    # Verify the DB row
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT channel, touch_number, notes FROM outreach "
        "WHERE outreach_id = ?", (data["outreach_id"],),
    ).fetchone()
    conn.close()
    assert row[0] == "phone"
    assert row[1] == 3
    assert "voicemail" in row[2]


def test_log_manual_touch_rejects_wrong_channel(app_on, db_path):
    """Posting channel='email' for touch 3 (which is configured as phone) → 400."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 2, "email", "2026-05-11T09:00:00Z"),
    )
    conn.commit()
    conn.close()
    resp = app_on.test_client().post(
        "/api/outreach/log-manual-touch",
        json={"pin": "14210010010000", "touch_number": 3, "channel": "email"},
    )
    assert resp.status_code == 400


def test_log_manual_touch_rejects_out_of_order(app_on):
    """Posting touch 5 when only touch 1 has been done → 400."""
    resp = app_on.test_client().post(
        "/api/outreach/log-manual-touch",
        json={"pin": "14210010010000", "touch_number": 5, "channel": "email"},
    )
    assert resp.status_code == 400


def test_log_manual_touch_409_on_duplicate(app_on, db_path):
    """Inserting a duplicate (pin, touch_number) violates the unique index → 409."""
    # touch 2 already exists; try to log another touch 2
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 2, "email", "2026-05-11T09:00:00Z"),
    )
    conn.commit()
    conn.close()
    # log-manual-touch validates next-due, so we'd get 400 first. Test the
    # unique-index path directly by attempting touch 2 again via the manual
    # endpoint. The validate-next-due check should catch it as "already done".
    resp = app_on.test_client().post(
        "/api/outreach/log-manual-touch",
        json={"pin": "14210010010000", "touch_number": 2, "channel": "email"},
    )
    assert resp.status_code == 400  # caught by validate, before DB


def test_log_manual_touch_404_when_flag_off(app_off):
    assert app_off.test_client().post(
        "/api/outreach/log-manual-touch", json={}
    ).status_code == 404


def test_log_manual_touch_accepts_skipped_channel(app_on, db_path):
    """Logging touch 3 with channel='skipped' records the touch as done
    without doing anything. The next touch surfaces normally."""
    # Send touch 2 first to make touch 3 valid
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 2, "email", "2026-05-11T09:00:00Z"),
    )
    conn.commit()
    conn.close()
    resp = app_on.test_client().post(
        "/api/outreach/log-manual-touch",
        json={"pin": "14210010010000", "touch_number": 3,
              "channel": "skipped", "notes": "Don't want to call."},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    # Verify the row was inserted with channel='skipped'
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT channel, touch_number FROM outreach "
        "WHERE outreach_id = ?", (resp.get_json()["outreach_id"],),
    ).fetchone()
    conn.close()
    assert row[0] == "skipped"
    assert row[1] == 3


def test_pause_parcel_sets_flag(app_on, db_path):
    resp = app_on.test_client().post(
        "/api/parcels/14210010010000/pause",
        json={"paused": True},
    )
    assert resp.status_code == 200
    assert resp.get_json() == {"pin": "14210010010000", "paused": True}
    conn = sqlite3.connect(db_path)
    flag = conn.execute(
        "SELECT outreach_paused FROM parcels WHERE pin = ?",
        ("14210010010000",),
    ).fetchone()[0]
    conn.close()
    assert flag == 1


def test_pause_parcel_hides_from_due(app_on, db_path):
    # Pause it
    app_on.test_client().post(
        "/api/parcels/14210010010000/pause", json={"paused": True}
    )
    # Now it shouldn't appear in due (test clock pinned to 2026-05-11)
    resp = app_on.test_client().get("/api/outreach/due")
    assert resp.get_json()["groups"] == []


def test_pause_parcel_404_when_flag_off(app_off):
    assert app_off.test_client().post(
        "/api/parcels/14210010010000/pause", json={"paused": True}
    ).status_code == 404
```

Run: `.venv/bin/python -m pytest tests/test_webapp_cadence_routes.py -v`

Expected: 7 new tests FAIL.

- [ ] **Step 2: Implement the two routes**

In `webapp/routes.py`, inside the same `if app.config["FEATURE_OUTREACH"]:` block, add after the read routes from Task 6:

```python
        @app.post("/api/outreach/log-manual-touch")
        def api_outreach_log_manual_touch():
            data = request.get_json(silent=True) or {}
            pin = data.get("pin") or ""
            touch_number = data.get("touch_number")
            channel = data.get("channel")
            notes = data.get("notes") or ""
            if not pin.isdigit() or len(pin) != 14:
                abort(400, "invalid pin")
            if not isinstance(touch_number, int):
                abort(400, "touch_number must be an integer")
            if channel not in ("phone", "mail", "skipped"):
                abort(400, "channel must be 'phone', 'mail', or 'skipped'")

            cadence = _load_cadence()
            tpl = next(
                (t for t in cadence["sequence"] if t["touch"] == touch_number),
                None,
            )
            if tpl is None:
                abort(400, f"unknown touch_number {touch_number}")
            # 'skipped' is always allowed regardless of the cadence's configured
            # channel for this touch — the user chose not to do it.
            if channel != "skipped" and tpl["channel"] != channel:
                abort(
                    400,
                    f"touch {touch_number} channel is "
                    f"{tpl['channel']!r}, not {channel!r}",
                )

            with closing(_conn()) as conn:
                _parcel_or_404(conn, pin)
                outreach_rows = [
                    dict(r) for r in conn.execute(
                        "SELECT * FROM outreach WHERE pin = ? "
                        "ORDER BY touch_number",
                        (pin,),
                    )
                ]
                try:
                    outreach_module.validate_next_due_touch(
                        outreach_rows=outreach_rows,
                        touch_number=touch_number,
                    )
                except ValueError as e:
                    abort(400, str(e))
                try:
                    oid = outreach_module.create_outreach_record(
                        conn, pin=pin, contact_id=None,
                        channel=channel, subject="",
                        body=notes,
                        sent_date=_now_iso(),
                        touch_number=touch_number,
                    )
                except sqlite3.IntegrityError:
                    abort(409, "touch already completed")
                # Compute next due so the UI can update without a refetch.
                outreach_rows.append({
                    "touch_number": touch_number,
                    "sent_date": _now_iso(),
                })
                contact_row = conn.execute(
                    "SELECT * FROM contacts WHERE pin = ? LIMIT 1", (pin,)
                ).fetchone()
                contact = dict(contact_row) if contact_row else None
                parcel_row = conn.execute(
                    "SELECT mail_address FROM parcels WHERE pin = ?", (pin,)
                ).fetchone()
                due = cadence_module.next_due_touches_for_parcel(
                    cadence_config=cadence,
                    outreach_rows=outreach_rows,
                    contact=contact,
                    parcel_mail_address=parcel_row["mail_address"],
                    today=_today(),
                )
                next_touch = due[0] if due else None

            return jsonify({"outreach_id": oid, "next_touch": next_touch})

        @app.post("/api/parcels/<pin>/pause")
        def api_parcel_pause(pin: str):
            data = request.get_json(silent=True) or {}
            paused = bool(data.get("paused"))
            if not pin.isdigit() or len(pin) != 14:
                abort(400, "invalid pin")
            with closing(_conn()) as conn:
                _parcel_or_404(conn, pin)
                conn.execute(
                    "UPDATE parcels SET outreach_paused = ? WHERE pin = ?",
                    (1 if paused else 0, pin),
                )
                conn.commit()
            return jsonify({"pin": pin, "paused": paused})
```

You also need to import `sqlite3` at the top of `routes.py` (it's already there) — verify with `grep "import sqlite3" webapp/routes.py`.

- [ ] **Step 3: Run tests**

```bash
.venv/bin/python -m pytest tests/test_webapp_cadence_routes.py -v
.venv/bin/python -m pytest -q
```

Expected: 12 cadence route tests pass (7 prior + 8 new — adds the skip test); full suite 305 + 8 = 313.

- [ ] **Step 4: Commit**

```bash
git add webapp/routes.py tests/test_webapp_cadence_routes.py
git commit -m "feat(cadence): POST log-manual-touch + POST pause parcel"
```

---

## Task 8: Modify existing routes — `/api/outreach/send` touch_number + `/api/parcels/<pin>/outreach` sequence block

**Files:**
- Modify: `chicago-pipeline/webapp/routes.py`
- Modify: `chicago-pipeline/tests/test_webapp_outreach_routes.py`

- [ ] **Step 1: Add tests for the new behaviors**

Append to `tests/test_webapp_outreach_routes.py`:

```python
def test_send_outreach_accepts_touch_number(app_on, outreach_db_path):
    """Send with touch_number=2 records the outreach row with that touch."""
    # Send touch 1 first so touch 2 is valid
    import sqlite3
    conn = sqlite3.connect(outreach_db_path)
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 1, "email", "2026-05-08T09:00:00Z"),
    )
    conn.commit()
    conn.close()
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "m2", "threadId": "t2"}
        resp = app_on.test_client().post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "Touch 2", "body": "Day 3 follow-up",
            "touch_number": 2,
        })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    # Verify the row was inserted with touch_number=2
    conn = sqlite3.connect(outreach_db_path)
    row = conn.execute(
        "SELECT touch_number FROM outreach WHERE outreach_id = ?",
        (resp.get_json()["outreach_id"],),
    ).fetchone()
    conn.close()
    assert row[0] == 2


def test_send_outreach_rejects_out_of_order_touch(app_on):
    """Posting touch 5 when only touch 1 has been done → 400."""
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "m", "threadId": "t"}
        resp = app_on.test_client().post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "s", "body": "b", "touch_number": 5,
        })
    assert resp.status_code == 400


def test_send_outreach_defaults_touch_number_to_1(app_on, outreach_db_path):
    """Backward-compat: omitting touch_number sends touch 1."""
    with patch("webapp.routes.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "m", "threadId": "t"}
        resp = app_on.test_client().post("/api/outreach/send", json={
            "pin": "14210010010000", "to": "x@y.com",
            "subject": "s", "body": "b",
        })
    assert resp.status_code == 200
    import sqlite3
    conn = sqlite3.connect(outreach_db_path)
    row = conn.execute(
        "SELECT touch_number FROM outreach WHERE outreach_id = ?",
        (resp.get_json()["outreach_id"],),
    ).fetchone()
    conn.close()
    assert row[0] == 1


def test_get_parcel_outreach_includes_sequence_block(app_on, outreach_db_path):
    """The detail endpoint returns a `sequence` block describing the
    parcel's cadence state."""
    import sqlite3
    conn = sqlite3.connect(outreach_db_path)
    conn.execute(
        "UPDATE parcels SET stage = 'outreach', mail_address = ? WHERE pin = ?",
        ("500 N Main", "14210010010000"),
    )
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 1, "email", "2026-05-08T09:00:00Z"),
    )
    conn.execute(
        "INSERT INTO contacts (pin, email, source) VALUES (?, ?, ?)",
        ("14210010010000", "js@example.com", "manual"),
    )
    conn.commit()
    conn.close()
    resp = app_on.test_client().get("/api/parcels/14210010010000/outreach")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "sequence" in data
    seq = data["sequence"]
    assert seq["anchor_date"] == "2026-05-08"
    assert seq["current_touch"] == 1
    assert seq["next_due"] is not None
    assert seq["next_due"]["touch"] == 2
    assert seq["is_end_of_sequence"] is False
    assert seq["is_paused"] is False


def test_get_parcel_outreach_sequence_paused(app_on, outreach_db_path):
    import sqlite3
    conn = sqlite3.connect(outreach_db_path)
    conn.execute(
        "UPDATE parcels SET outreach_paused = 1 WHERE pin = ?",
        ("14210010010000",),
    )
    conn.commit()
    conn.close()
    resp = app_on.test_client().get("/api/parcels/14210010010000/outreach")
    assert resp.get_json()["sequence"]["is_paused"] is True
```

Run: `.venv/bin/python -m pytest tests/test_webapp_outreach_routes.py -v 2>&1 | tail -15`

Expected: 5 new tests FAIL.

- [ ] **Step 2: Wire the cadence path through the existing outreach fixtures**

The test_webapp_outreach_routes.py fixture creates an app without `outreach_cadence_path`. The new tests rely on the cadence. Update the `app_on` fixture in that file — find it and add `outreach_cadence_path`:

```python
@pytest.fixture
def cadence_path_in_outreach_tests(tmp_path: Path) -> Path:
    p = tmp_path / "cadence.yaml"
    p.write_text("""
sequence:
  - {touch: 1, day_offset: 0, channel: email, template: t1, requires: email}
  - {touch: 2, day_offset: 3, channel: email, template: t1, requires: email}
  - {touch: 5, day_offset: 19, channel: email, template: t1, requires: email}
end_of_sequence_grace_days: 0
""")
    return p


@pytest.fixture
def app_on(outreach_db_path: Path, templates_path: Path,
           cadence_path_in_outreach_tests: Path, tmp_path: Path):
    from datetime import date
    return create_app(
        db_path=outreach_db_path, feature_outreach=True,
        outreach_templates_path=templates_path,
        outreach_cadence_path=cadence_path_in_outreach_tests,
        clock=lambda: date(2026, 5, 11),  # pinned for deterministic tests
        gmail_client_secrets_path=tmp_path / "client.json",
        gmail_token_path=tmp_path / "token.json",
        gmail_sender_address="me@example.com",
    )
```

(Replace the existing `app_on` fixture with this version. Leave `app_off` unchanged — it doesn't need cadence config.)

- [ ] **Step 3: Modify api_outreach_send to accept and validate touch_number**

In `webapp/routes.py`, find `api_outreach_send`. Add `touch_number` extraction + validation before the existing pin/email/subject/body validation. Replace the start of the function body up to the `subject = outreach_module.sanitize_subject(subject)` line with:

```python
        @app.post("/api/outreach/send")
        def api_outreach_send():
            data = request.get_json(silent=True) or {}
            pin = data.get("pin") or ""
            to = data.get("to") or ""
            subject = data.get("subject")
            body = data.get("body")
            touch_number = data.get("touch_number", 1)
            if not isinstance(touch_number, int) or touch_number < 1:
                abort(400, "touch_number must be a positive integer")
            if not pin.isdigit() or len(pin) != 14:
                abort(400, "invalid pin")
            if not EMAIL_RE.match(to):
                abort(400, "invalid recipient email")
            if not subject or body is None:
                abort(400, "subject and body are required")

            subject = outreach_module.sanitize_subject(subject)
```

Then, inside the existing `with closing(_conn()) as conn:` block, BEFORE the call to `gmail_client.send_email`, add the next-due validation:

```python
            with closing(_conn()) as conn:
                _parcel_or_404(conn, pin)
                # Validate the touch_number is next-due for this parcel.
                outreach_rows = [
                    dict(r) for r in conn.execute(
                        "SELECT * FROM outreach WHERE pin = ? "
                        "ORDER BY touch_number",
                        (pin,),
                    )
                ]
                try:
                    outreach_module.validate_next_due_touch(
                        outreach_rows=outreach_rows,
                        touch_number=touch_number,
                    )
                except ValueError as e:
                    abort(400, str(e))

                cid = outreach_module.upsert_contact(
                    conn, pin=pin, email=to, source="manual"
                )
                # ... existing gmail send + record code follows ...
```

Then change the `create_outreach_record` call inside this function to pass `touch_number`:

```python
                oid = outreach_module.create_outreach_record(
                    conn, pin=pin, contact_id=cid,
                    channel="email", subject=subject, body=body,
                    sent_date=_now_iso(),
                    touch_number=touch_number,
                )
```

And handle the unique-index race: wrap the `create_outreach_record` call in a try/except for `sqlite3.IntegrityError → abort(409)`:

```python
                try:
                    oid = outreach_module.create_outreach_record(
                        conn, pin=pin, contact_id=cid,
                        channel="email", subject=subject, body=body,
                        sent_date=_now_iso(),
                        touch_number=touch_number,
                    )
                except sqlite3.IntegrityError:
                    abort(409, "touch already completed (race detected)")
```

Then find the existing line that stores the Gmail message id in the notes field — currently:

```python
                # Persist the Gmail message id in `notes` (cheap; avoids a
                # schema change to add a dedicated column).
                conn.execute(
                    "UPDATE outreach SET notes = ? WHERE outreach_id = ?",
                    (f"gmail_message_id={result.get('id','')}", oid),
                )
```

Replace with the new dedicated column (added in T2):

```python
                # Persist the Gmail message id in its dedicated column.
                conn.execute(
                    "UPDATE outreach SET gmail_message_id = ? WHERE outreach_id = ?",
                    (result.get("id", ""), oid),
                )
```

(Removes the prior `notes` double-purpose; `notes` is now exclusively for manual touch notes.)

- [ ] **Step 4: Modify api_parcel_outreach to include the sequence block**

In `webapp/routes.py`, find `api_parcel_outreach`. After the existing query/contact/outreach lookups but before the `return jsonify(...)`, build a sequence block:

```python
        @app.get("/api/parcels/<pin>/outreach")
        def api_parcel_outreach(pin: str):
            with closing(_conn()) as conn:
                parcel = _parcel_or_404(conn, pin)
                contact = conn.execute(
                    "SELECT * FROM contacts WHERE pin = ? LIMIT 1", (pin,)
                ).fetchone()
                outreach_rows = outreach_module.list_outreach_for_parcel(conn, pin)
                outreach_dicts = [dict(r) for r in outreach_rows]

            # Compute sequence state
            cadence = _load_cadence()
            today = _today()
            by_touch = {
                r["touch_number"]: r for r in outreach_dicts
                if r.get("touch_number") is not None
            }
            anchor_row = by_touch.get(1)
            anchor_date = (
                anchor_row["sent_date"][:10]
                if anchor_row and anchor_row.get("sent_date") else None
            )
            current_touch = max(by_touch.keys()) if by_touch else 0
            is_paused = bool(parcel.get("outreach_paused"))
            is_eos = cadence_module.is_end_of_sequence(
                cadence_config=cadence, outreach_rows=outreach_dicts, today=today,
            )

            next_due = None
            if not is_paused:
                due_list = cadence_module.next_due_touches_for_parcel(
                    cadence_config=cadence,
                    outreach_rows=outreach_dicts,
                    contact=dict(contact) if contact else None,
                    parcel_mail_address=parcel.get("mail_address"),
                    today=today,
                )
                if due_list:
                    n = due_list[0]
                    next_due = {
                        "touch": n["touch"],
                        "channel": n["channel"],
                        "target_date": n["target_date"],
                        "days_overdue": n["days_overdue"],
                        "available": True,
                    }

            return jsonify({
                "pin": pin,
                "contact": dict(contact) if contact else None,
                "outreach": outreach_dicts,
                "gmail_connected": gmail_client.is_connected(
                    Path(app.config["GMAIL_TOKEN_PATH"])
                ),
                "sender_address": app.config.get("GMAIL_SENDER_ADDRESS") or "",
                "sequence": {
                    "anchor_date": anchor_date,
                    "current_touch": current_touch,
                    "next_due": next_due,
                    "is_end_of_sequence": is_eos,
                    "is_paused": is_paused,
                },
            })
```

- [ ] **Step 5: Run tests + full suite**

```bash
.venv/bin/python -m pytest tests/test_webapp_outreach_routes.py -v 2>&1 | tail -15
.venv/bin/python -m pytest -q
```

Expected: 5 new outreach route tests pass; full suite 313 + 5 = 318.

- [ ] **Step 6: Commit**

```bash
git add webapp/routes.py tests/test_webapp_outreach_routes.py
git commit -m "feat(cadence): /send accepts touch_number; /parcels/<pin>/outreach returns sequence block"
```

---

## Task 9: Frontend — Due Today banner

**Files:**
- Modify: `chicago-pipeline/webapp/templates/index.html`
- Modify: `chicago-pipeline/webapp/static/js/outreach.js`
- Modify: `chicago-pipeline/webapp/static/css/style.css`

- [ ] **Step 1: Add the banner container to index.html**

In `webapp/templates/index.html`, find the existing `{% if feature_outreach %}` block at the top (it was removed in the previous outreach branch; we're adding it back). Insert immediately after the top-bar `<div>`:

```html
{% if feature_outreach %}
<div id="due-today-bar" class="due-today-bar" hidden>
  <span class="due-today-label">DUE TODAY</span>
  <span id="due-today-chips" class="due-today-chips"></span>
</div>
{% endif %}
```

The `hidden` attribute keeps it invisible until the JS confirms there's something to show.

Also update the `.main` div opening tag to add a `has-due-bar` class conditionally:

```html
<div class="{% if feature_outreach %}main has-due-bar{% else %}main{% endif %}">
```

- [ ] **Step 2: Append the Due Today renderer to outreach.js**

In `webapp/static/js/outreach.js`, inside the IIFE, near the other API-fetch helpers, add a fetcher and renderer. Right before the line `window.__outreachRenderSections = renderOutreachSections;`, insert:

```javascript
  // ---------- Due Today banner ----------

  async function fetchDue() {
    const resp = await fetch('/api/outreach/due');
    if (!resp.ok) throw new Error(`fetch due failed: ${resp.status}`);
    return resp.json();
  }

  function channelEmoji(channel) {
    return {email: '✉', phone: '☎', mail: '✉', end_of_sequence: '✓'}[channel] || '';
  }

  function channelLabel(channel, count) {
    const noun = {
      email: count === 1 ? 'email' : 'emails',
      phone: count === 1 ? 'phone call' : 'phone calls',
      mail:  count === 1 ? 'letter' : 'letters',
      end_of_sequence: count === 1 ? 'ready to retire' : 'ready to retire',
    }[channel] || channel;
    return `${count} ${noun}`;
  }

  async function renderDueToday() {
    const bar = document.getElementById('due-today-bar');
    if (!bar) return;
    let data;
    try { data = await fetchDue(); } catch (_) { return; }
    const groups = data.groups || [];
    if (groups.length === 0) {
      bar.hidden = true;
      bar.innerHTML = '<span class="due-today-label">DUE TODAY</span>'
        + '<span id="due-today-chips" class="due-today-chips"></span>';
      return;
    }
    bar.hidden = false;
    const chipsEl = bar.querySelector('#due-today-chips');
    chipsEl.innerHTML = '';
    groups.forEach(g => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'due-today-chip due-today-chip-' + g.channel;
      chip.dataset.channel = g.channel;
      chip.textContent = `${channelEmoji(g.channel)} ${channelLabel(g.channel, g.count)}`;
      chip.addEventListener('click', () => toggleChipDropdown(chip, g));
      chipsEl.appendChild(chip);
    });
  }

  function toggleChipDropdown(chip, group) {
    // Close any existing dropdown
    const existing = document.getElementById('due-today-dropdown');
    if (existing) {
      existing.remove();
      if (existing.dataset.forChannel === group.channel) return; // re-click = close
    }
    const dropdown = document.createElement('div');
    dropdown.id = 'due-today-dropdown';
    dropdown.className = 'due-today-dropdown';
    dropdown.dataset.forChannel = group.channel;
    dropdown.innerHTML = group.items.map(it => {
      const overdue = it.days_overdue > 0
        ? `<span class="due-today-overdue">+${it.days_overdue}d</span>` : '';
      const subline = group.channel === 'end_of_sequence'
        ? `Sequence complete · ${it.days_since_last}d since last touch`
        : `Touch ${it.touch} · ${it.target_date}`;
      return `
        <button type="button" class="due-today-row" data-pin="${escapeHtml(it.pin)}">
          <div class="due-today-row-main">
            <strong>${escapeHtml(it.address || it.pin)}</strong>
            <span class="due-today-row-owner">${escapeHtml(it.owner_first_name || '')}</span>
          </div>
          <div class="due-today-row-sub">${escapeHtml(subline)} ${overdue}</div>
        </button>
      `;
    }).join('');
    dropdown.querySelectorAll('[data-pin]').forEach(btn => {
      btn.addEventListener('click', () => {
        const pin = btn.dataset.pin;
        dropdown.remove();
        window.dispatchEvent(new CustomEvent('parcelselect', { detail: { pin } }));
      });
    });
    // Position below the chip
    const rect = chip.getBoundingClientRect();
    dropdown.style.position = 'fixed';
    dropdown.style.top = (rect.bottom + 4) + 'px';
    dropdown.style.left = rect.left + 'px';
    document.body.appendChild(dropdown);
    // Click-outside to close
    setTimeout(() => {
      document.addEventListener('click', function onDoc(e) {
        if (!dropdown.contains(e.target) && e.target !== chip) {
          dropdown.remove();
          document.removeEventListener('click', onDoc);
        }
      });
    }, 0);
  }

  // Render on page load + whenever an outreach refresh fires
  window.addEventListener('DOMContentLoaded', () => { renderDueToday(); });
  window.addEventListener('outreach:refresh', () => { renderDueToday(); });

  window.__outreachRenderDueToday = renderDueToday;
```

- [ ] **Step 3: Add the CSS**

Append to `webapp/static/css/style.css`:

```css
/* ===== Due Today banner ===== */
.due-today-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  background: #161b22;
  border-bottom: 1px solid #30363d;
  font-size: 12px;
  color: #c9d1d9;
}
.due-today-label {
  font-weight: 600;
  letter-spacing: 0.08em;
  font-size: 10px;
  color: #8b949e;
}
.due-today-chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.due-today-chip {
  background: #21262d;
  color: #c9d1d9;
  border: 1px solid #30363d;
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 11px;
  cursor: pointer;
  transition: background 100ms ease, border-color 100ms ease;
}
.due-today-chip:hover {
  background: #30363d;
  border-color: #58a6ff;
}
.due-today-chip-email { color: #58a6ff; }
.due-today-chip-phone { color: #f0883e; }
.due-today-chip-mail { color: #a371f7; }
.due-today-chip-end_of_sequence { color: #3fb950; }

.due-today-dropdown {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  min-width: 320px;
  max-width: 480px;
  max-height: 60vh;
  overflow-y: auto;
  z-index: 9500;
  box-shadow: 0 6px 16px rgba(0,0,0,0.4);
}
.due-today-row {
  display: block;
  width: 100%;
  text-align: left;
  background: transparent;
  border: none;
  border-bottom: 1px solid #30363d;
  padding: 10px 14px;
  color: #c9d1d9;
  cursor: pointer;
}
.due-today-row:last-child { border-bottom: none; }
.due-today-row:hover { background: #21262d; }
.due-today-row-main { display: flex; justify-content: space-between; font-size: 13px; }
.due-today-row-owner { color: #8b949e; font-size: 11px; }
.due-today-row-sub { color: #8b949e; font-size: 11px; padding-top: 2px; }
.due-today-overdue {
  display: inline-block;
  background: #f85149;
  color: white;
  border-radius: 3px;
  padding: 1px 4px;
  font-weight: 600;
}

/* Adjust main panel offset when the bar is present */
.main.has-due-bar { /* spacing handled by the flex layout — no extra margin needed */ }
```

- [ ] **Step 4: Sanity check that the static file serves the new code**

Start a quick smoke check via the test client (no real server needed):

```bash
.venv/bin/python -c "
from webapp.app import create_app
from pathlib import Path
db = Path('data/full.alt.db') if Path('data/full.alt.db').exists() else Path('data/smoke.db')
app = create_app(db_path=db, feature_outreach=True)
c = app.test_client()
r = c.get('/static/js/outreach.js')
body = r.get_data(as_text=True)
assert 'renderDueToday' in body
assert 'due-today-chip' in body
print('outreach.js ok')
r = c.get('/static/css/style.css')
css = r.get_data(as_text=True)
assert '.due-today-bar' in css
print('style.css ok')
r = c.get('/')
html = r.get_data(as_text=True)
assert 'due-today-bar' in html
print('index.html ok')
"
```

Expected: three `ok` lines.

- [ ] **Step 5: Run full pytest suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: 316 passed (no JS test runner; the new JS doesn't break templates).

- [ ] **Step 6: Commit**

```bash
git add webapp/templates/index.html webapp/static/js/outreach.js webapp/static/css/style.css
git commit -m "feat(cadence): Due Today banner with channel-grouped chips and parcel dropdowns"
```

---

## Task 10: Frontend — Sequence timeline + Stage controls (pause, mark dead)

**Files:**
- Modify: `chicago-pipeline/webapp/static/js/outreach.js`
- Modify: `chicago-pipeline/webapp/static/css/style.css`

- [ ] **Step 1: Add the sequence timeline renderer to outreach.js**

In `webapp/static/js/outreach.js`, near the existing `renderStageSection` / `renderContactSection` / `renderHistorySection` functions (inside the IIFE), add a `renderSequenceSection` function. Insert it just after `renderStageSection`:

```javascript
  // ---------- Sequence timeline ----------

  async function fetchCadenceConfig() {
    const resp = await fetch('/api/cadence/config');
    if (!resp.ok) throw new Error(`fetch cadence failed: ${resp.status}`);
    return resp.json();
  }

  // Cache the cadence config per page-load; reload on outreach:refresh
  let __cachedCadence = null;
  async function getCadenceConfig() {
    if (__cachedCadence) return __cachedCadence;
    __cachedCadence = await fetchCadenceConfig();
    return __cachedCadence;
  }
  window.addEventListener('outreach:refresh', () => { __cachedCadence = null; });

  function renderSequenceSection(parcel, data, cadence) {
    const el = document.createElement('div');
    el.className = 'detail-section';
    const seq = data.sequence || {};

    if (!seq.anchor_date) {
      // Parcel hasn't entered cadence — show a "Start cadence" prompt
      el.innerHTML = `
        <h3>Sequence</h3>
        <div style="font-size:12px; color:#8b949e;">
          No outreach yet. Compose touch 1 to start the cadence.
        </div>
      `;
      return el;
    }

    const completed = {};
    (data.outreach || []).forEach(r => {
      if (r.touch_number != null) completed[r.touch_number] = r;
    });

    const rows = cadence.sequence.map(t => {
      const done = completed[t.touch];
      const isNext = seq.next_due && seq.next_due.touch === t.touch;
      let status = 'future';
      if (done) status = 'done';
      else if (isNext) status = 'current';

      const targetDate = anchorPlus(seq.anchor_date, t.day_offset);
      const reqMissing = isNext && seq.next_due && !seq.next_due.available;
      return `
        <div class="seq-row seq-row-${status}">
          <span class="seq-icon">${status === 'done' ? '✓' : (status === 'current' ? '●' : '○')}</span>
          <span class="seq-num">${t.touch}</span>
          <span class="seq-channel">${t.channel}</span>
          <span class="seq-date">${done ? `sent ${(done.sent_date || '').slice(0,10)}` : `due ${targetDate}`}</span>
          ${reqMissing ? '<span class="seq-req-missing">(no ' + escapeHtml(t.requires) + ')</span>' : ''}
        </div>
      `;
    }).join('');

    const pausedBadge = seq.is_paused
      ? '<span class="seq-paused-badge">paused</span>' : '';
    const eosBadge = seq.is_end_of_sequence
      ? '<span class="seq-eos-badge">Sequence complete</span>' : '';

    el.innerHTML = `
      <h3>Sequence — Touch ${seq.current_touch || 0} of ${cadence.sequence.length} ${pausedBadge} ${eosBadge}</h3>
      <div class="seq-rows">${rows}</div>
    `;
    return el;
  }

  function anchorPlus(anchorIso, days) {
    const d = new Date(anchorIso + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() + days);
    return d.toISOString().slice(0, 10);
  }
```

- [ ] **Step 2: Hook the sequence section into renderOutreachSections**

In the same file, find `renderOutreachSections` and update it to fetch the cadence config and call the new renderer. Replace the existing implementation with:

```javascript
  async function renderOutreachSections(parcel, panel) {
    // Snapshot the panel's render serial at call time. If a newer render
    // bumps it before our fetch returns, we're stale — bail without appending.
    const serial = panel.dataset.renderSerial;
    let data, cadence;
    try {
      [data, cadence] = await Promise.all([fetchOutreach(parcel.pin), getCadenceConfig()]);
    } catch (_) {
      if (panel.dataset.renderSerial !== serial) return;
      const err = document.createElement('div');
      err.className = 'detail-section';
      err.innerHTML = '<h3>Outreach</h3><div style="font-size:12px; color:#f85149;">Couldn’t load outreach data.</div>';
      panel.appendChild(err);
      return;
    }
    if (panel.dataset.renderSerial !== serial) return;
    panel.appendChild(renderStageSection(parcel, data));
    panel.appendChild(renderSequenceSection(parcel, data, cadence));
    panel.appendChild(renderContactSection(parcel, data));
    panel.appendChild(renderHistorySection(parcel, data));
  }
```

(Note `renderStageSection` now takes `data` too — see Step 3.)

- [ ] **Step 3: Update renderStageSection to add pause + mark-dead buttons**

Replace the existing `renderStageSection` function with:

```javascript
  function renderStageSection(parcel, data) {
    const el = document.createElement('div');
    el.className = 'detail-section';
    const stages = ['scored', 'outreach', 'responded', 'introduced', 'dead'];
    const cur = parcel.stage || 'scored';
    const seq = (data && data.sequence) || {};
    const showPause = cur === 'outreach' && seq.anchor_date;
    const showMarkDead = seq.is_end_of_sequence && cur !== 'dead';

    el.innerHTML = `
      <h3>Stage</h3>
      <div class="detail-grid" style="grid-template-columns: 1fr;">
        <div class="detail-item" style="display:flex; gap:8px; align-items:center; flex-wrap: wrap;">
          <select id="outreach-stage-select" class="outreach-input outreach-stage-select">
            ${stages.map(s => `<option value="${s}"${s === cur ? ' selected' : ''}>${s}</option>`).join('')}
          </select>
          ${showPause ? `<button type="button" class="btn btn-sm" id="outreach-pause-btn">${seq.is_paused ? '▶ Resume cadence' : '⏸ Pause cadence'}</button>` : ''}
          ${showMarkDead ? '<button type="button" class="btn btn-sm" id="outreach-mark-dead-btn">Mark dead</button>' : ''}
        </div>
      </div>
    `;
    const sel = el.querySelector('#outreach-stage-select');
    sel.addEventListener('change', async () => {
      try {
        await setStage(parcel.pin, sel.value);
        showToast('Stage updated to "' + sel.value + '"', 'success');
        window.dispatchEvent(new CustomEvent('outreach:refresh',
                                              { detail: { pin: parcel.pin } }));
      } catch (e) {
        showToast("Couldn't update stage", 'error');
      }
    });
    const pauseBtn = el.querySelector('#outreach-pause-btn');
    if (pauseBtn) {
      pauseBtn.addEventListener('click', async () => {
        const target = !seq.is_paused;
        try {
          await fetch(`/api/parcels/${encodeURIComponent(parcel.pin)}/pause`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({paused: target}),
          });
          showToast(target ? 'Cadence paused' : 'Cadence resumed', 'success');
          window.dispatchEvent(new CustomEvent('outreach:refresh',
                                                { detail: { pin: parcel.pin } }));
        } catch (e) {
          showToast("Couldn't toggle pause", 'error');
        }
      });
    }
    const deadBtn = el.querySelector('#outreach-mark-dead-btn');
    if (deadBtn) {
      deadBtn.addEventListener('click', async () => {
        try {
          await setStage(parcel.pin, 'dead');
          showToast('Marked dead', 'success');
          window.dispatchEvent(new CustomEvent('outreach:refresh',
                                                { detail: { pin: parcel.pin } }));
        } catch (e) {
          showToast("Couldn't mark dead", 'error');
        }
      });
    }
    return el;
  }
```

- [ ] **Step 4: Add CSS for the timeline**

Append to `webapp/static/css/style.css`:

```css
/* ===== Sequence timeline ===== */
.seq-rows {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-top: 6px;
}
.seq-row {
  display: grid;
  grid-template-columns: 16px 18px 60px 1fr;
  gap: 8px;
  align-items: center;
  font-size: 12px;
  padding: 4px 0;
}
.seq-icon { text-align: center; font-size: 14px; }
.seq-num  { color: #8b949e; }
.seq-channel { color: #c9d1d9; }
.seq-date { color: #8b949e; }
.seq-row-done .seq-icon { color: #3fb950; }
.seq-row-current .seq-icon { color: #f0883e; }
.seq-row-future .seq-icon { color: #484f58; }
.seq-row-current { background: rgba(240, 136, 62, 0.06); border-radius: 4px; }
.seq-req-missing { color: #8b949e; font-style: italic; font-size: 11px; }
.seq-paused-badge {
  display: inline-block;
  background: #484f58;
  color: white;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 3px;
  margin-left: 6px;
}
.seq-eos-badge {
  display: inline-block;
  background: #3fb950;
  color: white;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 3px;
  margin-left: 6px;
}
```

- [ ] **Step 5: Sanity check**

```bash
.venv/bin/python -c "
from webapp.app import create_app
from pathlib import Path
db = Path('data/full.alt.db') if Path('data/full.alt.db').exists() else Path('data/smoke.db')
app = create_app(db_path=db, feature_outreach=True)
c = app.test_client()
js = c.get('/static/js/outreach.js').get_data(as_text=True)
assert 'renderSequenceSection' in js
assert 'outreach-pause-btn' in js
css = c.get('/static/css/style.css').get_data(as_text=True)
assert '.seq-rows' in css
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 6: Run pytest**

```bash
.venv/bin/python -m pytest -q
```

Expected: 316 passed (no JS test runner; backend unchanged).

- [ ] **Step 7: Commit**

```bash
git add webapp/static/js/outreach.js webapp/static/css/style.css
git commit -m "feat(cadence): sequence timeline + pause/mark-dead stage controls"
```

---

## Task 11: Frontend — Channel-aware compose (phone + mail modals)

**Files:**
- Modify: `chicago-pipeline/webapp/static/js/outreach.js`
- Modify: `chicago-pipeline/webapp/static/css/style.css`

- [ ] **Step 1: Add channel router on Compose button click**

In `webapp/static/js/outreach.js`, find `renderContactSection`. The existing Compose button handler calls `window.__outreachOpenCompose(parcel, liveContact, data.sender_address)`. Update the handler to inspect `data.sequence` and dispatch to the right modal:

Find the existing block:

```javascript
    const btn = el.querySelector('#outreach-compose-btn');
    btn.addEventListener('click', () => {
      if (typeof window.__outreachOpenCompose === 'function') {
        const liveEmail = input.value.trim();
        const liveContact = liveEmail
          ? Object.assign({}, data.contact || {}, { email: liveEmail })
          : data.contact;
        window.__outreachOpenCompose(parcel, liveContact, data.sender_address);
      }
    });
```

Replace with:

```javascript
    const btn = el.querySelector('#outreach-compose-btn');
    btn.addEventListener('click', async () => {
      const liveEmail = input.value.trim();
      const liveContact = liveEmail
        ? Object.assign({}, data.contact || {}, { email: liveEmail })
        : data.contact;
      const nextDue = data.sequence && data.sequence.next_due;
      const channel = nextDue ? nextDue.channel : 'email';
      const touchNum = nextDue ? nextDue.touch : 1;

      if (channel === 'email') {
        if (typeof window.__outreachOpenCompose === 'function') {
          window.__outreachOpenCompose(
            parcel, liveContact, data.sender_address, touchNum,
          );
        }
      } else if (channel === 'phone') {
        await openPhoneModal(parcel, liveContact, touchNum);
      } else if (channel === 'mail') {
        await openMailModal(parcel, touchNum);
      }
    });
```

- [ ] **Step 2: Update openComposeModal to honor touch_number**

Find `openComposeModal` in the same file. Add `touchNumber` as a parameter (default 1) and pass it through the send call.

Change the signature:

```javascript
  async function openComposeModal(parcel, contact, senderAddress, touchNumber) {
    touchNumber = touchNumber || 1;
```

Find the templates fetch + template selection logic. The current code lets the user pick any template. We want to default to the template for the current touch_number. Right after the `applyTemplate(0);` line, replace it with:

```javascript
    // Default to the cadence template for this touch if it's in the list.
    let cadenceCfg = null;
    try { cadenceCfg = await getCadenceConfig(); } catch (_) {}
    const cadenceTouch = cadenceCfg && cadenceCfg.sequence
      ? cadenceCfg.sequence.find(t => t.touch === touchNumber) : null;
    const defaultIdx = cadenceTouch
      ? Math.max(0, templates.findIndex(t => t.name === cadenceTouch.template))
      : 0;
    tplSelect.value = String(defaultIdx);
    applyTemplate(defaultIdx);
```

(Replace the existing single-line `applyTemplate(0);` call with this block.)

Also change the send-button handler to include `touch_number: touchNumber`. Find the `sendOutreach({ pin: parcel.pin, to, subject, body })` call and replace with:

```javascript
        await sendOutreach({
          pin: parcel.pin, to, subject, body,
          touch_number: touchNumber,
        });
```

- [ ] **Step 3: Add openPhoneModal and openMailModal**

Add these new functions inside the IIFE, near `openComposeModal`:

```javascript
  async function fetchTemplateRendered(pin, templateName) {
    const resp = await fetch(`/api/outreach/templates?pin=${encodeURIComponent(pin)}`);
    if (!resp.ok) throw new Error('fetch templates failed');
    const data = await resp.json();
    return (data.templates || []).find(t => t.name === templateName);
  }

  async function logManualTouch(payload) {
    const resp = await fetch('/api/outreach/log-manual-touch', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(text || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  function buildManualModal(title, bodyText, channel, parcel, touchNumber, scriptLabel) {
    const root = document.createElement('div');
    root.id = 'outreach-modal-root';
    root.className = 'outreach-modal-backdrop';
    root.innerHTML = `
      <div class="outreach-modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)}">
        <div class="outreach-modal-head">
          <h3>${escapeHtml(title)}</h3>
          <button type="button" class="btn btn-sm" id="manual-modal-close">Close</button>
        </div>
        <div class="outreach-modal-body">
          <div class="cm-row">
            <label class="cm-label">${escapeHtml(scriptLabel)}</label>
            <textarea readonly class="manual-template-text">${escapeHtml(bodyText)}</textarea>
          </div>
          <div class="cm-row">
            <label class="cm-label" for="manual-notes">Notes (optional)</label>
            <textarea id="manual-notes" placeholder="What happened on this touch?"></textarea>
          </div>
        </div>
        <div class="outreach-modal-foot">
          <span class="outreach-modal-error" id="manual-error"></span>
          <button type="button" class="btn" id="manual-copy-btn">Copy to clipboard</button>
          <button type="button" class="btn" id="manual-skip-btn" title="Mark this touch as skipped, no action taken">Skip touch</button>
          <button type="button" class="btn btn-primary" id="manual-complete-btn">Mark complete</button>
        </div>
      </div>
    `;
    document.body.appendChild(root);

    function onKey(ev) { if (ev.key === 'Escape') onClose(); }
    function onClose() {
      document.removeEventListener('keydown', onKey);
      root.remove();
    }
    document.addEventListener('keydown', onKey);
    root.querySelector('#manual-modal-close').addEventListener('click', onClose);
    root.addEventListener('click', e => { if (e.target === root) onClose(); });

    root.querySelector('#manual-copy-btn').addEventListener('click', () => {
      navigator.clipboard.writeText(bodyText).then(
        () => showToast('Copied to clipboard', 'success'),
        () => showToast("Couldn't copy", 'error'),
      );
    });

    const completeBtn = root.querySelector('#manual-complete-btn');
    const skipBtn = root.querySelector('#manual-skip-btn');
    const errSpan = root.querySelector('#manual-error');

    async function submit(actualChannel, successLabel) {
      const notes = root.querySelector('#manual-notes').value;
      errSpan.textContent = '';
      completeBtn.disabled = true; skipBtn.disabled = true;
      try {
        await logManualTouch({
          pin: parcel.pin, touch_number: touchNumber,
          channel: actualChannel, notes,
        });
        onClose();
        showToast(successLabel, 'success');
        window.dispatchEvent(new CustomEvent('outreach:refresh',
                                              { detail: { pin: parcel.pin } }));
      } catch (e) {
        errSpan.textContent = e.message || 'save failed';
        completeBtn.disabled = false; skipBtn.disabled = false;
      }
    }

    completeBtn.addEventListener('click',
      () => submit(channel, `Touch ${touchNumber} logged`));
    skipBtn.addEventListener('click',
      () => submit('skipped', `Touch ${touchNumber} skipped`));
  }

  async function openPhoneModal(parcel, contact, touchNumber) {
    const cadenceCfg = await getCadenceConfig();
    const touch = cadenceCfg.sequence.find(t => t.touch === touchNumber);
    if (!touch) { showToast('No cadence config for that touch', 'error'); return; }
    let tpl;
    try { tpl = await fetchTemplateRendered(parcel.pin, touch.template); }
    catch (_) { showToast("Couldn't load phone template", 'error'); return; }
    const body = (tpl && (tpl.rendered_body || tpl.body)) || '';
    const phone = (contact && contact.phone) || 'no phone on file';
    buildManualModal(
      `Phone call — touch ${touchNumber} of ${cadenceCfg.sequence.length} · ${phone}`,
      body,
      'phone', parcel, touchNumber,
      'Script (conversational, not verbatim)',
    );
  }

  async function openMailModal(parcel, touchNumber) {
    const cadenceCfg = await getCadenceConfig();
    const touch = cadenceCfg.sequence.find(t => t.touch === touchNumber);
    if (!touch) { showToast('No cadence config for that touch', 'error'); return; }
    let tpl;
    try { tpl = await fetchTemplateRendered(parcel.pin, touch.template); }
    catch (_) { showToast("Couldn't load mail template", 'error'); return; }
    const body = (tpl && (tpl.rendered_body || tpl.body)) || '';
    const channelLabel = touchNumber === 6 ? 'Postcard' : 'Letter';
    buildManualModal(
      `${channelLabel} — touch ${touchNumber} of ${cadenceCfg.sequence.length}`,
      body,
      'mail', parcel, touchNumber,
      `${channelLabel} body (copy + print + mail manually for now)`,
    );
  }
```

- [ ] **Step 4: Add CSS for the manual modal**

Append to `webapp/static/css/style.css`:

```css
/* ===== Manual touch modal ===== */
.manual-template-text {
  min-height: 180px;
  background: #0d1117;
  color: #c9d1d9;
  border: 1px solid #30363d;
  border-radius: 4px;
  padding: 8px;
  font-size: 13px;
  font-family: inherit;
  resize: vertical;
}
#manual-notes {
  min-height: 80px;
}
```

- [ ] **Step 5: Sanity check**

```bash
.venv/bin/python -c "
from webapp.app import create_app
from pathlib import Path
db = Path('data/full.alt.db') if Path('data/full.alt.db').exists() else Path('data/smoke.db')
app = create_app(db_path=db, feature_outreach=True)
js = app.test_client().get('/static/js/outreach.js').get_data(as_text=True)
assert 'openPhoneModal' in js
assert 'openMailModal' in js
assert 'logManualTouch' in js
print('ok')
"
```

Expected: `ok`.

- [ ] **Step 6: Run pytest**

```bash
.venv/bin/python -m pytest -q
```

Expected: 316 passed.

- [ ] **Step 7: Commit**

```bash
git add webapp/static/js/outreach.js webapp/static/css/style.css
git commit -m "feat(cadence): channel-aware compose — phone and mail manual modals"
```

---

## Task 12: Daily digest CLI `pipeline/due_digest.py`

**Files:**
- Create: `chicago-pipeline/pipeline/due_digest.py`
- Create: `chicago-pipeline/tests/test_pipeline_due_digest.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_pipeline_due_digest.py`:

```python
"""Tests for pipeline/due_digest.py — daily Due Today digest CLI."""
from __future__ import annotations
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.db import init_db
from pipeline.due_digest import build_digest, send_digest, main


CADENCE_YAML = """
sequence:
  - {touch: 1, day_offset: 0, channel: email, template: t1, requires: email}
  - {touch: 2, day_offset: 3, channel: email, template: t2, requires: email}
end_of_sequence_grace_days: 0
"""


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "t.db"
    init_db(p)
    conn = sqlite3.connect(p)
    conn.execute(
        "INSERT INTO parcels (pin, address, owner_name, mail_address, stage) "
        "VALUES (?, ?, ?, ?, ?)",
        ("14210010010000", "123 W Main", "JANE DOE", "500 N Main", "outreach"),
    )
    conn.execute(
        "INSERT INTO contacts (pin, email, source) VALUES (?, ?, ?)",
        ("14210010010000", "jane@example.com", "manual"),
    )
    conn.execute(
        "INSERT INTO outreach (pin, touch_number, channel, sent_date) "
        "VALUES (?, ?, ?, ?)",
        ("14210010010000", 1, "email", "2026-05-08T09:00:00Z"),
    )
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def cadence_path(tmp_path):
    p = tmp_path / "cadence.yaml"
    p.write_text(CADENCE_YAML)
    return p


def test_build_digest_returns_none_when_no_due(db_path, cadence_path):
    """A date before touch 2 is due → no digest."""
    text = build_digest(db_path, cadence_path, date(2026, 5, 9), app_url="http://x")
    assert text is None


def test_build_digest_returns_text_when_due_non_empty(db_path, cadence_path):
    text = build_digest(db_path, cadence_path, date(2026, 5, 11),
                        app_url="http://localhost:5051/")
    assert text is not None
    assert "DUE TODAY" in text
    assert "123 W Main" in text
    assert "jane@example.com" in text
    assert "http://localhost:5051/" in text


def test_send_digest_dry_run_prints(db_path, cadence_path, capsys):
    main(["--db", str(db_path), "--config", str(cadence_path),
          "--today", "2026-05-11", "--dry-run"])
    captured = capsys.readouterr()
    assert "DUE TODAY" in captured.out


def test_send_digest_invokes_gmail_when_due(db_path, cadence_path, tmp_path):
    with patch("pipeline.due_digest.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "msg-1", "threadId": "thr-1"}
        main(["--db", str(db_path), "--config", str(cadence_path),
              "--today", "2026-05-11",
              "--sender", "me@example.com",
              "--token-path", str(tmp_path / "token.json")])
    assert send_mock.call_count == 1
    kwargs = send_mock.call_args.kwargs
    assert kwargs["sender"] == "me@example.com"
    assert kwargs["to"] == "me@example.com"  # sends to self
    assert "DUE TODAY" in kwargs["body"]


def test_send_digest_skips_send_when_empty(db_path, cadence_path, tmp_path):
    with patch("pipeline.due_digest.gmail_client.send_email") as send_mock:
        main(["--db", str(db_path), "--config", str(cadence_path),
              "--today", "2026-05-09",
              "--sender", "me@example.com",
              "--token-path", str(tmp_path / "token.json"),
              "--last-run-path", str(tmp_path / "last_run.txt")])
    assert send_mock.call_count == 0


def test_send_digest_writes_last_run_sentinel(db_path, cadence_path, tmp_path):
    """Every non-dry-run invocation touches the sentinel — observability
    for 'did the cron fire today?'"""
    sentinel = tmp_path / "last_run.txt"
    with patch("pipeline.due_digest.gmail_client.send_email") as send_mock:
        send_mock.return_value = {"id": "m", "threadId": "t"}
        main(["--db", str(db_path), "--config", str(cadence_path),
              "--today", "2026-05-11",
              "--sender", "me@example.com",
              "--token-path", str(tmp_path / "token.json"),
              "--last-run-path", str(sentinel)])
    assert sentinel.exists()
    content = sentinel.read_text()
    # Should be an ISO-8601 UTC timestamp
    assert content.startswith("20")
    assert content.endswith("Z")


def test_send_digest_writes_sentinel_even_when_nothing_due(db_path, cadence_path, tmp_path):
    """Sentinel must update on empty-day runs too, otherwise empty days
    look like missed runs."""
    sentinel = tmp_path / "last_run.txt"
    with patch("pipeline.due_digest.gmail_client.send_email") as send_mock:
        main(["--db", str(db_path), "--config", str(cadence_path),
              "--today", "2026-05-09",   # nothing due
              "--sender", "me@example.com",
              "--token-path", str(tmp_path / "token.json"),
              "--last-run-path", str(sentinel)])
    assert sentinel.exists()
    assert send_mock.call_count == 0
```

Run: `.venv/bin/python -m pytest tests/test_pipeline_due_digest.py -v`

Expected: all FAIL (module not implemented).

- [ ] **Step 2: Implement pipeline/due_digest.py**

Create `pipeline/due_digest.py`:

```python
"""Daily Due Today digest — emails Hunter a summary of pending touches.

CLI:
    python -m pipeline.due_digest \\
        --db data/full.alt.db \\
        --config config/outreach_cadence.yaml \\
        [--today YYYY-MM-DD] [--dry-run]

Phase A: wired to local launchd at 9am daily. Phase B: replaced by a Railway
cron. The script is read-only on the DB (no writes) and uses the existing
Gmail OAuth token to send.
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

from pipeline import cadence as cadence_module
from pipeline import gmail_client


def build_digest(
    db_path: Path,
    cadence_path: Path,
    today: date,
    app_url: str,
) -> str | None:
    """Compute the digest body for `today`. Returns None when nothing is due
    (caller should skip sending)."""
    cadence = cadence_module.load_cadence_config(cadence_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        result = cadence_module.all_due_touches(conn, cadence, today)
    finally:
        conn.close()
    groups = result.get("groups") or []
    if not groups:
        return None

    lines = []
    counts = [f"{g['count']} {g['channel'].replace('_', ' ')}" for g in groups]
    lines.append(f"DUE TODAY · {today.isoformat()}")
    lines.append("")

    channel_titles = {
        "email": "Emails",
        "phone": "Phone calls",
        "mail": "Mail",
        "end_of_sequence": "End of sequence",
    }
    for g in groups:
        title = channel_titles.get(g["channel"], g["channel"])
        lines.append(f"{title} ({g['count']}):")
        for it in g["items"]:
            if g["channel"] == "end_of_sequence":
                lines.append(
                    f"  • {it['address']} — sent last touch "
                    f"{it['last_touch_date']}, {it['days_since_last']}d ago. "
                    "Mark as dead?"
                )
            else:
                overdue = f" [+{it['days_overdue']} overdue]" if it["days_overdue"] > 0 else ""
                contact_info = ""
                if g["channel"] == "email":
                    contact_info = f", to: {it.get('to_email', '?')}"
                elif g["channel"] == "phone":
                    contact_info = f", phone: {it.get('to_phone', '?')}"
                lines.append(
                    f"  • {it['address']} — touch {it['touch']} "
                    f"({it['template']}){contact_info}{overdue}"
                )
        lines.append("")

    # Active outreach count (parcels in outreach stage) — reminds the user
    # to scan their inbox for replies before approving the next touch.
    conn = sqlite3.connect(db_path)
    try:
        active = conn.execute(
            "SELECT COUNT(*) FROM parcels WHERE stage = 'outreach' "
            "AND COALESCE(outreach_paused, 0) = 0"
        ).fetchone()[0]
    finally:
        conn.close()
    lines.append(
        f"Reminder: scan your inbox for replies from parcels in active outreach "
        f"before approving the next touch. Active outreach parcels: {active}."
    )
    lines.append("")
    lines.append(f"Open the app: {app_url}")
    return "\n".join(lines)


def send_digest(
    body: str,
    *,
    sender: str,
    token_path: Path,
    today: date,
) -> dict:
    """Send the digest via Gmail. `to` is the same as `sender` (you mail
    yourself)."""
    counts_line = body.split("\n", 2)[0]  # "DUE TODAY · YYYY-MM-DD"
    # Count totals for the subject
    total = sum(
        int(part.split()[0])
        for part in body.split("\n")
        if part.startswith(("  • ",))
    ) if False else None  # simpler: count bullets
    bullets = body.count("\n  • ")
    subject = f"Chicago pipeline — {bullets} touches due today"
    return gmail_client.send_email(
        token_path=token_path,
        sender=sender,
        to=sender,
        subject=subject,
        body=body,
    )


DEFAULT_LAST_RUN_PATH = Path("data/due_digest_last_run.txt")


def write_last_run_sentinel(path: Path) -> None:
    """Write the current timestamp to the last-run sentinel file. The UI
    reads this via /api/health/digest to surface stale-cron warnings."""
    from datetime import datetime, timezone
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send the Due Today digest email")
    parser.add_argument("--db", type=Path,
                        default=Path(os.environ.get("PIPELINE_DB_PATH", "data/full.alt.db")))
    parser.add_argument("--config", type=Path,
                        default=Path("config/outreach_cadence.yaml"))
    parser.add_argument("--today", type=str, default=None,
                        help="Override today's date (YYYY-MM-DD), for testing")
    parser.add_argument("--app-url", default="http://localhost:5051/",
                        help="URL to surface in the digest email body")
    parser.add_argument("--sender", default=os.environ.get("GMAIL_SENDER_ADDRESS", ""),
                        help="Gmail address to send from (and receive at)")
    parser.add_argument("--token-path", type=Path,
                        default=Path(os.environ.get("GMAIL_TOKEN_PATH", "data/gmail_token.json")))
    parser.add_argument("--last-run-path", type=Path, default=DEFAULT_LAST_RUN_PATH,
                        help="File the digest touches on every run for observability")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the digest to stdout instead of sending")
    args = parser.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    body = build_digest(args.db, args.config, today, args.app_url)
    # Always update the last-run sentinel — even for "nothing due" runs. The
    # UI uses this to detect a stalled cron (e.g., Mac off for days). If we
    # only wrote it on send-days, "nothing due" days would look like
    # missed runs.
    if not args.dry_run:
        write_last_run_sentinel(args.last_run_path)
    if body is None:
        if args.dry_run:
            print(f"# Nothing due on {today.isoformat()}; would not send.")
        return 0
    if args.dry_run:
        print(body)
        return 0
    if not args.sender:
        print("ERROR: --sender (or GMAIL_SENDER_ADDRESS env var) is required",
              file=sys.stderr)
        return 2
    send_digest(body, sender=args.sender, token_path=args.token_path, today=today)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Run digest tests + full suite**

```bash
.venv/bin/python -m pytest tests/test_pipeline_due_digest.py -v
.venv/bin/python -m pytest -q
```

Expected: 7 digest tests pass (5 prior + 2 sentinel tests); full suite 318 + 7 = 325.

- [ ] **Step 4: Commit**

```bash
git add pipeline/due_digest.py tests/test_pipeline_due_digest.py
git commit -m "feat(cadence): daily Due Today digest CLI (pipeline.due_digest)"
```

---

## Task 13: Launchd installer + backup script + observability endpoint + README

**Files:**
- Create: `chicago-pipeline/scripts/install_due_digest_launchd.sh`
- Create: `chicago-pipeline/scripts/com.chicagopipeline.duedigest.plist.template`
- Create: `chicago-pipeline/scripts/backup_outreach.sh`
- Modify: `chicago-pipeline/webapp/routes.py` (add `/api/health/digest`)
- Modify: `chicago-pipeline/webapp/app.py` (add `DUE_DIGEST_LAST_RUN_PATH` config key)
- Modify: `chicago-pipeline/webapp/static/js/outreach.js` (surface last-run timestamp)
- Modify: `chicago-pipeline/webapp/static/css/style.css` (style the warning)
- Modify: `chicago-pipeline/tests/test_webapp_cadence_routes.py` (test the new endpoint)
- Modify: `chicago-pipeline/.gitignore` (ignore the new sentinel + log files)
- Modify: `chicago-pipeline/README.md`

- [ ] **Step 1: Create the launchd plist template**

Create `chicago-pipeline/scripts/com.chicagopipeline.duedigest.plist.template`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.chicagopipeline.duedigest</string>

    <key>ProgramArguments</key>
    <array>
        <string>__PROJECT_DIR__/.venv/bin/python</string>
        <string>-m</string>
        <string>pipeline.due_digest</string>
        <string>--db</string>
        <string>__PROJECT_DIR__/data/full.alt.db</string>
        <string>--config</string>
        <string>__PROJECT_DIR__/config/outreach_cadence.yaml</string>
    </array>

    <key>WorkingDirectory</key>
    <string>__PROJECT_DIR__</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>GMAIL_SENDER_ADDRESS</key>
        <string>__SENDER__</string>
        <key>GMAIL_TOKEN_PATH</key>
        <string>__PROJECT_DIR__/data/gmail_token.json</string>
    </dict>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>__PROJECT_DIR__/data/due_digest.log</string>
    <key>StandardErrorPath</key>
    <string>__PROJECT_DIR__/data/due_digest.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Create the install script**

Create `chicago-pipeline/scripts/install_due_digest_launchd.sh`:

```bash
#!/usr/bin/env bash
# Install the Due Today digest as a launchd job that fires daily at 9am
# local time. Reads GMAIL_SENDER_ADDRESS from .env or arg 1.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$PROJECT_DIR/scripts/com.chicagopipeline.duedigest.plist.template"
PLIST_DEST="$HOME/Library/LaunchAgents/com.chicagopipeline.duedigest.plist"

SENDER="${1:-}"
if [ -z "$SENDER" ] && [ -f "$PROJECT_DIR/.env" ]; then
    SENDER=$(grep '^GMAIL_SENDER_ADDRESS=' "$PROJECT_DIR/.env" | cut -d= -f2-)
fi
if [ -z "$SENDER" ]; then
    echo "ERROR: GMAIL_SENDER_ADDRESS not provided. Pass as arg 1 or set in .env." >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    -e "s|__SENDER__|$SENDER|g" \
    "$TEMPLATE" > "$PLIST_DEST"

# Unload first in case it's already loaded
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load -w "$PLIST_DEST"

echo "Installed: $PLIST_DEST"
echo "Daily digest will fire at 9:00 local time."
echo ""
echo "Verify with: launchctl list | grep chicagopipeline"
echo "Test now with: $PROJECT_DIR/.venv/bin/python -m pipeline.due_digest --dry-run"
echo "Uninstall with: launchctl unload $PLIST_DEST && rm $PLIST_DEST"
```

Make it executable:

```bash
cd /Users/hunterheyman/Claude/chicago-pipeline
chmod +x scripts/install_due_digest_launchd.sh
```

- [ ] **Step 3: Create the backup script**

Create `chicago-pipeline/scripts/backup_outreach.sh`:

```bash
#!/usr/bin/env bash
# Dumps just the outreach/contacts/waves tables from the working DB into
# a timestamped backup file under data/. Run manually before any risky
# operation, or wire to launchd for daily backups.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:-$PROJECT_DIR/data/full.alt.db}"
TIMESTAMP="$(date +%Y-%m-%d-%H%M%S)"
DEST="$PROJECT_DIR/data/outreach_backup_${TIMESTAMP}.sql"

if [ ! -f "$SRC" ]; then
    echo "ERROR: source DB not found: $SRC" >&2
    exit 1
fi

# Dump only the outreach-related tables. SQL text file is human-readable
# and small (~few KB at 10-20/wk volume). To restore: sqlite3 <new.db>
# < $DEST after init_db creates the schema.
sqlite3 "$SRC" \
    ".dump outreach" \
    ".dump contacts" \
    ".dump waves" \
    > "$DEST"

# Keep only the last 30 daily backups — older than that gets pruned.
find "$PROJECT_DIR/data" -name 'outreach_backup_*.sql' -type f \
    | sort -r | tail -n +31 | xargs -I {} rm -f {}

echo "Backup written: $DEST"
echo "Restore with: sqlite3 <new.db> < $DEST  (after init_db creates the schema)"
```

Make it executable:

```bash
chmod +x scripts/backup_outreach.sh
```

Test it:

```bash
./scripts/backup_outreach.sh
ls -la data/outreach_backup_*.sql | head -3
```

Expected: at least one `.sql` backup file in `data/`.

Optionally wire to launchd as a sibling job (8:55am daily, 5 min before the digest). Create `scripts/com.chicagopipeline.backup.plist.template` if you want automated daily backups, or just run the script manually before any risky operation.

- [ ] **Step 4: Add the observability endpoint `/api/health/digest`**

Open `webapp/app.py` and add a new config key alongside the existing outreach paths. After `app.config["GMAIL_TOKEN_PATH"] = ...`, add:

```python
    app.config["DUE_DIGEST_LAST_RUN_PATH"] = due_digest_last_run_path or Path(
        "data/due_digest_last_run.txt"
    )
```

Add the param to `create_app`'s signature alongside the other Path params:

```python
    due_digest_last_run_path: Path | None = None,
```

Open `webapp/routes.py` and add a new route inside the existing `if app.config["FEATURE_OUTREACH"]:` block (next to `/api/cadence/config`):

```python
        @app.get("/api/health/digest")
        def api_health_digest():
            """Returns the last-known-good timestamp of the daily digest
            cron + a stale flag. Used by the UI to warn when the digest
            hasn't fired (Mac was off, cron is broken, etc.)."""
            from datetime import datetime, timedelta, timezone
            p = Path(app.config["DUE_DIGEST_LAST_RUN_PATH"])
            if not p.exists():
                return jsonify({"last_run": None, "stale": True,
                                "reason": "no sentinel file yet"})
            try:
                ts_text = p.read_text().strip()
                ts = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
            except (ValueError, OSError):
                return jsonify({"last_run": None, "stale": True,
                                "reason": "unparseable sentinel"})
            stale = (datetime.now(timezone.utc) - ts) > timedelta(hours=25)
            return jsonify({"last_run": ts_text, "stale": stale})
```

Add a test in `tests/test_webapp_cadence_routes.py`:

```python
def test_health_digest_no_sentinel_means_stale(app_on):
    resp = app_on.test_client().get("/api/health/digest")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["stale"] is True
    assert data["last_run"] is None


def test_health_digest_recent_sentinel_not_stale(app_on, tmp_path):
    from datetime import datetime, timezone
    sentinel = Path(app_on.config["DUE_DIGEST_LAST_RUN_PATH"])
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    resp = app_on.test_client().get("/api/health/digest")
    data = resp.get_json()
    assert data["stale"] is False
    assert data["last_run"] is not None


def test_health_digest_old_sentinel_is_stale(app_on, tmp_path):
    sentinel = Path(app_on.config["DUE_DIGEST_LAST_RUN_PATH"])
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    # 2 days old
    sentinel.write_text("2026-05-13T09:00:00Z")
    resp = app_on.test_client().get("/api/health/digest")
    data = resp.get_json()
    assert data["stale"] is True
```

The test `app_on` fixture should be updated to pass `due_digest_last_run_path=tmp_path / "last_run.txt"` so tests don't trample on real data — find the existing `app_on` fixture in `test_webapp_cadence_routes.py` and add that arg:

```python
@pytest.fixture
def app_on(db_path, cadence_path, templates_path, tmp_path):
    from datetime import date
    return create_app(
        db_path=db_path, feature_outreach=True,
        outreach_templates_path=templates_path,
        outreach_cadence_path=cadence_path,
        due_digest_last_run_path=tmp_path / "last_run.txt",
        clock=lambda: date(2026, 5, 11),
        gmail_client_secrets_path=tmp_path / "client.json",
        gmail_token_path=tmp_path / "token.json",
        gmail_sender_address="me@example.com",
    )
```

Run the tests:

```bash
.venv/bin/python -m pytest tests/test_webapp_cadence_routes.py -v
```

Expected: 12 prior + 3 new = 15 cadence-route tests pass.

- [ ] **Step 5: Surface the last-run timestamp in the UI**

In `webapp/static/js/outreach.js`, near `renderDueToday`, add a function that fetches digest health and prepends a warning to the Due Today bar when stale:

```javascript
  async function fetchDigestHealth() {
    const resp = await fetch('/api/health/digest');
    if (!resp.ok) return null;
    return resp.json();
  }

  async function renderDigestHealthBadge() {
    const bar = document.getElementById('due-today-bar');
    if (!bar) return;
    let health;
    try { health = await fetchDigestHealth(); } catch (_) { return; }
    if (!health) return;
    // Remove existing badge if present
    const existing = bar.querySelector('.digest-health-badge');
    if (existing) existing.remove();
    if (!health.stale) return;  // healthy → no badge
    const badge = document.createElement('span');
    badge.className = 'digest-health-badge';
    badge.title = health.last_run
      ? `Last digest run: ${health.last_run}`
      : 'Digest has never run';
    badge.textContent = health.last_run
      ? '⚠ Digest stale'
      : '⚠ Digest not running';
    bar.appendChild(badge);
    // Show the bar even if Due Today is empty when the digest is unhealthy
    bar.hidden = false;
  }

  window.addEventListener('DOMContentLoaded', () => { renderDigestHealthBadge(); });
  window.addEventListener('outreach:refresh', () => { renderDigestHealthBadge(); });
```

Add the CSS to `webapp/static/css/style.css`:

```css
.digest-health-badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 10px;
  border-radius: 4px;
  background: #58220e;
  color: #ffd2a8;
  font-size: 11px;
  margin-left: auto;
  cursor: help;
}
```

- [ ] **Step 6: Add the new artifacts to `.gitignore`**

Append to `.gitignore`:

```
# Cadence runtime artifacts — local-only
data/due_digest.log
data/due_digest_last_run.txt
data/outreach_backup_*.sql
```

- [ ] **Step 7: Update README.md with the cadence section**

In `README.md`, append at the very end:

```markdown

## Outreach cadence (7-touch sequence)

The cadence engine surfaces what's due today across all parcels in `outreach` stage. See [docs/superpowers/specs/2026-05-15-outreach-cadence-design.md](docs/superpowers/specs/2026-05-15-outreach-cadence-design.md) for the design.

### Phases

- **Phase A (current):** local cadence, local launchd digest. Mac-on assumed.
- **Phase B (future):** Railway deploy with multi-user auth + always-on cron.

### Daily digest (Phase A)

A launchd job emails you a Due Today summary at 9am local time daily, skipping the email if nothing is due.

**One-time install:**

```bash
.venv/bin/python -m pytest -q   # confirm green
./scripts/install_due_digest_launchd.sh   # reads .env for GMAIL_SENDER_ADDRESS
```

**Test the digest manually:**

```bash
.venv/bin/python -m pipeline.due_digest --dry-run
```

**Uninstall:**

```bash
launchctl unload ~/Library/LaunchAgents/com.chicagopipeline.duedigest.plist
rm ~/Library/LaunchAgents/com.chicagopipeline.duedigest.plist
```

### Editing cadence rules

Edit `config/outreach_cadence.yaml` directly. Changes take effect on the next page load — no restart. Mid-flight edits will shift in-progress parcels (no `cadence_version` snapshotting in v1; document changes in the YAML's comment header).

### Editing touch templates

The compose modal's Save template button writes back to `config/outreach_templates.yaml`. Six new templates ship as drafts; the cold-intro (touch 1) is the previously-shipped version. Edit content directly in the file or via the UI.

### Skipping a touch

If a touch's `requires` field isn't satisfied (e.g., no phone for touch 3), the cadence engine silently skips it — it never surfaces in Due Today. If you have the contact info but choose not to use a channel (e.g., have the phone but don't want to call), open the touch via Compose and click **Skip touch** in the modal. The touch is logged with channel = `skipped` and the cadence advances to the next available touch.

### Backups

Outreach state lives in your local SQLite file (`data/full.alt.db`). Run a backup before any risky operation:

```bash
./scripts/backup_outreach.sh
```

This dumps just the `outreach`, `contacts`, and `waves` tables to a timestamped `.sql` file under `data/`. The last 30 daily backups are kept; older ones are pruned.

### Digest observability

The daily digest writes a sentinel file (`data/due_digest_last_run.txt`) on every successful run. The UI surfaces a "⚠ Digest stale" badge in the Due Today bar when the sentinel is older than 25 hours, or absent. Check `data/due_digest.log` for the underlying error.
```

- [ ] **Step 8: Run pytest + sanity checks**

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pipeline.due_digest --help | head -10
./scripts/backup_outreach.sh
```

Expected: 328 passed (325 + 3 new health-digest tests); help text prints without error; backup script writes a `.sql` file.

- [ ] **Step 9: Commit**

```bash
git add scripts/install_due_digest_launchd.sh \
        scripts/com.chicagopipeline.duedigest.plist.template \
        scripts/backup_outreach.sh \
        webapp/app.py webapp/routes.py \
        webapp/static/js/outreach.js webapp/static/css/style.css \
        tests/test_webapp_cadence_routes.py \
        .gitignore \
        README.md
git commit -m "feat(cadence): launchd, backup script, /api/health/digest, README"
```

---

## Task 14: Browser smoke verification + final commit

**Files:**
- No code changes — verification only.

- [ ] **Step 1: Stop any running server, then start fresh with the new code**

```bash
cd /Users/hunterheyman/Claude/chicago-pipeline
pkill -f "python -m webapp" 2>/dev/null || true
lsof -nP -iTCP:5051 -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $2}' | xargs -I {} kill -9 {} 2>/dev/null || true
sleep 1
set -a; source .env; set +a
.venv/bin/python -m webapp --db data/full.alt.db --port 5051 --outreach &
sleep 2
curl -sf http://127.0.0.1:5051/health && echo " server up"
```

- [ ] **Step 2: Smoke-test the new endpoints via curl**

```bash
PIN=$(curl -s "http://127.0.0.1:5051/api/parcels?limit=1" | python3 -c "import sys,json; print(json.load(sys.stdin)['parcels'][0]['pin'])")
echo "Testing pin: $PIN"

echo "--- GET /api/cadence/config ---"
curl -s http://127.0.0.1:5051/api/cadence/config | python3 -c "import sys,json; d=json.load(sys.stdin); print('touches:', len(d['sequence']))"

echo "--- GET /api/outreach/due (no parcels in outreach yet) ---"
curl -s http://127.0.0.1:5051/api/outreach/due | python3 -m json.tool

echo "--- GET /api/parcels/<pin>/outreach includes sequence block ---"
curl -s "http://127.0.0.1:5051/api/parcels/$PIN/outreach" | python3 -c "import sys,json; d=json.load(sys.stdin); print('has sequence block:', 'sequence' in d)"
```

Expected:
- cadence config returns 7 touches
- due is `{"today": "...", "groups": []}` (no parcels in outreach stage yet)
- parcel/outreach response has a sequence block

- [ ] **Step 3: Walk through the UI in a browser**

Open `http://localhost:5051/` in a browser. Verify:

1. **Top bar** still renders with score version + count. Due Today banner only appears if something is actually due (or a digest staleness warning fires).
2. **Pick a parcel.** Detail panel shows: Stage, **Sequence** ("No outreach yet."), Contact, Outreach History sections. The Sequence section says "No outreach yet — Compose touch 1 to start the cadence."
3. **Add an email** in the Contact section, click outside → "Email saved" toast.
4. **Click "Compose email…"** — modal opens for touch 1 with the `initial-cold` template selected.
5. **Type a real test email** (your own) and click Send. Confirm:
   - Toast "Email sent"
   - Stage flips to `outreach`
   - Sequence section now shows "Touch 1 of 7" with the 1st row checked ✓ and the 2nd row marked ●
   - Due Today banner appears at top — but only after day 3 from touch 1
6. **Wait 3+ days OR set the clock for a one-off probe:** to verify Due Today, temporarily override the clock in a Python shell against the running app's process (or simulate by directly inserting an old `sent_date` via sqlite). The browser path is to actually wait the days, then refresh and confirm your parcel shows in the email group.
7. **Click "Pause cadence"** — toast confirms; refresh the page; Sequence section shows the "paused" badge; Due Today banner no longer surfaces this parcel.
8. **Click "Resume cadence"** — paused badge clears.
9. **Send touch 2** by clicking Compose again (now defaults to touch 2 template) — verify the Sequence section updates.
10. **Try a phone touch (touch 3) manually:** with touch 2 sent and 7+ days elapsed (or fake by inserting an old touch_1 sent_date), click Compose — phone modal opens, shows the script, click "Mark complete" → toast, Sequence updates.
11. **Try Skip touch:** in the same phone modal, click "Skip touch" instead — outreach row records with channel=`skipped`, cadence advances.
12. **Run the digest manually:** `.venv/bin/python -m pipeline.due_digest --dry-run` — prints the digest body or "Nothing due."
13. **Verify the health endpoint:** `curl http://127.0.0.1:5051/api/health/digest` — returns `{"last_run": null, "stale": true, ...}` initially. Run `pipeline.due_digest` once for real (or use `--dry-run` after Step 5 of T12 adds the sentinel-write — confirm dry-run also touches the sentinel). Re-curl: `stale` becomes `false`.
14. **Run the backup script:** `./scripts/backup_outreach.sh` — writes `data/outreach_backup_*.sql`. Restore-readiness can be eyeballed by `head data/outreach_backup_*.sql`.

If any of those steps misbehave, screenshot and pin down which step before moving on.

- [ ] **Step 4: Stop the server**

```bash
pkill -f "python -m webapp" 2>/dev/null || true
```

- [ ] **Step 5: Final pytest + lint pass**

```bash
.venv/bin/python -m pytest -q
```

Expected: 328 passed, 0 failed.

- [ ] **Step 6: Branch summary commit (optional — empty or doc-only)**

If anything came up during smoke testing that needed a small fix, commit it now with a clear message. Otherwise skip.

- [ ] **Step 7: Push the branch**

```bash
git push -u origin outreach-cadence
```

Branch is ready to merge.

---

## Self-review

After all 14 tasks land, run this self-check:

1. **Spec coverage:** The spec at `docs/superpowers/specs/2026-05-15-outreach-cadence-design.md` lists every requirement. For each section (Architecture, Cadence YAML, Templates, Cadence engine, Schema, API surface, UI, Digest, Sequence rules), point at a task. All accounted for in T1-T14.

2. **Placeholder scan:** Read the plan top to bottom. Any "TBD", vague "handle edge cases", incomplete code blocks, missing test bodies? Fix inline.

3. **Type consistency:** `cadence_config` dict shape is identical across `load_cadence_config` return, `next_due_touches_for_parcel` arg, `is_end_of_sequence` arg, and `all_due_touches` arg. `outreach_rows` shape (list of dicts with `touch_number`, `sent_date`) is the same everywhere. `validate_next_due_touch` is the only new outreach.py export.

4. **Test count math:** 276 start → +3 (T2) → +13 (T3) → +5 (T4) → +4 (T5) → +4 (T6) → +8 (T7) → +5 (T8) → +7 (T12) → +3 (T13) = 328 final.

If any check fails, add the missing task or fix the existing one inline; no need for a second review pass.
