"""Auth endpoints: signup (invite-code gated), login, logout, and /me.

Session is a Redis-backed opaque token in an httponly cookie — see
app.core.auth. `SameSite=Lax` is sufficient here without a separate CSRF
token: these are JSON POSTs with a non-simple Content-Type, so a cross-origin
request needs a CORS preflight first, and CORSMiddleware (app/main.py) only
allows the explicit origins in CORS_ORIGINS — never a wildcard alongside
allow_credentials=True. If that ever changes to allow a broader origin set,
this reasoning needs revisiting.
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user_optional, enforce_auth_rate_limit
from app.core.auth import (
    hash_password,
    verify_password_timing_safe,
    create_session,
    delete_session,
)
from app.core.config import settings
from app.database.repositories import users as user_repo
from app.schemas.auth import SignupRequest, LoginRequest, UserResponse

router = APIRouter()


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        path="/",
    )


@router.post("/signup", response_model=UserResponse, status_code=201)
async def signup(
    payload: SignupRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(enforce_auth_rate_limit),
):
    if not settings.signup_invite_code:
        raise HTTPException(status_code=403, detail="Signups are currently closed")
    # Constant-time comparison — the invite code is a single shared secret
    # compared on every attempt, so a naive == would leak how many leading
    # characters matched via timing.
    if not secrets.compare_digest(payload.invite_code, settings.signup_invite_code):
        raise HTTPException(status_code=403, detail="Invalid invite code")

    existing = await user_repo.get_user_by_email(db, payload.email)
    if existing:
        # Invite-gated (not open to the internet), so a specific message here
        # is an acceptable, low-risk UX tradeoff over a generic one.
        raise HTTPException(status_code=409, detail="This email is already registered — try logging in")

    user = await user_repo.create_user(
        db,
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    await db.commit()

    # Always mint a fresh token — never reuse one from an incoming cookie
    # (session fixation).
    token = await create_session(user["id"])
    _set_session_cookie(response, token)
    return UserResponse(**user)


@router.post("/login", response_model=UserResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(enforce_auth_rate_limit),
):
    user = await user_repo.get_user_by_email(db, payload.email)
    # Verify against the real hash when the user exists, or a dummy hash when
    # they don't — always doing a real Argon2 verify either way, so "no such
    # account" and "wrong password" take similar time and neither discloses
    # whether the email is registered.
    ok = verify_password_timing_safe(payload.password, user["password_hash"] if user else None)
    if not user or not ok:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = await create_session(user["id"])
    _set_session_cookie(response, token)
    return UserResponse(**user)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    """Idempotent: logging out with no session, or an already-expired one,
    still succeeds — there is nothing meaningfully different about that case
    from the caller's point of view."""
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        await delete_session(token)
    response.delete_cookie(key=settings.session_cookie_name, path="/")


@router.get("/me")
async def me(user: dict | None = Depends(get_current_user_optional)):
    """Whether anyone is logged in, and who — the frontend calls this once on
    load to decide whether to show the app or the login screen."""
    return {"user": UserResponse(**user) if user else None}
