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
