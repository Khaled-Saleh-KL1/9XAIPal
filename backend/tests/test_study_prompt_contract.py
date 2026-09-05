"""The Desk prompt must describe the parser that actually reads it.

`parse_tool_calls` silently truncates each tool list, and the caps are not
uniform — SECTION/SEARCH/READ keep three, WEB/NOTE/NOTE ALL keep two,
REMEMBER keeps one. Those numbers are deliberate (see the comments beside
them: two notes so a model that decides note-writing is helpful cannot bury
the board in one answer; one REMEMBER because most rounds should remember
nothing).

The prompt used to tell the model "Up to three lines of each" — true for
half the tools and false for the rest. The model would emit a third NOTE, the
parser would drop it with no error and no observation, and the model would go
on believing it had pinned three. Nothing surfaces that: the truncation is a
slice.

The prompt text and the slices sit ~200 lines apart in study_agent.py, which
is how they drifted. This test holds them together.
"""

import re

import pytest

from app.chat.study_agent import _AGENT_SYSTEM, _REMEMBER_BULLET, _WEB_HELP, parse_tool_calls

# tool name -> how many the parser keeps
CAPS = {
    "sections": 3,
    "searches": 3,
    "reads": 3,
    "webs": 2,
    "notes": 2,
    "notes_all": 2,
    "remembers": 1,
}

# Four of every tool, so every cap has something to cut.
OVERFULL_REPLY = """Here is my plan.
<tool>
THINK: checking every paper
""" + "\n".join(
    f"SECTION: P1:{10 + i}\n"
    f"SEARCH: query number {i}\n"
    f"READ: P1:{100 + i}-{110 + i}\n"
    f"WEB: web query {i}\n"
    f"NOTE: note number {i}\n"
    f"NOTE ALL: universal note {i}\n"
    f"REMEMBER: reader fact {i}"
    for i in range(4)
) + "\n</tool>"


@pytest.mark.parametrize("key,cap", sorted(CAPS.items()))
def test_the_parser_keeps_exactly_the_documented_number(key, cap):
    calls = parse_tool_calls(OVERFULL_REPLY)
    assert len(calls[key]) == cap, f"{key} cap moved; the prompt must move with it"


def test_the_prompt_states_the_real_limits_for_the_always_present_tools():
    """SECTION/SEARCH/READ and NOTE/NOTE ALL are in the prompt on every
    request, so their numbers have to be right in the base template."""
    body = _AGENT_SYSTEM
    assert "three SECTION, three SEARCH and three READ" in body
    assert "two NOTE and two NOTE ALL" in body
    # The old wording is the bug; it must not come back.
    assert "three lines of each" not in body


def test_the_conditional_bullets_carry_their_own_limits():
    """WEB is only in the prompt when web search is configured, and REMEMBER
    ships in its own bullet — so each has to state its own cap where it
    lives, or the number disappears whenever that bullet does."""
    assert "Two WEB lines per block" in _WEB_HELP
    assert "one line per block" in _REMEMBER_BULLET


def test_every_cap_the_parser_enforces_is_stated_somewhere_in_the_prompt():
    """The catch-all: a new tool with a silent cap and no prompt sentence is
    the exact shape of the bug this file exists for."""
    full_prompt = _AGENT_SYSTEM + _WEB_HELP + _REMEMBER_BULLET
    spelled = {1: "one", 2: "two", 3: "three"}
    for key, cap in CAPS.items():
        word = spelled[cap]
        assert re.search(word, full_prompt, re.IGNORECASE), (
            f"{key} is capped at {cap} but the prompt never says so"
        )
