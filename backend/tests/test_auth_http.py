"""HTTP-level test of the auth cookie lifecycle: signup -> login -> cookie
carries a session -> logout invalidates it.

This is the one test file in the suite that goes through the actual HTTP
layer (httpx.AsyncClient over ASGITransport) rather than calling repository
functions directly. That's a deliberate, narrow exception to this suite's
usual convention (see tests/README.md) — cookie-based auth (Set-Cookie on
login, the client's cookie jar carrying it forward, 401 after it's cleared)
is a wire-protocol behavior a bare AsyncSession-based test cannot exercise at
all, since there's no HTTP layer, no cookie, no middleware in that path.
Everything else stays at the repository level.
"""

import pytest
import httpx

from app.main import app
from app.core.config import settings


@pytest.fixture(autouse=True)
async def _fresh_redis_client():
    """app.core.redis caches one client for the process lifetime, which is
    correct for a real server (one event loop for the whole process) but not
    for pytest-asyncio's default function-scoped event loops — a client
    created in one test's loop errors with "Event loop is closed" when reused
    from the next test's loop. Reset the module-level singleton around each
    test so it's always created fresh, in the loop actually running it."""
    import app.core.redis as redis_module
    redis_module._client = None
    r = redis_module.get_redis()
    await r.flushdb()  # rate-limit counters and any stray sessions from a prior test
    yield
    await r.flushdb()
    await r.aclose()
    redis_module._client = None


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_signup_is_open_no_invite_code_needed(client):
    resp = await client.post("/api/v1/auth/signup", json={
        "email": "alice@example.com", "password": "correct horse battery",
    })
    assert resp.status_code == 201
    assert resp.json()["email"] == "alice@example.com"
    assert settings.session_cookie_name in resp.cookies


@pytest.mark.asyncio
async def test_signup_duplicate_email_rejected_with_generic_message(client):
    first = await client.post("/api/v1/auth/signup", json={
        "email": "dupe@example.com", "password": "correct horse battery",
    })
    assert first.status_code == 201

    second = await client.post("/api/v1/auth/signup", json={
        "email": "dupe@example.com", "password": "another password entirely",
    })
    assert second.status_code == 409
    # Wording must not confirm the email specifically exists — that's a user
    # enumeration leak now that signup is open to the public.
    assert "already registered" not in second.json()["detail"].lower()


@pytest.mark.asyncio
async def test_signup_login_me_logout_cycle(client):
    signup = await client.post("/api/v1/auth/signup", json={
        "email": "bob@example.com", "password": "correct horse battery",
    })
    assert signup.status_code == 201

    # /me with the signup cookie already in the client's jar
    me = await client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "bob@example.com"

    # Logout clears the session
    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 204

    me_after = await client.get("/api/v1/auth/me")
    assert me_after.status_code == 200
    assert me_after.json()["user"] is None

    # Login re-establishes a session
    login = await client.post("/api/v1/auth/login", json={
        "email": "bob@example.com", "password": "correct horse battery",
    })
    assert login.status_code == 200
    me_again = await client.get("/api/v1/auth/me")
    assert me_again.json()["user"]["email"] == "bob@example.com"


@pytest.mark.asyncio
async def test_login_wrong_password_rejected(client):
    await client.post("/api/v1/auth/signup", json={
        "email": "carol@example.com", "password": "correct horse battery",
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "carol@example.com", "password": "wrong password",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_email_rejected(client):
    resp = await client.post("/api/v1/auth/login", json={
        "email": "nobody@example.com", "password": "whatever",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_route_requires_session(client):
    resp = await client.get("/api/v1/papers")
    assert resp.status_code == 401
