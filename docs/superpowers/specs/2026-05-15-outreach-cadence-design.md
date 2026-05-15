# Outreach Cadence Engine — Design

**Status:** Draft, 2026-05-15. Design choices approved by Hunter in conversation; this written form is pending one final read before the implementation plan is generated.

**Goal:** Add a 7-touch outreach cadence on top of the shipped single-touch email machinery. The Flask app surfaces a "Due Today" view of what touches are due across all parcels in `outreach` stage; the user reviews and approves every touch (Path A). A daily digest emails Hunter a summary so he knows when to open the app. No auto-send.

**Phasing:**
- **Phase A — local cadence first.** Build cadence engine, Due Today UI, sequence timeline, channel-aware compose, manual touch logging, pause flag, and a daily digest cron via local launchd. Run for 2-3 weeks at 10-20 parcels/week to validate the cadence shape. **This is the scope of the implementation plan that follows this spec.**
- **Phase B — Railway migration.** Multi-user auth (David sees no outreach, Hunter does), outreach data on Railway's persistent volume, Gmail token on Railway, Railway cron replaces local launchd. Phase B is a separate plan triggered after Phase A validates the cadence design.

---

## Architecture

### 1. Stateless cadence

The Flask app computes Due Today on every page load — no background jobs, no state machine. The query reads `outreach.sent_date` per parcel + a YAML cadence config, applies the schedule rules, and returns what's due. Same model as today's app, just with more sophisticated read logic.

### 2. Per-parcel anchor at `touch_1.sent_date`

Each parcel has its own cadence clock. The schedule for parcel P is anchored at the date you first emailed P (touch 1). Subsequent target dates are `touch_1.sent_date + day_offset` per touch. Missed touches show as overdue but don't shift future ones.

**Hard rule: cadence requires email at start.** If a parcel has no `contact.email`, it can't enter cadence — `next_due_touches_for_parcel` returns `[]`. To do mail-only outreach on an emailless parcel, the user handles that as a standalone manual touch outside the cadence (no schedule, no anchor). This is a deliberate simplification away from the older 2026-04-13 spec's "mail is the guaranteed fallback channel" language, because supporting cadence-without-touch-1 forces a messy anchor model and the volume doesn't justify it.

### 3. No `waves` concept in v1

The `outreach.wave_id` column stays NULL. Wave-level batch metrics (the "Feedback Report") are deferred indefinitely. The column is left in place for forward compatibility.

### 4. Local launchd digest (Phase A) → Railway cron (Phase B)

`python -m pipeline.due_digest` is a CLI that reads Due Today, formats an email summary, and sends via the existing Gmail OAuth token. Wired to local launchd in Phase A, Railway cron in Phase B. Daily 9am local time. Skips the email when Due Today is empty.

Digest also includes a "Parcels in active outreach — check your inbox for replies" reminder so Hunter can manually mark replies before the next touch fires. Reply auto-detection (IMAP polling) is explicitly deferred per Hunter's call.

---

## Cadence YAML — `config/outreach_cadence.yaml`

```yaml
# Per-touch config. Editing this file changes future schedule computations
# immediately (no recompile). Mid-flight edits shift in-progress parcels —
# cadence_version snapshotting is deferred. Document any change in the YAML
# comment header so future-you remembers when the rules shifted.
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
end_of_sequence_action: surface_for_dead   # day 30+ → digest prompts "mark dead?"
end_of_sequence_grace_days: 0
```

The `requires` field maps to:
- `email` → `contacts.email` IS NOT NULL
- `phone` → `contacts.phone` IS NOT NULL
- `mail_address` → always true (`parcels.mail_address` is sourced from the Cook County Assessor for ~98% of parcels)

---

## Templates — 6 new entries in `config/outreach_templates.yaml`

The existing `Save template` UI shipped in the previous release writes to this file already. Hunter can edit any of these in-place via the compose modal's "Save template" button.

