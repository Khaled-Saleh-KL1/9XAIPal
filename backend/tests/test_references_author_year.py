"""Parsing and keys for dash-list author–year bibliographies."""

import pytest
from uuid import uuid4

import httpx
from sqlalchemy import text

from app.services import references as references_service
from app.services.references import parse_references
from app.main import app


@pytest.fixture(autouse=True)
async def _fresh_redis():
    import app.core.redis as redis_module

    redis_module._client = None
    redis = redis_module.get_redis()
    await redis.flushdb()
    yield
    await redis.flushdb()
    await redis.aclose()
    redis_module._client = None


def _chunks(*texts):
    return [
        {"chunk_type": "heading", "plain_text": "References"},
        *({"chunk_type": "text", "plain_text": text} for text in texts),
        {"chunk_type": "heading", "plain_text": "Appendix"},
        {"chunk_type": "text", "plain_text": "not part of the bibliography"},
    ]


def test_author_year_items_keep_continuations_and_get_document_order_numbers():
    chunks = _chunks(
        "- Ahmed Abdelali, Hamdy Mubarak, …, and 1 others. 2024. LAraBench: Benchmarking Arabic AI.\n"
        "  Continuation on the next line.",
        "Still part of the same entry.\n"
        "- Jonas Gehring, Michael Auli, David Grangier, Denis Yarats, and Yann N Dauphin. "
        "Convolutional sequence to sequence learning. In ICML, 2017.",
    )

    assert parse_references(chunks) == [
        {
            "number": 1,
            "raw_text": (
                "Ahmed Abdelali, Hamdy Mubarak, …, and 1 others. 2024. LAraBench: Benchmarking Arabic AI.\n"
                "  Continuation on the next line.\nStill part of the same entry."
            ),
        },
        {
            "number": 2,
            "raw_text": (
                "Jonas Gehring, Michael Auli, David Grangier, Denis Yarats, and Yann N Dauphin. "
                "Convolutional sequence to sequence learning. In ICML, 2017."
            ),
        },
    ]


def test_real_numbered_bibliography_keeps_the_existing_output_exactly():
    raw = (
        "Vaswani, Ashish, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, "
        "Lukasz Kaiser, and Illia Polosukhin. Attention is all you need. In Advances in neural "
        "information processing systems, pages 5998–6008. 2017."
    )
    assert parse_references(_chunks(f"- [1] {raw}\n- [3] B. Author. Another real paper title. 2020.")) == [
        {"number": 1, "raw_text": raw},
        {"number": 3, "raw_text": "B. Author. Another real paper title. 2020."},
    ]


def test_parser_reports_author_year_style_without_changing_entry_shape():
    parser = getattr(references_service, "parse_references_with_style", None)
    assert callable(parser), "the parser must expose the detected citation style to the endpoint"
    assert parser(_chunks("- X and Y. 2020. A paper title.")) == (
        [{"number": 1, "raw_text": "X and Y. 2020. A paper title."}],
        "author_year",
    )


