"""Showing a picture, not only describing one.

Three routes reach an answer, and all three end as ordinary markdown that the
shared frontend pipeline renders:

  * a figure from the document — the block already existed and was citable,
    but nothing ever told the model its URL, so it could say "see Figure 3"
    and never show Figure 3;
  * a picture from the web, via the IMAGE tool;
  * a diagram the model draws itself in a ```mermaid block.

These cover the first two. The third is a prompt instruction plus a renderer,
and is pinned by test_study_prompt_contract.
"""

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.chat import paper_agent, study_agent
from app.chat.agent_tools import attach_asset_urls, format_block, run_image


# ── the document's own figures ─────────────────────────────────────────────

def test_a_block_with_a_picture_carries_the_markdown_to_show_it():
    block = {
        "sequence_id": 42,
        "chunk_type": "figure",
        "plain_text": "Figure 3: the encoder-decoder stack",
        "image_url": "/api/v1/papers/doc/assets/doc/fig3.png",
    }
    out = format_block(block)
    assert "show with: ![Figure 3: the encoder-decoder stack](/api/v1/papers/doc/assets/doc/fig3.png)" in out
    assert "[[42]] (figure)" in out


def test_a_prefixed_block_still_cites_by_paper():
    block = {"sequence_id": 7, "chunk_type": "figure", "plain_text": "F1",
             "image_url": "/api/v1/papers/doc/assets/d/a.png"}
    assert "[[P2:7]]" in format_block(block, prefix="P2:")


def test_a_text_block_gains_nothing():
    out = format_block({"sequence_id": 5, "chunk_type": "text", "plain_text": "prose"})
    assert "show with" not in out


@pytest.mark.asyncio
async def test_asset_urls_are_attached_from_the_database(db_session):
    user = await db_session.execute(
        text("INSERT INTO users (email, password_hash) VALUES (:e,'x') RETURNING id"),
        {"e": f"{uuid4()}@test.local"},
    )
    doc = await db_session.execute(
        text("""INSERT INTO documents (user_id, filename, original_filename, status)
                VALUES (:u,:f,:f,'complete') RETURNING id"""),
        {"u": user.scalar_one(), "f": "a.pdf"},
    )
    doc_id = doc.scalar_one()
    chunk = await db_session.execute(
        text("""INSERT INTO chunks (document_id, sequence_id, chunk_type, markdown, plain_text)
                VALUES (:d, 1, 'figure', 'x', 'Figure 1') RETURNING id"""),
        {"d": doc_id},
    )
    chunk_id = chunk.scalar_one()
    await db_session.execute(
        text("""INSERT INTO chunk_assets (chunk_id, asset_type, file_path, mime_type)
                VALUES (:c, 'image', :p, 'image/png')"""),
        {"c": chunk_id, "p": f"{doc_id}/fig1.png"},
    )
    await db_session.commit()

    blocks = [{"id": chunk_id, "sequence_id": 1, "chunk_type": "figure", "plain_text": "Figure 1"}]
    await attach_asset_urls(db_session, blocks)
    assert blocks[0]["image_url"] == f"/api/v1/papers/{doc_id}/assets/{doc_id}/fig1.png"
    assert "show with:" in format_block(blocks[0])


@pytest.mark.asyncio
async def test_blocks_with_no_assets_are_left_alone(db_session):
    blocks = [{"id": uuid4(), "sequence_id": 1, "chunk_type": "text", "plain_text": "prose"}]
    await attach_asset_urls(db_session, blocks)
    assert "image_url" not in blocks[0]


# ── the web ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_image_search_hands_back_pasteable_markdown():
    hits = [{"img_url": "https://ex.com/a.png", "title": "Transformer diagram",
             "source_url": "https://ex.com/post"}]
    with patch("app.chat.agent_tools.web_search.search_images", return_value=hits):
        obs, sources = await run_image("transformer architecture")
    assert "![Transformer diagram](https://ex.com/a.png)" in obs
    assert sources == [{"title": "Transformer diagram", "url": "https://ex.com/post"}]


@pytest.mark.asyncio
async def test_a_dead_image_provider_costs_the_picture_not_the_answer():
    """It must return an observation the model can read and move on from,
    never raise into the tool round."""
    with patch("app.chat.agent_tools.web_search.search_images", side_effect=RuntimeError("down")):
        obs, sources = await run_image("anything")
    assert "no pictures came back" in obs
    assert sources == []


@pytest.mark.asyncio
async def test_results_with_no_usable_url_are_dropped():
    with patch("app.chat.agent_tools.web_search.search_images",
               return_value=[{"title": "no url here"}]):
        obs, sources = await run_image("anything")
    assert "no pictures came back" in obs


# ── both agents understand the tool ────────────────────────────────────────

# The two agents spell these differently (the study agent's are public), so
# the shared behaviour is exercised through one small adapter rather than two
# near-identical copies of each test.
def _parse(mod, reply):
    fn = getattr(mod, "parse_tool_calls", None) or mod._parse_tool_calls
    return fn(reply)


def _has(mod, calls):
    fn = getattr(mod, "has_calls", None) or mod._has_calls
    return fn(calls)


def _plan(mod, calls):
    return mod._plan(calls, 3) if mod is study_agent else mod._plan(calls)


@pytest.mark.parametrize("mod", [paper_agent, study_agent])
def test_both_agents_parse_and_plan_an_image_call(mod):
    calls = _parse(mod, "<tool>\nIMAGE: ferroelectric hysteresis loop\n</tool>")
    assert calls["images"] == ["ferroelectric hysteresis loop"]
    assert _has(mod, calls)
    assert [c["tool"] for c in _plan(mod, calls)] == ["IMAGE"]


@pytest.mark.parametrize("mod", [paper_agent, study_agent])
def test_image_outside_a_tool_block_is_not_a_call(mod):
    """Prose mentioning the word must not trigger a round trip — the same
    rule every other tool follows."""
    calls = _parse(mod, "I could show you an IMAGE: of the layout, but won't.")
    assert calls["images"] == []
