"""Stateless authentication for the hosted MCP server.

Two secrets come from the environment:

``ADMIN_SECRET_KEY``
    Plain string that the admin sends as ``admin_secret_key`` when issuing a
    token. Compared literally (constant-time) with :func:`hmac.compare_digest`.
``TOKEN_SIGNING_SECRET``
    Key used to sign the issued JWT. Rotating it revokes every token at once.

No database is involved: the email and expiry live inside the signed token and
are verified on every request.
"""

from __future__ import annotations

import hmac
import re
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import jwt

from .settings import get_web_settings

BUCHAREST = ZoneInfo("Europe/Bucharest")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALGORITHM = "HS256"


def check_admin_secret(provided: str) -> bool:
    """Raw, constant-time comparison of the supplied secret with settings."""
    expected = get_web_settings().admin_secret_key.get_secret_value()
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def parse_expiry(raw: str) -> datetime:
    """Parse an ISO 8601 expiry string into a timezone-aware datetime.

    Naive values are interpreted in Europe/Bucharest (Romania local time);
    values with an explicit offset are kept as-is. Past dates are rejected.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("token_expire_date is required (ISO 8601).")
    text = raw.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError as ex:
        raise ValueError(
            "token_expire_date must be ISO 8601, e.g. 2026-12-31T23:59:59+02:00."
        ) from ex
    if value.tzinfo is None:
        value = value.replace(tzinfo=BUCHAREST)
    now = datetime.now(timezone.utc)
    if value <= now:
        raise ValueError("token_expire_date must be in the future.")
    return value


def issue_token(email: str, expires_at: datetime) -> str:
    """Create a signed token carrying the email and expiry (no server state)."""
    if not EMAIL_RE.match(email or ""):
        raise ValueError("user_email is not a valid email address.")
    secret = get_web_settings().token_signing_secret.get_secret_value()
    now = datetime.now(timezone.utc)
    claims = {
        "sub": email,
        "exp": int(expires_at.timestamp()),
        "iat": int(now.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def verify_token(token: str) -> dict:
    """Verify signature and expiry; return the claims or raise ValueError."""
    secret = get_web_settings().token_signing_secret.get_secret_value()
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub", "jti"]},
        )
    except jwt.PyJWTError as ex:
        raise ValueError("Invalid or expired token.") from ex
    if not isinstance(claims.get("sub"), str) or not EMAIL_RE.match(claims["sub"]):
        raise ValueError("Invalid token subject.")
    return claims


def expiry_from_token(token: str) -> datetime:
    """Return the expiry of a verified token, in UTC."""
    claims = verify_token(token)
    return datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)


def default_expiry(days: int = 30) -> datetime:
    """Convenience for admin tooling/tests."""
    return datetime.now(timezone.utc) + timedelta(days=days)

