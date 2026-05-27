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
    result = build_digest(db_path, cadence_path, date(2026, 5, 9), app_url="http://x")
    assert result is None


def test_build_digest_returns_text_when_due_non_empty(db_path, cadence_path):
    result = build_digest(db_path, cadence_path, date(2026, 5, 11),
                          app_url="http://localhost:5051/")
    assert result is not None
    body, count = result
    assert "DUE TODAY" in body
    assert "123 W Main" in body
    assert "jane@example.com" in body
    assert "http://localhost:5051/" in body
    # One touch is due (touch 2 for jane@example.com on 2026-05-11)
    assert count == 1


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


def test_send_digest_failure_does_not_write_sentinel(db_path, cadence_path, tmp_path):
    """If Gmail send fails on a send-day, the sentinel must NOT be written —
    otherwise /api/health/digest would mask the failure as a successful run."""
    sentinel = tmp_path / "last_run.txt"
    with patch("pipeline.due_digest.gmail_client.send_email") as send_mock:
        send_mock.side_effect = RuntimeError("Gmail unavailable")
        with pytest.raises(RuntimeError, match="Gmail unavailable"):
            main(["--db", str(db_path), "--config", str(cadence_path),
                  "--today", "2026-05-11",   # touch 2 is due
                  "--sender", "me@example.com",
                  "--token-path", str(tmp_path / "token.json"),
                  "--last-run-path", str(sentinel)])
    assert send_mock.call_count == 1
    assert not sentinel.exists()