| Name | Channel | Day | Purpose |
|---|---|---|---|
| `initial-cold` (exists) | email | 0 | Personalized cold intro |
| `email-followup-3day` | email | 3 | Short follow-up, different angle |
| `phone-script` | phone | 7 | Conversational talking points, not verbatim |
| `letter-day-14` | mail | 14 | Personal letter; handwritten font in Phase B with Lob |
| `email-new-angle-19` | email | 19 | Market-commentary hook; signals sequence nearing end |
| `postcard-day-24` | mail | 24 | Brief reminder, ~40 words for 4x6 postcard |
| `email-warm-close-30` | email | 30 | Final note, explicit close, no follow-up promise |

Draft content for all 6 is in the conversation transcript at the time of approval (humanizer-audited). Implementation plan ships them as the initial YAML content; Hunter iterates via Save template as he uses the cadence.

---

## Cadence engine — `pipeline/cadence.py`

Three pure functions over args plus one DB-touching orchestrator. Pure → unit-testable without fixtures.

```python
def load_cadence_config(path: Path) -> dict:
    """Load and validate config/outreach_cadence.yaml. Returns a dict with
    keys `sequence` (list of touch configs) and `end_of_sequence_*`."""

def next_due_touches_for_parcel(
    *,
    cadence_config: dict,
    outreach_rows: list[dict],  # all rows for this parcel, ordered by touch_number
    contact: dict | None,       # has 'email', 'phone' or None
    today: date,
) -> list[dict]:
    """Returns list of touch configs that are due/overdue. Each item adds
    `target_date` (computed) and `days_overdue` (0 if today, positive if
    past). Returns [] if no touch_1 row exists (no anchor)."""

def is_end_of_sequence(
    *,
    cadence_config: dict,
    outreach_rows: list[dict],
    today: date,
) -> bool:
    """True when all touches are done AND grace period elapsed since the
    last one — surfaces 'mark dead?' suggestion."""

def all_due_touches(conn, cadence_config: dict, today: date) -> dict:
    """DB-touching orchestrator. Returns the structure shown below."""
```

### Engine rules

