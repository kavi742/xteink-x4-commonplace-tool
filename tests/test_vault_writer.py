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

    expected = tmp_path / "Books" / "Pastoral" / "2026-07-03-01.png"
    assert expected.exists()
    assert expected.read_bytes() == b"pngbytes"
    assert embed == "Pastoral/2026-07-03-01.png"

def test_write_screenshot_sanitizes_book_title(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.write_screenshot("A/B:C", date(2026, 7, 3), b"x", 1)
    book_dir = tmp_path / "Books" / "ABC"
    assert book_dir.exists()


# ------------------------------------------------------------------ #
# append_to_daily_note                                                 #
# ------------------------------------------------------------------ #

def test_append_creates_note_with_frontmatter(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.append_to_daily_note("Pastoral", date(2026, 7, 3), "Pastoral/2026-07-03-01.png")

    note = tmp_path / "Books" / "Pastoral.md"
    content = note.read_text()
    assert 'title: "Pastoral"' in content
    assert "## 2026-07-03" in content
    assert "![[Pastoral/2026-07-03-01.png]]" in content

def test_append_adds_collapsible_ocr_callout(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.append_to_daily_note(
        "Pastoral", date(2026, 7, 3), "Pastoral/img.png",
        ocr_text="Hello\nworld"
    )
    content = (tmp_path / "Books" / "Pastoral.md").read_text()
    assert "> [!quote] OCR text" in content
    assert "> Hello" in content
    assert "> world" in content

def test_append_skips_callout_when_no_ocr(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.append_to_daily_note("Pastoral", date(2026, 7, 3), "Pastoral/img.png", ocr_text=None)
    content = (tmp_path / "Books" / "Pastoral.md").read_text()
    assert "[!quote]" not in content

def test_append_accumulates_multiple_screenshots_under_same_heading(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.append_to_daily_note("Book", date(2026, 7, 3), "Book/img1.png")
    vw.append_to_daily_note("Book", date(2026, 7, 3), "Book/img2.png")
    content = (tmp_path / "Books" / "Book.md").read_text()
    assert "img1.png" in content
    assert "img2.png" in content
    assert content.count("## 2026-07-03") == 1  # heading appears only once


# ------------------------------------------------------------------ #
# Reading log + book timeline                                          #
# ------------------------------------------------------------------ #

def test_write_reading_log(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.write_reading_log(date(2026, 7, 4), "Pastoral", 45.3)
    content = (tmp_path / "Reading Log" / "2026-07-04.md").read_text()
    assert "Pastoral" in content
    assert "45.3%" in content


def test_write_reading_log_cross_day_range(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.write_reading_log(
        date(2026, 7, 4), "Pastoral", 45.3,
        prev_percentage=22.1, prev_day=date(2026, 7, 3)
    )
    content = (tmp_path / "Reading Log" / "2026-07-04.md").read_text()
    assert "22.1%" in content
    assert "45.3%" in content
    assert "→" in content


def test_write_reading_log_same_day_replaces(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.write_reading_log(date(2026, 7, 4), "Pastoral", 22.1)
    vw.write_reading_log(date(2026, 7, 4), "Pastoral", 45.3)
    content = (tmp_path / "Reading Log" / "2026-07-04.md").read_text()
    assert content.count("Pastoral") == 1
    assert "45.3%" in content
    assert "22.1%" not in content


def test_update_book_timeline_same_day_replaces(tmp_path):
    import re
    vw = VaultWriter(str(tmp_path))
    vw.update_book_timeline("Pastoral", "", date(2026, 7, 4), 22.1)
    vw.update_book_timeline("Pastoral", "", date(2026, 7, 4), 45.3, first_today_pct=22.1)
    content = (tmp_path / "Books" / "Pastoral.md").read_text()
    assert content.count("## 2026-07-04") == 1
    assert "22.1%" in content  # start of range
    assert "45.3%" in content
    assert len(re.findall(r'^- [\d]', content, re.MULTILINE)) == 1


def test_update_book_timeline_with_cfi(tmp_path):
    vw = VaultWriter(str(tmp_path))
    vw.update_book_timeline("Pastoral", "", date(2026, 7, 4), 45.3,
                            progress="/body/DocFragment[12]/body")
    content = (tmp_path / "Books" / "Pastoral.md").read_text()
    assert "§12" in content
