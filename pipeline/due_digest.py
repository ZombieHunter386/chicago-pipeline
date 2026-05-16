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
from datetime import date, datetime, timezone
from pathlib import Path

from pipeline import cadence as cadence_module
from pipeline import gmail_client


def build_digest(
    db_path: Path,
    cadence_path: Path,
    today: date,
    app_url: str,
) -> tuple[str, int] | None:
    """Compute the digest body for `today`. Returns `(body, touch_count)` or
    None when nothing is due (caller should skip sending)."""
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
    lines.append(f"DUE TODAY · {today.isoformat()}")
    lines.append("")

    channel_titles = {
        "email": "Emails",
        "phone": "Phone calls",
        "mail": "Mail",
        "end_of_sequence": "End of sequence",
    }
    touch_count = 0
    for g in groups:
        title = channel_titles.get(g["channel"], g["channel"])
        lines.append(f"{title} ({g['count']}):")
        for it in g["items"]:
            touch_count += 1
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
    return "\n".join(lines), touch_count


def send_digest(
    body: str,
    *,
    sender: str,
    token_path: Path,
    count: int,
) -> dict:
    """Send the digest via Gmail. `to` is the same as `sender` (you mail
    yourself). `count` is the number of touches surfaced in the body, used
    for the subject line."""
    subject = f"Chicago pipeline — {count} touches due today"
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
    digest = build_digest(args.db, args.config, today, args.app_url)
    # Nothing due: this is a successful run with no work to do. Update the
    # sentinel so empty days don't look like missed cron runs to the UI's
    # /api/health/digest check.
    if digest is None:
        if not args.dry_run:
            write_last_run_sentinel(args.last_run_path)
        if args.dry_run:
            print(f"# Nothing due on {today.isoformat()}; would not send.")
        return 0
    body, count = digest
    if args.dry_run:
        print(body)
        return 0
    if not args.sender:
        print("ERROR: --sender (or GMAIL_SENDER_ADDRESS env var) is required",
              file=sys.stderr)
        return 2
    # Send first, then write the sentinel. If Gmail raises, the sentinel
    # stays stale and /api/health/digest will flag the failure instead of
    # masking it as a successful run.
    send_digest(body, sender=args.sender, token_path=args.token_path, count=count)
    write_last_run_sentinel(args.last_run_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