1. **Anchor:** the `sent_date` on the row where `touch_number = 1`. No touch 1 → no due touches (parcel not in cadence).
2. **Skip already-done:** if a row exists with `touch_number = N`, touch N is never resurfaced.
3. **Skip not-yet-due:** if `target_date > today`, the touch is not yet due (no preview).
4. **Skip missing contact:** if the touch's `requires` field isn't satisfied by the contact + parcel data, the touch is silently skipped. (No "you can't send touch 3, find the phone" prompt — it just doesn't appear in Due Today.)
5. **Order:** the returned list is sorted by touch number.
6. **`outreach_paused = 1` → empty list.** Paused parcels never surface.
7. **`stage != 'outreach'` → empty list.** Only parcels in the `outreach` stage are considered.

### `all_due_touches` response shape

```json
{
  "today": "2026-05-15",
  "groups": [
    {
      "channel": "email",
      "count": 3,
      "items": [
        {
          "pin": "14210010010000",
          "address": "123 W Main St",
          "owner_first_name": "John",
          "owner_name": "JOHN SMITH",
          "touch": 2,
          "template": "email-followup-3day",
          "target_date": "2026-05-12",
          "days_overdue": 3,
          "to_email": "js@example.com"
        }
      ]
    },
    {"channel": "phone", "count": 1, "items": [...]},
    {"channel": "mail",  "count": 2, "items": [...]},
    {
      "channel": "end_of_sequence",
      "count": 1,
      "items": [
        {"pin": "...", "address": "...", "last_touch_date": "...",
         "days_since_last": 31, "suggest": "mark_dead"}
      ]
    }
  ]
}
```

Ordering: email → phone → mail → end_of_sequence. Groups with `count = 0` are omitted.

---

## Schema additions

Two changes to `pipeline/db.py`, applied via the existing runtime-migration pattern (the codebase already adds late columns this way):

1. **`parcels.outreach_paused INTEGER DEFAULT 0`** — pause flag, set/cleared via the new pause endpoint.
2. **Partial unique index on `outreach(pin, touch_number)`** — prevents race-duplicate touches from concurrent sends. Partial (`WHERE touch_number IS NOT NULL`) so legacy rows without touch numbers don't conflict.

No other schema changes. `outreach.wave_id` stays NULL.

---

## API surface

### New endpoints

All gated by `FEATURE_OUTREACH` (Phase A) → by `g.is_outreach_user` after Phase B migration.

- **`GET /api/outreach/due`** — returns the structure above.
- **`POST /api/outreach/log-manual-touch`** — records phone or mail touch completion (no Gmail send).
- **`POST /api/parcels/<pin>/pause`** — body `{"paused": true|false}`, toggles `parcels.outreach_paused`.
- **`GET /api/cadence/config`** — read-only YAML view for UI rendering (template names, day offsets, etc.).

### Modified endpoints

- **`POST /api/outreach/send`** — gains optional `touch_number` (defaults to 1). Server validates the touch is actually next-due (refuses out-of-order sends with 400). On insert, the partial unique index catches duplicates → returns 409.
- **`GET /api/parcels/<pin>/outreach`** — response gains a `sequence` block:
  ```json
  {
    "sequence": {
      "anchor_date": "2026-05-08",
      "current_touch": 2,
      "next_due": {
        "touch": 3, "channel": "phone",
        "target_date": "2026-05-15", "days_overdue": 0,
        "available": true
      },
      "is_end_of_sequence": false,
      "is_paused": false
    }
  }
  ```

### `POST /api/outreach/log-manual-touch` shape

```json
// Request
{"pin": "14210010010000", "touch_number": 3, "channel": "phone",
 "notes": "Left voicemail at 2pm, will try again next week."}

// Response
{"outreach_id": 42,
 "next_touch": {"touch": 4, "channel": "mail", "target_date": "2026-05-22"}}
```

Validates: `channel` matches cadence_config for that touch_number; `touch_number` is next-due; partial-unique-index handles race. Notes go into `outreach.notes` (shared with the `gmail_message_id=...` line from regular sends — fine to coexist; if conflict ever matters we add a dedicated `manual_notes` column).

---

## UI changes

### Due Today banner (top of UI)

Currently empty/hidden. Becomes a live readout populated from `GET /api/outreach/due`:

```
┌──────────────────────────────────────────────────────────────┐
│ DUE TODAY  ·  3 emails  ·  1 phone call  ·  2 letters  · ✓1  │
└──────────────────────────────────────────────────────────────┘
```

Each chip is clickable → expands a dropdown listing the parcels in that channel group. Clicking a parcel selects it (existing parcel-select event) and opens the channel-appropriate compose/log UI.

Hidden when nothing is due. Order: email → phone → mail → end_of_sequence ("✓1 ready to retire").

### Sequence timeline in the detail panel

A new section between Stage and Contact, showing the 7-touch path for the selected parcel:

```
Sequence — Touch 2 of 7
✓ 1  Email · sent 2026-05-08
● 2  Email · due now
○ 3  Phone · due 2026-05-15
○ 4  Mail · due 2026-05-22
○ 5  Email · due 2026-05-27
○ 6  Mail · due 2026-06-01
○ 7  Email · due 2026-06-07
```

Visual style: completed = green check; current/overdue = orange dot; future = grey ring. Hover shows `target_date` + template name. Touches with `requires` not met show with a "(no email)" / "(no phone)" annotation in grey.

Hidden when parcel hasn't entered cadence (no touch 1 yet) — instead a "Start cadence" button appears, equivalent to clicking the existing Compose for touch 1.

### Channel-aware compose

The existing Compose modal handles email touches (1, 2, 5, 7). For phone (3) and mail (4, 6), a sibling modal appears:

- **Phone modal:** title "Phone call — touch 3 of 7"; body shows the rendered phone-script template (mail-merged with parcel data); a `notes` textarea for what was said; "Mark complete" button → POST log-manual-touch.
- **Mail modal:** title "Letter — touch 4 of 7" (or "Postcard — touch 6 of 7"); body shows the rendered letter/postcard template; a `notes` field; "Mark sent" button. Phase A: a "Copy to clipboard" affordance because the user prints and mails themselves. Phase B: a "Send via Lob" button when Lob is wired.

All three modals share the same outer styling (already built — the existing compose modal CSS).

### Pause / resume button

In the Stage section of the detail panel, alongside the existing stage dropdown:

```
Stage: [outreach ▾]   [⏸ Pause cadence]
```

When paused, the button reads "▶ Resume cadence" and the sequence timeline shows a "paused" overlay. Doesn't change stage.

### "Mark dead" surfacing

When `is_end_of_sequence: true`, the sequence timeline shows a green badge "Sequence complete" and a "Mark as dead" button next to the stage dropdown. Click → POST `/api/parcels/<pin>/stage` with `{"stage": "dead"}`. No auto-transition — explicit user action.

---

## Daily digest — `python -m pipeline.due_digest`

A CLI script using only standard library + the existing `pipeline.cadence` and `pipeline.gmail_client` modules.

```
USAGE: python -m pipeline.due_digest [--db PATH] [--config PATH] [--dry-run]
```

1. Loads the DB and cadence config.
2. Calls `all_due_touches(conn, config, date.today())`.
3. If groups are non-empty: composes a plain-text email summary, sends via Gmail.
4. If `--dry-run`: prints the summary to stdout instead of sending.
5. Exits 0 on success; non-zero on Gmail failure or DB error.

### Email format

```
Subject: Chicago pipeline — 6 touches due today

DUE TODAY · 2026-05-15

Emails (3):
  • 123 W Main St — touch 2 (Day 3 follow-up), to: js@example.com
  • 456 N Halsted — touch 5 (Day 19 new angle), to: maria@example.com
  • 789 W Belmont — touch 7 (Day 30 close), to: dan@example.com [+1 overdue]

Phone (1):
  • 1010 N Lincoln — touch 3 (Day 7 call), (312) 555-0145

Mail (2):
  • 234 W Diversey — touch 4 (Day 14 letter)
  • 567 N Sheffield — touch 6 (Day 24 postcard)

End of sequence (1):
  • 890 W Wellington — sent touch 7 on 2026-04-14, no reply. Mark as dead?

Reminder: scan your inbox for replies from parcels in active outreach
before approving the next touch. Active outreach parcels: 12.

Open the app: http://localhost:5051/  (Phase A)
                                       (Phase B: https://...railway.app/)
```

### Local launchd schedule (Phase A)

A plist at `~/Library/LaunchAgents/com.chicagopipeline.duedigest.plist` running daily at 9am CT. The implementation plan ships a `scripts/install_due_digest_launchd.sh` that installs and `bootstraps` the plist. User runs the script once; can `launchctl unload` to disable.

Missed runs (Mac off at 9am) don't fire on wake — accepted per the Phase A philosophy. Phase B's Railway cron is the always-on solution.

---

## Sequence rules — summary table

| Rule | Mechanism |
|---|---|
| Response stops cadence | `mark-replied` transitions to `responded` → cadence engine ignores parcel |
| End of sequence → suggest dead | `is_end_of_sequence` true → digest + UI "Mark dead?" button. No auto-transition. |
| Missing contact info skips touch | `requires` field check in `next_due_touches_for_parcel` |
| Email bounce | Not detected in v1. User manually flags ("This email bounced") → contact.email cleared → future email touches silently skipped per the missing-info rule. |
| Email required to start cadence | Anchor at touch_1.sent_date; no email → no touch 1 → no cadence |
| Pause | `parcels.outreach_paused = 1` short-circuits the engine for that parcel |
| Race-safe touches | Partial unique index on `outreach(pin, touch_number)` → second insert returns 409 |
| Out-of-order touches blocked | Server-side validation in `/api/outreach/send` and `/log-manual-touch` |
| Touch templates | One template per touch, in `config/outreach_templates.yaml`, editable via existing Save template UI |
| YAML edits | Mid-flight edits shift in-progress parcels. Documented; cadence_version snapshotting deferred. |

---

## Tests

### Cadence engine unit tests (`tests/test_pipeline_cadence.py`)

```
test_load_cadence_config_parses_yaml
test_no_touch_1_means_no_due_touches
test_touch_2_due_3_days_after_touch_1
test_touch_3_skipped_when_no_phone
test_touch_4_mail_surfaces_even_without_email_for_parcel_in_cadence
test_completed_touch_doesnt_resurface
test_paused_parcel_returns_empty_due_list
test_responded_parcel_returns_empty_due_list
test_end_of_sequence_after_30_days_no_response
test_overdue_touch_includes_days_overdue_count
test_target_dates_anchored_to_touch_1_not_shifted_by_late_touches
test_invalid_yaml_raises
test_missing_template_field_raises
```

### Route tests (`tests/test_webapp_cadence_routes.py`)

```
test_get_due_returns_empty_when_no_parcels_in_outreach
test_get_due_groups_by_channel
test_get_due_includes_end_of_sequence_suggestion
test_get_due_404s_when_flag_off
test_log_manual_touch_records_phone_touch
test_log_manual_touch_rejects_wrong_channel_for_touch
test_log_manual_touch_rejects_out_of_order_touch
test_log_manual_touch_409s_on_duplicate
test_pause_parcel_sets_flag
test_pause_parcel_hides_from_due
test_send_with_touch_number_advances_cadence
test_send_rejects_out_of_order_touch_number
test_get_cadence_config_returns_yaml
test_parcel_outreach_response_includes_sequence_block
```

### Digest CLI test (`tests/test_pipeline_due_digest.py`)

```
test_dry_run_prints_to_stdout_no_send
test_send_called_when_due_non_empty
test_no_send_when_due_empty
test_subject_includes_count
```

### Race / index test

```
test_outreach_unique_index_prevents_duplicate_touch
```

Target: ~30 new tests, full suite ~305+ passing after the plan ships.

---

## Out of scope (deferred)

| Feature | Reason | When |
|---|---|---|
| Lob integration | Hunter wants email signal first | Phase 2 of cadence work, post-validation |
| IMAP reply detection | Hunter's inbox handles it; digest reminder is enough | If "forgot to mark replied" becomes a real issue |
| Contact enrichment (REISkip, IL SOS, Zillow) | Separate subsystem; cadence value is independent | Separate spec + plan after cadence proves out |
| Wave-level metrics / Feedback Report | `wave_id` stays NULL; no batch grouping needed at 10-20/wk | Indefinitely deferred |
| Multi-contact per parcel | One contact per pin still sufficient | If a real "registered agent + manager" workflow surfaces |
| `cadence_version` snapshotting | Mid-flight YAML edits are user-initiated and rare | Defer; document the gotcha |
| Bounce auto-detection | Manual flag covers it | If bounce rate becomes a real problem |
| Multi-user auth on Railway | Phase B | After Phase A validation |

---

## Risks

1. **YAML edits mid-cadence shift in-progress parcels** — documented; `cadence_version` deferred.
2. **Race condition on simultaneous sends** — mitigated by partial unique index.
3. **Forgotten "mark replied" causes follow-up to engaged owners** — mitigated by daily digest reminder. Phase B can add IMAP polling if this becomes real.
4. **Mac off at 9am misses the digest** — accepted in Phase A; Phase B's Railway cron is always-on.
5. **The 6 new templates ship as drafts** — content is humanizer-audited but unproven. Hunter will iterate via Save template.
6. **Single SQLite write-lock with concurrent route handlers** — at 10-20/wk this is non-issue; SQLite handles serial writes fine. Worth knowing if volume grows.

---

## Security review todo

Captured in the session todo list. Audit Railway deploy (when Phase B happens) for:
- Multi-user auth correctness (rate limit, password hashing, env-var format)
- Gmail token at rest on Railway volume (perms, snapshot exposure, rotation runbook)
- Outreach data exposure on Railway
- Route guards (return 404 not 403, every route gated)
- CSRF on write routes (probably still not needed; confirm given the multi-user shift)
- OAuth state CSRF (the `_state` we throw away today)
- Cron failure monitoring

Not a blocker for Phase A; mandatory before Phase B ships.
