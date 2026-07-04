"""
Integration test: builds a realistic vault from synthetic data and leaves it on
disk so you can inspect it.

Run:
    uv run pytest tests/test_vault_integration.py -v -s

Vault is written to test_scripts/vault/ (recreated on each run).
"""
import io
import shutil
import textwrap
from datetime import date
from pathlib import Path

import json

import pytest
from PIL import Image, ImageDraw, ImageFont

from xteink_service.archiver import ScreenshotArchiver
from xteink_service.vault_writer import VaultWriter

VAULT = Path(__file__).parent.parent / "test_scripts" / "vault"

# Realistic OCR passages from Pastoral (Nicholson Baker)
_PASSAGES = [
    """\
Apple. Now, because Bigland did not get home until after midnight, he slept
late the next morning, and when he woke, he lay for a while listening to the
crows before he got up. He felt rested. The morning was bright. He could
hear his wife downstairs, and the sound of her made him happy.\
""",
    """\
The great elm was gone. In its place a raw stump, pale as a wound, still
smelling of sawdust and sap. The tree had been there as long as anyone could
remember. Bigland stood looking at it for a long time, his hands in his
pockets, not quite able to believe it.\
""",
    """\
He walked out into the field and looked back at the house. It was a good
house. He had built most of it himself, working evenings and weekends over
three years. The clapboards were unpainted — he had never got around to
painting them — and had weathered to a silver-grey that he found he liked
better than paint would have looked anyway.\
""",
]


def _make_png(text: str = "", width: int = 480, height: int = 360) -> bytes:
    """Create an e-ink-style PNG with the given text rendered on it."""
    img = Image.new("RGB", (width, height), color=(245, 242, 235))
    if text:
        draw = ImageDraw.Draw(img)
        font = ImageFont.load_default(size=16)
        margin, line_height = 24, 22
        for i, line in enumerate(textwrap.wrap(text, width=50)):
            draw.text((margin, margin + i * line_height), line, fill=(30, 30, 30), font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module", autouse=True)
def fresh_vault():
    """Wipe and recreate the output vault before the module's tests run."""
    if VAULT.exists():
        shutil.rmtree(VAULT)
    VAULT.mkdir(parents=True)
    yield
    # intentionally NOT cleaning up — leave vault for inspection


# ------------------------------------------------------------------ #
# Screenshots → vault                                                  #
# ------------------------------------------------------------------ #

def test_write_screenshots_pastoral_july4():
    vw = VaultWriter(str(VAULT))
    for i, ocr in enumerate(_PASSAGES, start=1):
        png = _make_png(ocr)
        png = ScreenshotArchiver._embed_ocr_in_png(png, ocr)
        fake_path = f"/screenshots/Pastoral/shot{i:03d}.bmp"
        fake_hash = f"deadbeef{i:04d}"
        embed = vw.write_screenshot("Pastoral", date(2026, 7, 4), png, i)
        vw.write_screenshot_meta(embed, fake_path, fake_hash, "Pastoral", "2026-07-04", ocr)
        vw.append_to_daily_note("Pastoral", date(2026, 7, 4), embed, ocr)

    note = VAULT / "Books" / "Pastoral.md"
    assert note.exists(), "Pastoral.md not created"
    assert (VAULT / "Books" / "Pastoral").exists(), "attachment dir not created"
    # JSON sidecars should exist alongside every PNG
    sidecars = list((VAULT / "Books" / "Pastoral").glob("*.json"))
    assert sidecars, "no JSON sidecar files written"
    meta = json.loads(sidecars[0].read_text())
    assert "ocr_text" in meta and "content_hash" in meta


def test_write_screenshots_pastoral_earlier_days():
    vw = VaultWriter(str(VAULT))
    for day, ocr in [
        (date(2026, 7, 3), _PASSAGES[1]),
        (date(2026, 7, 2), _PASSAGES[0]),
    ]:
        png = _make_png(ocr)
        png = ScreenshotArchiver._embed_ocr_in_png(png, ocr)
        embed = vw.write_screenshot("Pastoral", day, png, 1)
        vw.append_to_daily_note("Pastoral", day, embed, ocr)


# ------------------------------------------------------------------ #
# Reading log / book timeline                                          #
# ------------------------------------------------------------------ #

def test_write_reading_log():
    vw = VaultWriter(str(VAULT))
    vw.write_reading_log(date(2026, 7, 4), "Pastoral", 45.3,
                         progress="/body/DocFragment[11]/body",
                         prev_percentage=22.1, prev_day=date(2026, 7, 3))

    log = VAULT / "Reading Log" / "2026-07-04.md"
    assert log.exists()
    assert "Pastoral" in log.read_text()


def test_update_book_timeline():
    vw = VaultWriter(str(VAULT))
    vw.update_book_timeline("Pastoral", "Nicholson Baker", date(2026, 7, 4), 45.3,
                            progress="/body/DocFragment[11]/body",
                            first_today_pct=22.1)


# ------------------------------------------------------------------ #
# Print the vault tree so it shows up in pytest -s output             #
# ------------------------------------------------------------------ #

def test_print_vault_tree():
    print(f"\n\nVault written to: {VAULT}\n")
    for path in sorted(VAULT.rglob("*")):
        if ".obsidian" in path.parts:
            continue
        indent = "  " * (len(path.relative_to(VAULT).parts) - 1)
        print(f"{indent}{path.name}{'/' if path.is_dir() else ''}")
    print()
    for md in sorted(VAULT.rglob("*.md")):
        print(f"\n--- {md.relative_to(VAULT)} ---")
        print(md.read_text())
