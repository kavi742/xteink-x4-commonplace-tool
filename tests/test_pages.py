"""Tests for xteink_service.pages — epub word-count → page estimation.

Unit tests build a synthetic epub in memory. The live test downloads a real
book from the X4 (skipped unless it's in File Transfer mode).
"""
import io
import os
import urllib.parse
import urllib.request
import zipfile

import pytest

from xteink_service import pages


def _make_epub(word_count: int, docs: int = 1, extra_noise: bool = True) -> bytes:
    """Build a minimal epub whose content docs hold exactly `word_count` words."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        # Non-content parts that must NOT be counted:
        if extra_noise:
            z.writestr("OEBPS/content.opf", "<package><spine>ignore me words</spine></package>")
            z.writestr("toc.ncx", "<ncx>also not counted here</ncx>")
        per = word_count // docs
        remainder = word_count - per * docs
        for i in range(docs):
            n = per + (remainder if i == docs - 1 else 0)
            body = " ".join(f"word{j}" for j in range(n))
            z.writestr(f"OEBPS/ch{i}.xhtml", f"<html><body><p>{body}</p></body></html>")
    return buf.getvalue()


def test_count_words_ignores_markup_and_noncontent():
    assert pages.count_words(_make_epub(1000)) == 1000


def test_count_words_across_multiple_docs():
    assert pages.count_words(_make_epub(1234, docs=5)) == 1234


def test_count_words_bad_input_returns_zero():
    assert pages.count_words(b"not a zip") == 0
    assert pages.count_words(b"") == 0


def test_estimate_total_pages():
    assert pages.estimate_total_pages(_make_epub(2750)) == 10          # 2750/275
    assert pages.estimate_total_pages(_make_epub(1375)) == 5


def test_estimate_custom_density():
    assert pages.estimate_total_pages(_make_epub(1000), words_per_page=250) == 4


def test_estimate_minimum_one_page():
    assert pages.estimate_total_pages(_make_epub(5)) == 1


def test_estimate_empty_book_is_zero():
    assert pages.estimate_total_pages(b"not a zip") == 0


def test_page_at_maps_percentage():
    assert pages.page_at(0.0, 200) == 1
    assert pages.page_at(0.5, 200) == 100
    assert pages.page_at(1.0, 200) == 200
    assert pages.page_at(0.543, 200) == 109


def test_page_at_no_total():
    assert pages.page_at(0.5, 0) == 0


# ------------------------------------------------------------------ #
# Live test — requires the X4 in File Transfer mode                    #
# ------------------------------------------------------------------ #

def test_live_estimate_real_book():
    host = os.getenv("DEVICE_HOST", "crosspoint.local")
    path = "/Alexis, Andre/Fifteen Dogs - Andre Alexis.epub"
    url = f"http://{host}/download?path={urllib.parse.quote(path)}"
    try:
        data = urllib.request.urlopen(url, timeout=10).read()
    except Exception as exc:
        pytest.skip(f"device not reachable ({exc}); needs File Transfer mode")

    words = pages.count_words(data)
    est = pages.estimate_total_pages(data)
    print(f"\nFifteen Dogs: {words} words -> ~{est} pages "
          f"(54.3% -> p{pages.page_at(0.543, est)}, 85% -> p{pages.page_at(0.85, est)})")
    assert words > 10000        # a real novel
    assert 100 < est < 500      # sane page range
