"""Gmail bounce poller.

Scans recent mailer-daemon messages, extracts failed recipients via
Final-Recipient (RFC 3464) headers — with an angle-bracket fallback for
DSNs that omit the structured header — and marks matching contacts rows
dead. Idempotent: uses bounce_poll_state.last_message_id to process only
messages newer than the last successful poll.

Runs on a 60-min launchd cycle plus best-effort after each Gmail send,
so timestamps use the same ISO-8601-with-Z format as the rest of the
project (see pipeline/db.py default columns).
"""
from __future__ import annotations
import re
import sqlite3
from pathlib import Path

# Final-Recipient per RFC 3464:
#   Final-Recipient: rfc822; user@example.com
# MULTILINE so each line in the multipart delivery-status section is
# matched independently; IGNORECASE because some servers send
# "final-recipient" or "FINAL-RECIPIENT".
FINAL_RECIPIENT_RE = re.compile(
    r"^[ \t]*Final-Recipient:\s*rfc822;\s*([^\s<>]+)",
    re.IGNORECASE | re.MULTILINE,
)
# Fallback: address in angle brackets in the human-readable body, e.g.
#   "The following message to <user@example.com> was undeliverable."
# Only used when the structured Final-Recipient header is missing —
# otherwise we might pick up an in-body quoted address that isn't the
# one that actually bounced.
ANGLE_ADDR_RE = re.compile(
    r"<([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>",
)


def extract_failed_recipients(body: str) -> list[str]:
    """Return deduped, lowercased addresses parsed from a bounceback message.

    Prefers the structured Final-Recipient header; falls back to angle-
    bracketed addresses in the human-readable section when the header is
    absent (some DSN producers ship only a textual notification). The
    googlemail.com / google.com domains are filtered from the fallback
    so the mailer-daemon's own From: address doesn't get marked dead.
    """
    seen: list[str] = []
    for addr in FINAL_RECIPIENT_RE.findall(body or ""):
        a = addr.strip().lower()
        if a and a not in seen:
            seen.append(a)
    if not seen:
        # Fallback heuristic — only when no Final-Recipient was found.
        for addr in ANGLE_ADDR_RE.findall(body or ""):
            a = addr.strip().lower()
            if (
                a
                and a not in seen
                and not a.endswith("@googlemail.com")
                and not a.endswith("@google.com")
            ):
                seen.append(a)
    return seen


def mark_addresses_dead(
    conn: sqlite3.Connection,
    addresses: list[str],
    reason: str = "bounce",
) -> int:
    """Flip dead=1 for any contacts row whose email matches an address.

    Case-insensitive match on email. Only updates rows where dead=0 so
    re-polling the same message doesn't re-stamp dead_at. Returns the
    count of rows actually flipped this call.
    """
    if not addresses:
        return 0
    placeholders = ",".join("?" for _ in addresses)
    cur = conn.execute(
        f"UPDATE contacts SET dead=1, "
        f"dead_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), dead_reason=? "
        f"WHERE LOWER(email) IN ({placeholders}) AND dead=0",
        (reason, *addresses),
    )
    conn.commit()
    return cur.rowcount


def poll_once(
    *,
    db_path: Path,
    gmail_token_path: Path,
    fetch_messages_fn=None,  # injectable for testing
) -> dict:
    """Fetch new mailer-daemon messages, parse, mark dead.

    fetch_messages_fn lets the test suite substitute the real Gmail API
    call with a stub that returns canned (msg_id, body) tuples — we
    never want a unit test to hit the network or load real OAuth
    credentials.

    Returns {'messages_processed': N, 'addresses_flipped': M}.
    """
    from pipeline import gmail_client
    fetch = fetch_messages_fn or gmail_client.fetch_mailer_daemon_messages

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        state = conn.execute(
            "SELECT last_message_id FROM bounce_poll_state WHERE id=1"
        ).fetchone()
        last_id = state["last_message_id"] if state else None

        messages = fetch(token_path=gmail_token_path, since_message_id=last_id)
        total_flipped = 0
        highest_id = last_id
        for msg_id, body in messages:
            addrs = extract_failed_recipients(body)
            total_flipped += mark_addresses_dead(conn, addrs)
            # Gmail API list returns newest-first; the first id we see is
            # therefore the highest. We capture it on the first iteration
            # so subsequent polls can skip everything already processed.
            if highest_id == last_id:
                highest_id = msg_id

        if highest_id and highest_id != last_id:
            conn.execute(
                "UPDATE bounce_poll_state SET last_message_id=?, "
                "last_polled_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                "WHERE id=1",
                (highest_id,),
            )
            conn.commit()
    return {
        "messages_processed": len(messages),
        "addresses_flipped": total_flipped,
    }