def test_parser_reports_numeric_style_for_numbered_entries():
    parser = getattr(references_service, "parse_references_with_style", None)
    assert callable(parser), "the parser must expose the detected citation style to the endpoint"
    assert parser(_chunks("- [7] X. A paper title. 2020.")) == (
        [{"number": 7, "raw_text": "X. A paper title. 2020."}],
        "numeric",
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            "Ahmed Abdelali, Hamdy Mubarak, …, and 1 others. 2024. LAraBench: Benchmarking Arabic AI. In ACL, 2024.",
            ("Abdelali", 2024),
        ),
        (
            "Jonas Gehring, Michael Auli, David Grangier, Denis Yarats, and Yann N Dauphin. "
            "Convolutional sequence to sequence learning. In ICML, pages 1243–1252. PMLR, 2017.",
            ("Gehring", 2017),
        ),
        (
            "T. Achim, A. Best, A. Bietti, …, et al. Aristotle: A benchmark of reasoning. 2025.",
            ("Achim", 2025),
        ),
        ("X and Y. 2020. A paper title.", ("X", 2020)),
        ("AA. Gdpval-aa leaderboard, 2025.", ("AA", 2025)),
        ("John Smith. Useful paper title. 2020.", ("Smith", 2020)),
        ("John A. Smith. Useful paper title. 2020.", ("Smith", 2020)),
        ("Smith and Jones. 2020a. A paper title.", ("Smith", 2020)),
        ("Fédérico, A. Colleague. 2023. Une étude importante.", ("Fédérico", 2023)),
        ("محمد الفارسي، علي حسن، وآخرون. 2022. بحث علمي.", ("الفارسي", 2022)),
    ],
)
def test_author_year_key_extractor_returns_first_surname_and_publication_year(raw, expected):
    extractor = getattr(references_service, "extract_author_year", None)
    assert callable(extractor), "author–year keys must be extracted by the references service"
    assert extractor(raw) == expected


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _create_paper(client, db_session):
    signup = await client.post(
        "/api/v1/auth/signup",
        json={"email": f"{uuid4()}@example.com", "password": "correct horse battery"},
    )
    assert signup.status_code == 201, signup.text
    document_id = (
        await db_session.execute(
            text(
                "INSERT INTO documents (user_id, filename, original_filename, status, doc_kind) "
                "VALUES (:user, :filename, :filename, 'complete', 'paper') RETURNING id"
            ),
            {"user": signup.json()["id"], "filename": f"{uuid4()}.pdf"},
        )
    ).scalar_one()
    await db_session.commit()
    return document_id


@pytest.mark.asyncio
async def test_author_year_endpoint_returns_keys_and_persists_style(client, db_session):
    document_id = await _create_paper(client, db_session)
    await db_session.execute(
        text(
            "INSERT INTO chunks (document_id, sequence_id, chunk_type, markdown, plain_text) "
            "VALUES (:doc, 1, 'heading', '## References', 'References'), "
            "(:doc, 2, 'text', :body, :body)"
        ),
        {"doc": document_id, "body": "- Ahmed Abdelali, Hamdy Mubarak. 2024. LAraBench title."},
    )
    await db_session.commit()

    response = await client.get(f"/api/v1/papers/{document_id}/references")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["citation_style"] == "author_year"
    assert payload["references"][0]["first_author"] == "Abdelali"
    assert payload["references"][0]["year"] == 2024
    row = (
        await db_session.execute(
            text("SELECT citation_style FROM paper_references WHERE document_id = :doc"),
            {"doc": document_id},
        )
    ).one()
    assert row.citation_style == "author_year"


@pytest.mark.asyncio
async def test_legacy_null_style_is_returned_as_numeric(client, db_session):
    document_id = await _create_paper(client, db_session)
    await db_session.execute(
        text(
            "INSERT INTO paper_references (document_id, ref_number, raw_text) "
            "VALUES (:doc, 3, 'A. Author. A paper title. 2020.')"
        ),
        {"doc": document_id},
    )
    await db_session.commit()

    response = await client.get(f"/api/v1/papers/{document_id}/references")

    assert response.status_code == 200, response.text
    assert response.json()["citation_style"] == "numeric"


@pytest.mark.asyncio
async def test_empty_parse_is_not_cached(client, db_session):
    document_id = await _create_paper(client, db_session)
    await db_session.execute(
        text(
            "INSERT INTO chunks (document_id, sequence_id, chunk_type, markdown, plain_text) "
            "VALUES (:doc, 1, 'heading', '## References', 'References')"
        ),
        {"doc": document_id},
    )
    await db_session.commit()

    response = await client.get(f"/api/v1/papers/{document_id}/references")

    assert response.status_code == 200, response.text
    assert response.json()["references"] == []
    assert response.json()["citation_style"] == "numeric"
    count = (
        await db_session.execute(
            text("SELECT count(*) FROM paper_references WHERE document_id = :doc"),
            {"doc": document_id},
        )
    ).scalar_one()
    assert count == 0
