"""Password hashing and session management.

Sessions are Redis-backed opaque tokens rather than signed cookies: Redis is
already a hard dependency (Celery), so this is zero new infra, and it buys
real server-side revocation (delete the key) — a signed-cookie-only session
can't be revoked without rotating a secret that logs out every user at once.
"""

import json
import secrets
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHash

from app.core.config import settings
from app.core.redis import get_redis

_hasher = PasswordHasher()

# Precomputed once at import time so a login attempt for a nonexistent email
# can still run a real Argon2 verify against *something* — otherwise "no such
# account" returns near-instantly while "wrong password" takes tens of
# milliseconds, and that timing gap itself discloses which emails are
# registered.
_DUMMY_HASH = _hasher.hash("no-such-account-timing-safety-placeholder")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
        return True
    except (VerifyMismatchError, InvalidHash):
        return False


def verify_password_timing_safe(password: str, password_hash: Optional[str]) -> bool:
    """Verify against `password_hash`, or a dummy hash when None (unknown
    email) — always doing a real Argon2 verify either way, so response time
    doesn't leak whether the email exists."""
    return verify_password(password, password_hash or _DUMMY_HASH)


# ── Sessions ─────────────────────────────────────────────────────────────────

_SESSION_KEY_PREFIX = "session:"


async def create_session(user_id: UUID) -> str:
    """Mint a brand-new session token. Always a fresh token — never reuse one
    from an incoming request, which would be session fixation."""
    token = secrets.token_urlsafe(32)
    payload = json.dumps({
        "user_id": str(user_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    r = get_redis()
    await r.set(_SESSION_KEY_PREFIX + token, payload, ex=settings.session_ttl_seconds)
    return token


async def get_session_user_id(token: str) -> Optional[UUID]:
    """Look up the session, refreshing its TTL (sliding expiration) so an
    active user is never logged out mid-session. Returns None for a missing
    or expired session."""
    r = get_redis()
    key = _SESSION_KEY_PREFIX + token
    raw = await r.get(key)
    if raw is None:
        return None
    await r.expire(key, settings.session_ttl_seconds)
    try:
        data = json.loads(raw)
        return UUID(data["user_id"])
    except (ValueError, KeyError, TypeError):
        return None


async def delete_session(token: str) -> None:
    r = get_redis()
    await r.delete(_SESSION_KEY_PREFIX + token)
