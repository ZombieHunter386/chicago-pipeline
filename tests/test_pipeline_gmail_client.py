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
        creds_cls.from_authorized_user_info.return_value = MagicMock(expired=False)

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
