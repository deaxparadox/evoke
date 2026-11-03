import pytest

from ragling.testing.pdf import make_text_pdf
from tests.golden import PDF_BYTES, TRUTH


def test_generator_reproduces_the_golden_file():
    pages = [["The quick brown fox jumps over the lazy dog.\nIt was a sunny day.", "Second paragraph on page one."], ["Page two has a single paragraph that is short."], []]
    assert make_text_pdf(pages) == PDF_BYTES  # guards against the generator drifting from the committed golden PDF


def test_special_characters_are_escaped():
    assert b"\\(a\\) \\\\ b" in make_text_pdf([["(a) \\ b"]])


def test_non_ascii_is_refused():
    with pytest.raises(ValueError):
        make_text_pdf([["café"]])


def test_truth_file_matches_page_count():
    assert len(TRUTH["pages"]) == 3
