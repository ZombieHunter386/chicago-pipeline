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

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build


# gmail.send: outreach worker writes new messages.
# gmail.readonly: bounce poller reads mailer-daemon bouncebacks (T9). The
# readonly scope is the narrowest one that supports messages.list/get with
# format=raw — modify/labels would be over-broad for a read-only sweep.
# Adding a scope requires re-running the OAuth consent flow (the existing
# refresh token still works but lacks readonly until the user reconnects).
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


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
        try:
            creds.refresh(Request())
        except RefreshError as e:
            # Refresh token expired or user revoked the app at
            # myaccount.google.com/permissions. Translate to the same error
            # the no-token case raises so the route's existing 503 branch
            # handles it — operator just needs to re-consent at
            # /api/oauth/start.
            raise GmailNotConnectedError(
                f"Gmail refresh token rejected ({e}). Re-consent at /api/oauth/start."
            ) from e
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
    bcc: list[str] | None = None,
) -> dict[str, str]:
    """Send a plain-text email via Gmail API. Returns {id, threadId}.

    bcc: optional list of recipient addresses. When provided, addresses are
    added to a Bcc: header so the visible To: stays set to `to` (typically
    the sender's own inbox for BCC-fanout outreach) while Gmail delivers to
    every Bcc address as well. Recipients on the Bcc list do not see each
    other or the full To/From conversation, matching standard BCC semantics.
    """
    creds = load_credentials(token_path)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg.set_content(body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    sent = (
        service.users().messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
    return {"id": sent.get("id", ""), "threadId": sent.get("threadId", "")}


def fetch_mailer_daemon_messages(
    *,
    token_path: Path,
    since_message_id: str | None = None,
    max_results: int = 50,
) -> list[tuple[str, str]]:
    """Return [(message_id, decoded_body), ...] for mailer-daemon bouncebacks.

    Gmail's messages.list returns newest-first; we walk that order and
    stop when we hit `since_message_id` (the cursor saved by the previous
    poll) so we don't re-process old bouncebacks. The 7-day q-filter is a
    belt-and-suspenders bound in case the cursor is lost or wrong — the
    poller still won't miss messages because subsequent polls overlap.

    Bodies are returned as the decoded raw RFC-822 source (str) so the
    parser can scan for Final-Recipient headers across the multipart
    structure without us having to walk it here.
    """
    creds = load_credentials(token_path)
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    listing = (
        service.users().messages()
        .list(
            userId="me",
            q="from:mailer-daemon newer_than:7d",
            maxResults=max_results,
        )
        .execute()
    )
    items = listing.get("messages", []) or []

    out: list[tuple[str, str]] = []
    for item in items:
        msg_id = item.get("id")
        if not msg_id:
            continue
        # Stop at the resume cursor — everything past this id has already
        # been processed by a prior poll.
        if since_message_id and msg_id == since_message_id:
            break
        msg = (
            service.users().messages()
            .get(userId="me", id=msg_id, format="raw")
            .execute()
        )
        raw_b64 = msg.get("raw", "")
        if not raw_b64:
            continue
        # Gmail returns urlsafe base64 without padding; `urlsafe_b64decode`
        # tolerates the missing padding when we pad it back to a multiple
        # of 4 ourselves. Decode as latin-1 so any non-UTF bytes (rare but
        # legal in MIME headers) survive — the parser is regex-based and
        # only needs ASCII-range bytes to match Final-Recipient.
        padded = raw_b64 + "=" * (-len(raw_b64) % 4)
        raw_bytes = base64.urlsafe_b64decode(padded)
        body = raw_bytes.decode("latin-1", errors="replace")
        out.append((msg_id, body))
    return out
