"""Estimate a book's page count from its epub.

Used to turn KOReader reading percentages into approximate page numbers.

Why estimate? The X4's File Transfer API exposes only the book library, not
KOReader's ``statistics.sqlite3`` or per-book ``.sdr`` sidecars, and these epubs
carry no EPUB3 page-list. So the only server-side signal is the text itself:
count the words and divide by a typical page density. The result is a
book-length approximation (~WORDS_PER_PAGE words/page), not the device's exact
on-screen pagination.
"""
import io
import re
import zipfile

# Trade-paperback density; tune if estimates skew high/low.
WORDS_PER_PAGE = 275

_TAG_RE = re.compile(r"<[^>]+>")
_CONTENT_EXT = (".xhtml", ".html", ".htm")


def count_words(epub_bytes: bytes) -> int:
    """Total word count across an epub's (X)HTML content documents.

    Non-content parts (mimetype, OPF, NCX, images) are ignored. Returns 0 for a
    non-epub / unreadable input rather than raising.
    """
    words = 0
    try:
        with zipfile.ZipFile(io.BytesIO(epub_bytes)) as z:
            for name in z.namelist():
                if not name.lower().endswith(_CONTENT_EXT):
                    continue
                try:
                    text = z.read(name).decode("utf-8", "ignore")
                except Exception:
                    continue
                words += len(_TAG_RE.sub(" ", text).split())
    except (zipfile.BadZipFile, Exception):
        return 0
    return words


def estimate_total_pages(epub_bytes: bytes, words_per_page: int = WORDS_PER_PAGE) -> int:
    """Estimate a book's total pages from its word count (minimum 1)."""
    if words_per_page <= 0:
        words_per_page = WORDS_PER_PAGE
    words = count_words(epub_bytes)
    if words <= 0:
        return 0
    return max(1, round(words / words_per_page))


def page_at(percentage: float, total_pages: int) -> int:
    """Map a KOReader percentage (0.0-1.0) to a 1-based page number."""
    if total_pages <= 0:
        return 0
    return max(1, min(total_pages, round(percentage * total_pages)))
