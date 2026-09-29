"""Parsing and keys for dash-list author–year bibliographies."""

import pytest

from app.services import references as references_service
from app.services.references import parse_references


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
        ("Smith and Jones. 2020a. A paper title.", ("Smith", 2020)),
        ("Fédérico, A. Colleague. 2023. Une étude importante.", ("Fédérico", 2023)),
        ("محمد الفارسي، علي حسن، وآخرون. 2022. بحث علمي.", ("الفارسي", 2022)),
    ],
)
def test_author_year_key_extractor_returns_first_surname_and_publication_year(raw, expected):
    extractor = getattr(references_service, "extract_author_year", None)
    assert callable(extractor), "author–year keys must be extracted by the references service"
    assert extractor(raw) == expected
