"""Several conversations per Desk scope (2026-09-12): the "Chats · N" list the
book reader has, on the desk. Turns already carried a conversation_id; what
changes is that the scope lists them, reads one at a time, continues a named
one, starts a new one on request, and deletes one at a time — all
tenant-scoped like everything else on the desk."""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app.database.repositories import studies as study_repo
from app.main import app


@pytest.fixture(autouse=True)
async def _fresh_redis():
    import app.core.redis as redis_module
    redis_module._client = None
    r = redis_module.get_redis()
    await r.flushdb()
    yield
    await r.flushdb()
    await r.aclose()
    redis_module._client = None


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _signed_up(client) -> str:
    resp = await client.post("/api/v1/auth/signup", json={"email": f"{uuid4()}@example.com", "password": "correct horse battery"})
    assert resp.status_code == 201
    return resp.json()["id"]


async def _turns(db_session, user_id, study_id, cid, *pairs):
    for role, content in pairs:
        await study_repo.add_turn(db_session, user_id=user_id, study_id=study_id, conversation_id=cid, role=role, content=content)
    await db_session.commit()


@pytest.mark.asyncio
async def test_list_get_and_delete_one_conversation(client, db_session):
    user_id = await _signed_up(client)
    a, b = uuid4(), uuid4()
    await _turns(db_session, user_id, None, a, ("user", "First chat, first question"), ("assistant", "A1"))
    await _turns(db_session, user_id, None, b, ("user", "Second chat"), ("assistant", "B1"), ("user", "more"), ("assistant", "B2"))

    resp = await client.get("/api/v1/studies/library/conversations")
    assert resp.status_code == 200
    convs = resp.json()["conversations"]
    assert [c["conversation_id"] for c in convs] == [str(b), str(a)]          # most recent first
    assert convs[0]["turn_count"] == 4 and convs[0]["first_user_message"] == "Second chat"
    assert convs[1]["turn_count"] == 2 and convs[1]["first_user_message"] == "First chat, first question"

    # No id → the latest conversation, and the response says which.
    resp = await client.get("/api/v1/studies/library/chat")
    body = resp.json()
    assert body["conversation_id"] == str(b) and [t["content"] for t in body["turns"]] == ["Second chat", "B1", "more", "B2"]
    # A named one → that one only.
    resp = await client.get(f"/api/v1/studies/library/chat?conversation_id={a}")
    assert [t["content"] for t in resp.json()["turns"]] == ["First chat, first question", "A1"]

    # Delete one; the other survives.
    resp = await client.delete(f"/api/v1/studies/library/chat?conversation_id={b}")
    assert resp.status_code == 204
    convs = (await client.get("/api/v1/studies/library/conversations")).json()["conversations"]
    assert [c["conversation_id"] for c in convs] == [str(a)]
    # Delete all (the old "Clear chat").
    assert (await client.delete("/api/v1/studies/library/chat")).status_code == 204
    assert (await client.get("/api/v1/studies/library/conversations")).json()["conversations"] == []
    assert (await client.get("/api/v1/studies/library/chat")).json() == {"conversation_id": None, "turns": []}


@pytest.mark.asyncio
async def test_conversations_are_scoped_to_the_study_and_the_user(client, db_session):
    user_id = await _signed_up(client)
    study = (await client.post("/api/v1/studies", json={"name": "S"})).json()
    sid = study["id"]
    lib, st = uuid4(), uuid4()
    await _turns(db_session, user_id, None, lib, ("user", "library q"))
    await _turns(db_session, user_id, study["id"], st, ("user", "study q"))
    # Another user's conversation in *their* library scope.
    other = (await db_session.execute(text("INSERT INTO users (email, password_hash) VALUES (:e, 'x') RETURNING id"), {"e": f"{uuid4()}@example.com"})).scalar_one()
    await _turns(db_session, other, None, uuid4(), ("user", "not yours"))

    assert [c["first_user_message"] for c in (await client.get("/api/v1/studies/library/conversations")).json()["conversations"]] == ["library q"]
    assert [c["first_user_message"] for c in (await client.get(f"/api/v1/studies/{sid}/conversations")).json()["conversations"]] == ["study q"]
    # A conversation id from the other scope reads as empty here — not as a grafted history.
    assert (await client.get(f"/api/v1/studies/{sid}/chat?conversation_id={lib}")).json()["turns"] == []


@pytest.mark.asyncio
async def test_asking_continues_a_named_conversation_or_starts_a_new_one(client, db_session, monkeypatch):
    """The stream endpoint's routing of a question to a conversation, with the
    agent itself replaced by a stub that answers immediately."""
    from app.api.v1.endpoints import studies as ep

    async def fake_answer(session, **kw):
        fake_answer.histories.append([h["content"] for h in kw["history"]])
        yield {"type": "done", "answer": "ok", "model": "stub", "cited": [], "steps": []}
    fake_answer.histories = []
    monkeypatch.setattr(ep, "answer_study_question", fake_answer)
    monkeypatch.setattr(ep.grounding, "enabled", lambda: False)

    user_id = await _signed_up(client)
    existing = uuid4()
    await _turns(db_session, user_id, None, existing, ("user", "earlier"), ("assistant", "yes"))

    async def ask(payload):
        async with client.stream("POST", "/api/v1/studies/library/chat/stream", json=payload) as resp:
            assert resp.status_code == 200
            events = [line[6:] for line in (await resp.aread()).decode().splitlines() if line.startswith("data: ")]
        import json
        return [json.loads(e) for e in events]

    # No conversation named: continues the latest (the old behaviour).
    evs = await ask({"question": "q1"})
    assert evs[0]["type"] == "created" and evs[0]["conversation_id"] == str(existing)
    assert fake_answer.histories[-1] == ["earlier", "yes"]

    # new_conversation: a fresh id, empty history.
    evs = await ask({"question": "q2", "new_conversation": True})
    fresh = evs[0]["conversation_id"]
    assert fresh != str(existing) and fake_answer.histories[-1] == []

    # A named conversation is continued with its own history…
    evs = await ask({"question": "q3", "conversation_id": fresh})
    assert evs[0]["conversation_id"] == fresh and fake_answer.histories[-1] == ["q2", "ok"]
    # …and the scope now lists two.
    convs = (await client.get("/api/v1/studies/library/conversations")).json()["conversations"]
    assert {c["conversation_id"] for c in convs} == {str(existing), fresh}

    # A conversation id that is not this scope's: refused, nothing grafted.
    resp = await client.post("/api/v1/studies/library/chat/stream", json={"question": "q4", "conversation_id": str(uuid4())})
    assert resp.status_code == 404
