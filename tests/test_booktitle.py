"""Tests for the canonical book-slug used to unify the screenshot and
reading-log namespaces."""
import pytest

from xteink_service.booktitle import canonical_book_slug


@pytest.mark.parametrize("title, expected", [
    # Screenshot folder form and Calibre alias form collapse to one slug.
    ("The-Hidden-Keys", "the-hidden-keys"),
    ("Hidden Keys, The - Andre Alexis", "the-hidden-keys"),
    ("the hidden keys", "the-hidden-keys"),
    # Author stripping + hyphenation.
    ("Days-by-Moonlight", "days-by-moonlight"),
    ("Days by moonlight - Andre Alexis", "days-by-moonlight"),
    ("Ring", "ring"),
    ("Ring - Andre Alexis", "ring"),
    ("Reading-Dante", "reading-dante"),
    ("Reading Dante - Giuseppe Mazzotta", "reading-dante"),
    # Already-matched manual aliases still map to themselves.
    ("Fifteen-Dogs", "fifteen-dogs"),
    ("The-Bhagavad-Gita", "the-bhagavad-gita"),
    # Edge cases.
    ("", ""),
    (None, ""),
])
def test_canonical_book_slug(title, expected):
    assert canonical_book_slug(title) == expected


def test_screenshot_and_alias_forms_match():
    """The two namespaces for one book produce an identical slug."""
    assert canonical_book_slug("The-Hidden-Keys") == canonical_book_slug(
        "Hidden Keys, The - Andre Alexis"
    )


def test_only_last_author_segment_is_stripped():
    """A hyphen-with-spaces inside the title survives; only the author drops."""
    assert canonical_book_slug("Butcher's Crossing - (a novel) - John Williams") == (
        "butcher-s-crossing-a-novel"
    )
