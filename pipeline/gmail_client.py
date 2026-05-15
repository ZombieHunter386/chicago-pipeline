"""Gmail OAuth flow + send helpers.

Single-user, web-application OAuth flow with redirect URI
http://localhost:5051/api/oauth/callback. Refresh-token persisted to a JSON file
(default data/gmail_token.json, gitignored).

Public surface:
  build_authorization_url(client_secrets_path, redirect_uri) -> (url, state)
  exchange_code_for_token(...) -> None      # writes token file
  load_credentials(token_path) -> Credentials
  save_token(token_path, info)              # write token JSON with 0o600
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
