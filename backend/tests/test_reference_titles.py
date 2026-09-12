"""services/references.py::title_candidates — the title guessed out of a raw
bibliography entry, because Semantic Scholar's match endpoint is a TITLE
matcher (see the note above the function): fed the whole entry it 404s.

Every shape here is a real entry from the corpus (Attention Is All You
Need's bibliography, as MinerU extracts it) or a standard style variant.
"""

import pytest

from app.services.references import title_candidates


@pytest.mark.parametrize("raw, title", [
    # arXiv / ACL style: Authors. Title. Venue, year.
    ("Jimmy Lei Ba, Jamie Ryan Kiros, and Geoffrey E Hinton. Layer normalization. arXiv preprint arXiv:1607.06450, 2016.",
     "Layer normalization"),
    # Initial with a period inside the author list must not split there.
    ("Denny Britz, Anna Goldie, Minh-Thang Luong, and Quoc V. Le. Massive exploration of neural machine translation architectures. CoRR, abs/1703.03906, 2017.",
     "Massive exploration of neural machine translation architectures"),
    # "Proc." abbreviation in the venue.
    ("Chris Dyer, Adhiguna Kuncoro, Miguel Ballesteros, and Noah A. Smith. Recurrent neural network grammars. In Proc. ofNAACL, 2016.",
     "Recurrent neural network grammars"),
    # Title runs straight into the year, no venue.
    ("Sepp Hochreiter, Yoshua Bengio, Paolo Frasconi, and Jürgen Schmidhuber. Gradient flow in recurrent nets: the difficulty of learning long-term dependencies, 2001.",
     "Gradient flow in recurrent nets: the difficulty of learning long-term dependencies"),
    # Title ends in a question mark.
    ("Łukasz Kaiser and Samy Bengio. Can active memory replace attention? In Advances in Neural Information Processing Systems, (NIPS), 2016.",
     "Can active memory replace attention?"),
    # Colon in the title.
    ("Diederik Kingma and Jimmy Ba. Adam: A method for stochastic optimization. In ICLR, 2015.",
     "Adam: A method for stochastic optimization"),
    # "et al." ends the author list and the title starts right after it.
    ("Yonghui Wu, Mike Schuster, Zhifeng Chen, Quoc V Le, et al. Google’s neural machine translation system: Bridging the gap between human and machine translation. arXiv preprint arXiv:1609.08144, 2016.",
     "Google’s neural machine translation system: Bridging the gap between human and machine translation"),
    # IEEE style: quoted title.
    ('A. Vaswani, N. Shazeer, and I. Polosukhin, "Attention is all you need," in Advances in Neural Information Processing Systems, 2017, pp. 5998–6008.',
     "Attention is all you need"),
    # APA style: year in parentheses after the authors.
    ("Vaswani, A., Shazeer, N., Parmar, N., et al. (2017). Attention is all you need. Advances in Neural Information Processing Systems, 30.",
     "Attention is all you need"),
])
def test_title_is_the_first_candidate(raw, title):
    assert title_candidates(raw)[0] == title


def test_venue_and_page_segments_are_never_candidates():
    raw = "Sepp Hochreiter and Jürgen Schmidhuber. Long short-term memory. Neural computation, 9(8):1735–1780, 1997."
    assert title_candidates(raw) == ["Long short-term memory"]
    raw = "Kaiming He, Xiangyu Zhang, Shaoqing Ren, and Jian Sun. Deep residual learning for image recognition. In Proceedings of the IEEE Conference on Computer Vision and Pattern Recognition, pages 770–778, 2016."
    assert title_candidates(raw) == ["Deep residual learning for image recognition"]


def test_a_bare_title_is_its_own_candidate():
    assert title_candidates("Attention is all you need") == ["Attention is all you need"]


def test_a_quoted_title_is_the_only_candidate():
    raw = 'J. Smith, "A study of things, revisited," in Proc. Things, 2020.'
    assert title_candidates(raw) == ["A study of things, revisited"]


def test_empty_and_limit():
    assert title_candidates("") == []
    assert title_candidates("   ") == []
    many = "A Author. First segment here. Second segment here. Third segment here. 2020."
    assert len(title_candidates(many, limit=2)) == 2


# ── What a Semantic Scholar hit turns into ──────────────────────────────────
# Verified 2026-09-12 on the live box: every arXiv paper the resolver matched
# came back with `openAccessPdf: null`, so nothing was addable. The arXiv id
# is the PDF.

def test_arxiv_hit_without_open_access_pdf_still_yields_a_pdf_url():
    from app.search.semantic_scholar_client import _to_match
    m = _to_match({
        "paperId": "abc123",
        "title": "MoCa: Modality-aware Continual Pre-training",
        "authors": [{"name": "Haonan Chen"}, {"name": "Hong Liu"}],
        "year": 2025,
        "externalIds": {"ArXiv": "2506.23115", "DOI": "10.48550/arXiv.2506.23115"},
        "openAccessPdf": None,
    })
    assert m.pdf_url == "https://arxiv.org/pdf/2506.23115"
    assert m.arxiv_id == "2506.23115"
    assert m.s2_paper_id == "abc123"
    assert m.authors == "Haonan Chen, Hong Liu"


def test_semantic_scholars_own_pdf_link_wins_over_arxiv():
    from app.search.semantic_scholar_client import _to_match
    m = _to_match({
        "title": "x", "externalIds": {"ArXiv": "1.2"},
        "openAccessPdf": {"url": "https://aclanthology.org/x.pdf"},
    })
    assert m.pdf_url == "https://aclanthology.org/x.pdf"


def test_no_identifier_means_no_pdf():
    from app.search.semantic_scholar_client import _to_match
    m = _to_match({"title": "x", "externalIds": {"DOI": "10.1/x"}, "openAccessPdf": None})
    assert m.pdf_url is None and m.s2_paper_id is None


def test_entry_search_query_is_a_title_not_the_whole_citation():
    from app.api.v1.endpoints.chunks import _entry_out
    raw = ("Haonan Chen, Hong Liu, Yuping Luo, Liang Wang, Nan Yang, Furu Wei, and Zhicheng Dou. "
           "MoCa: Modality-aware continual pre-training makes better bidirectional multimodal embeddings. "
           "arXiv preprint arXiv:2506.23115, 2025.")
    base = {"ref_number": 21, "raw_text": raw, "added_document_id": None}
    unresolved = _entry_out({**base, "resolve_status": "no_match", "external_ids": None})
    assert unresolved.search_query.startswith("MoCa: Modality-aware")
    assert "Haonan Chen" not in unresolved.search_query
    assert unresolved.s2_url is None and unresolved.arxiv_url is None

    resolved = _entry_out({
        **base, "resolve_status": "resolved", "resolved_title": "MoCa: Modality-aware Continual Pre-training",
        "external_ids": '{"arxiv": "2506.23115", "s2": "abc123"}',
    })
    assert resolved.search_query == "MoCa: Modality-aware Continual Pre-training"
    assert resolved.s2_url == "https://www.semanticscholar.org/paper/abc123"
    assert resolved.arxiv_url == "https://arxiv.org/abs/2506.23115"
