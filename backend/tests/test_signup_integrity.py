import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.v1.endpoints import auth
from app.schemas.auth import SignupRequest


class _Session:
    def __init__(self):
        self.rolled_back = False

    async def commit(self) -> None:
        raise AssertionError("commit is not reached after an insert failure")

    async def rollback(self) -> None:
        self.rolled_back = True


class _UniqueViolation:
    sqlstate = "23505"


async def test_signup_returns_the_documented_conflict_for_a_concurrent_insert(monkeypatch):
    session = _Session()

    async def _missing_user(*_args, **_kwargs):
        return None

    async def _duplicate_insert(*_args, **_kwargs):
        raise IntegrityError("INSERT", {}, _UniqueViolation())

    monkeypatch.setattr(auth.user_repo, "get_user_by_email", _missing_user)
    monkeypatch.setattr(auth.user_repo, "create_user", _duplicate_insert)
    monkeypatch.setattr(auth, "hash_password", lambda _password: "hash")

    with pytest.raises(HTTPException) as raised:
        await auth.signup(
            SignupRequest(email="person@example.com", password="long-enough-password"),
            response=object(),
            db=session,
        )

    assert raised.value.status_code == 409
    assert session.rolled_back is True
