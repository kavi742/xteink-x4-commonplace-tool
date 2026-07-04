import re
from datetime import date
from pathlib import Path

import pytest

from xteink_service.vault_writer import VaultWriter, _sanitize


# ------------------------------------------------------------------ #
# _sanitize                                                            #
# ------------------------------------------------------------------ #

def test_sanitize_strips_special_chars():
    assert _sanitize("The Great Gatsby!") == "The Great Gatsby"

def test_sanitize_allows_spaces_and_dots():
    assert _sanitize("My Book 2.0") == "My Book 2.0"

def test_sanitize_strips_slashes():
    assert _sanitize("Book/Title") == "BookTitle"


# ------------------------------------------------------------------ #
# write_screenshot                                                     #
# ------------------------------------------------------------------ #

def test_write_screenshot_creates_file(tmp_path):
    vw = VaultWriter(str(tmp_path))
    embed = vw.write_screenshot("Pastoral", date(2026, 7, 3), b"pngbytes", 1)

    expected = tmp_path / "Commonplace" / "Pastoral" / "attachments" / "2026-07-03-01.png"
    assert expected.exists()
    assert expected.read_bytes() == b"pngbytes"
    assert embed == "attachments/2026-07-03-01.png"

def test_write_screenshot_sanitizes_book_title(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.write_screenshot("A/B:C", date(2026, 7, 3), b"x", 1)
    book_dir = tmp_path / "Commonplace" / "ABC"
    assert book_dir.exists()


# ------------------------------------------------------------------ #
# append_to_daily_note                                                 #
# ------------------------------------------------------------------ #

def test_append_creates_note_with_header(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.append_to_daily_note("Pastoral", date(2026, 7, 3), "attachments/2026-07-03-01.png")

    note = tmp_path / "Commonplace" / "Pastoral" / "2026-07-03.md"
    content = note.read_text()
    assert "# 2026-07-03" in content
    assert "![[attachments/2026-07-03-01.png]]" in content

def test_append_adds_collapsible_ocr_callout(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.append_to_daily_note(
        "Pastoral", date(2026, 7, 3), "attachments/img.png",
        ocr_text="Hello\nworld"
    )
    content = (tmp_path / "Commonplace" / "Pastoral" / "2026-07-03.md").read_text()
    assert "> [!quote]- OCR text" in content
    assert "> Hello" in content
    assert "> world" in content

def test_append_skips_callout_when_no_ocr(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.append_to_daily_note("Pastoral", date(2026, 7, 3), "attachments/img.png", ocr_text=None)
    content = (tmp_path / "Commonplace" / "Pastoral" / "2026-07-03.md").read_text()
    assert "[!quote]" not in content

def test_append_accumulates_multiple_screenshots(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.append_to_daily_note("Book", date(2026, 7, 3), "attachments/img1.png")
    vw.append_to_daily_note("Book", date(2026, 7, 3), "attachments/img2.png")
    content = (tmp_path / "Commonplace" / "Book" / "2026-07-03.md").read_text()
    assert "img1.png" in content
    assert "img2.png" in content
