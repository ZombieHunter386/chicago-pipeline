"""HTTP basic auth — single username/password from env vars.

Auth activates when both WEBAPP_USER and WEBAPP_PASSWORD are set in the
environment. If either is missing, no auth is enforced (so local dev with
no env vars stays unauthenticated). On the deployed instance both are set
in the host's secret store (Render dashboard, never committed).

Why basic auth: the user is sharing the deployed URL with a single trusted
friend. Basic auth gives a one-time browser prompt that the browser then
remembers, no login page or session machinery required. Adequate for
sharing a non-public dataset with one person; not adequate for any larger
audience or sensitive PII handling.
"""
from __future__ import annotations
import os

from flask import Flask, Response, request


_REALM = "Chicago Pipeline"


def _credentials_configured() -> bool:
    return bool(os.environ.get("WEBAPP_USER")) and bool(
        os.environ.get("WEBAPP_PASSWORD")
    )


def _check(username: str | None, password: str | None) -> bool:
    if username is None or password is None:
        return False
    expected_user = os.environ.get("WEBAPP_USER", "")
    expected_pass = os.environ.get("WEBAPP_PASSWORD", "")
    return username == expected_user and password == expected_pass


def _challenge() -> Response:
    return Response(
        "Authentication required.",
        401,
        {"WWW-Authenticate": f'Basic realm="{_REALM}"'},
    )


def _before_request_auth():
    auth = request.authorization
    if auth is None or not _check(auth.username, auth.password):
        return _challenge()
    return None


def init_auth(app: Flask) -> None:
    """Wire basic auth onto the app if credentials are configured.

    No-op when WEBAPP_USER / WEBAPP_PASSWORD aren't set (e.g. local dev).
    """
    if not _credentials_configured():
        return
    app.before_request(_before_request_auth)
