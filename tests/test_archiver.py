from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
import io

from PIL import Image

from xteink_service.archiver import ScreenshotArchiver


def _resp_json(data):
    """Async context manager mock that yields .json() -> data."""
    r = AsyncMock()
    r.json = AsyncMock(return_value=data)
    r.__aenter__ = AsyncMock(return_value=r)
    r.__aexit__ = AsyncMock(return_value=False)
    return r


def _resp_bytes(data: bytes):
    """Async context manager mock that yields .read() -> data."""
    r = AsyncMock()
    r.read = AsyncMock(return_value=data)
    r.__aenter__ = AsyncMock(return_value=r)
    r.__aexit__ = AsyncMock(return_value=False)
    return r


def _make_archiver():
    return ScreenshotArchiver("/vault", "crosspoint.local")


def _make_bmp(width: int = 2, height: int = 2) -> bytes:
    """Create a minimal valid BMP in memory."""
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


# ------------------------------------------------------------------ #
# _list_screenshots                                                    #
# ------------------------------------------------------------------ #

async def test_list_screenshots_returns_bmps_only():
    """Only .bmp files inside book directories are returned."""
    archiver = _make_archiver()
    session = MagicMock()
    session.get = MagicMock(side_effect=[
        _resp_json([
            {"name": "MyBook", "isDirectory": True},
            {"name": "stray.txt", "isDirectory": False},   # ignored
        ]),
        _resp_json([
            {"name": "shot001.bmp", "isDirectory": False, "mtime": 1751500800},
            {"name": "shot002.bmp", "isDirectory": False, "mtime": 1751500800},
            {"name": "thumb.png",   "isDirectory": False, "mtime": 1751500800},  # ignored
            {"name": "subdir",      "isDirectory": True,  "mtime": 1751500800},  # ignored
        ]),
    ])

    results = await archiver._list_screenshots(session)

    assert len(results) == 2
    books, _, paths = zip(*results)
    assert all(b == "MyBook" for b in books)
    assert all(p.endswith(".bmp") for p in paths)


async def test_list_screenshots_groups_by_mtime():
    """day is derived from the file's mtime."""
    archiver = _make_archiver()
    mtime = 1751500800
    session = MagicMock()
    session.get = MagicMock(side_effect=[
        _resp_json([{"name": "Book", "isDirectory": True}]),
        _resp_json([{"name": "a.bmp", "isDirectory": False, "mtime": mtime}]),
    ])

    results = await archiver._list_screenshots(session)

    _, day, _ = results[0]
    assert day == datetime.fromtimestamp(mtime).date()


async def test_list_screenshots_multiple_books():
    """Each book directory is listed separately."""
    archiver = _make_archiver()
    session = MagicMock()
    session.get = MagicMock(side_effect=[
        _resp_json([
            {"name": "BookA", "isDirectory": True},
            {"name": "BookB", "isDirectory": True},
        ]),
        _resp_json([{"name": "a.bmp", "isDirectory": False, "mtime": 1751500800}]),
        _resp_json([{"name": "b.bmp", "isDirectory": False, "mtime": 1751500800},
                    {"name": "c.bmp", "isDirectory": False, "mtime": 1751500800}]),
    ])

    results = await archiver._list_screenshots(session)

    assert len(results) == 3
    assert {r[0] for r in results} == {"BookA", "BookB"}


# ------------------------------------------------------------------ #
# _download_file                                                       #
# ------------------------------------------------------------------ #

async def test_download_file_returns_bytes():
    """_download_file fetches /download with path param and returns raw bytes."""
    archiver = _make_archiver()
    session = MagicMock()
    session.get = MagicMock(return_value=_resp_bytes(b"bmp-payload"))

    result = await archiver._download_file(session, "/screenshots/Book/shot.bmp")

    assert result == b"bmp-payload"
    session.get.assert_called_once_with(
        "http://crosspoint.local/download",
        params={"path": "/screenshots/Book/shot.bmp"},
    )


# ------------------------------------------------------------------ #
# _bmp_to_png                                                         #
# ------------------------------------------------------------------ #

def test_bmp_to_png_produces_valid_png():
    """_bmp_to_png converts BMP bytes to a valid PNG."""
    bmp = _make_bmp()
    png = ScreenshotArchiver._bmp_to_png(bmp)

    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG"


def test_bmp_to_png_preserves_dimensions():
    """Output PNG has the same dimensions as the input BMP."""
    bmp = _make_bmp(width=4, height=8)
    png = ScreenshotArchiver._bmp_to_png(bmp)

    img = Image.open(io.BytesIO(png))
    assert img.size == (4, 8)


# ------------------------------------------------------------------ #
# _embed_ocr_in_png                                                   #
# ------------------------------------------------------------------ #

def test_embed_ocr_roundtrip():
    """OCR text embedded as iTXt can be read back via img.info."""
    png = ScreenshotArchiver._bmp_to_png(_make_bmp())
    with_ocr = ScreenshotArchiver._embed_ocr_in_png(png, "Hello world")

    img = Image.open(io.BytesIO(with_ocr))
    assert img.info.get("ocr_text") == "Hello world"


def test_embed_ocr_preserves_image():
    """Embedding OCR text does not change the image dimensions."""
    png = ScreenshotArchiver._bmp_to_png(_make_bmp(width=3, height=5))
    with_ocr = ScreenshotArchiver._embed_ocr_in_png(png, "text")

    img = Image.open(io.BytesIO(with_ocr))
    assert img.size == (3, 5)


# ------------------------------------------------------------------ #
# _parse_filename / _status_label                                     #
# ------------------------------------------------------------------ #

def test_parse_filename_extracts_chapter_and_page():
    assert ScreenshotArchiver._parse_filename("Pastoral_ch8_p25_20pct_480360.bmp") == {
        "chapter": 8, "page": 25
    }


def test_parse_filename_returns_empty_for_unknown_format():
    assert ScreenshotArchiver._parse_filename("screenshot_001.bmp") == {}


def test_status_label_with_chapter_page():
    label = ScreenshotArchiver._status_label("Pastoral", "/screenshots/Pastoral/Pastoral_ch8_p25_20pct.bmp", 3, 10)
    assert "[3/10]" in label
    assert "ch8" in label
    assert "p25" in label


def test_status_label_fallback_without_chapter_page():
    label = ScreenshotArchiver._status_label("MyBook", "/screenshots/MyBook/img001.bmp", 1, 5)
    assert "[1/5]" in label
    assert "MyBook" in label


# ------------------------------------------------------------------ #
# _ocr_image                                                          #
# ------------------------------------------------------------------ #

def test_ocr_image_returns_extracted_text():
    """_ocr_image returns the stripped string from pytesseract."""
    png = ScreenshotArchiver._bmp_to_png(_make_bmp())
    with patch("xteink_service.archiver.pytesseract.image_to_string",
               return_value="  Hello world  "):
        result = ScreenshotArchiver._ocr_image(png)
    assert result == "Hello world"


def test_ocr_image_returns_none_on_empty_output():
    """Blank OCR output (whitespace only) returns None."""
    png = ScreenshotArchiver._bmp_to_png(_make_bmp())
    with patch("xteink_service.archiver.pytesseract.image_to_string",
               return_value="   "):
        result = ScreenshotArchiver._ocr_image(png)
    assert result is None


def test_ocr_image_returns_none_on_tesseract_error():
    """If Tesseract raises, _ocr_image returns None without crashing."""
    png = ScreenshotArchiver._bmp_to_png(_make_bmp())
    with patch("xteink_service.archiver.pytesseract.image_to_string",
               side_effect=Exception("tesseract not found")):
        result = ScreenshotArchiver._ocr_image(png)
    assert result is None


# ------------------------------------------------------------------ #
# _group_consecutive                                                   #
# ------------------------------------------------------------------ #

def _shot(book: str, chapter: int, page: int, day: date = date(2026, 7, 4)):
    """Build a (book, day, filepath) tuple with a parseable screenshot name."""
    name = f"{book}_ch{chapter}_p{page}_10pct_480360.bmp"
    return (book, day, f"/screenshots/{book}/{name}")


def _pages(group):
    """Page numbers of a group, in group order."""
    return [
        ScreenshotArchiver._parse_filename(item[3].rsplit("/", 1)[-1])["page"]
        for item in group
    ]


def test_group_consecutive_merges_a_run():
    groups = ScreenshotArchiver._group_consecutive(
        [_shot("Pastoral", 8, 25), _shot("Pastoral", 8, 26), _shot("Pastoral", 8, 27)]
    )
    assert len(groups) == 1
    assert _pages(groups[0]) == [25, 26, 27]


def test_group_consecutive_breaks_on_page_gap():
    groups = ScreenshotArchiver._group_consecutive(
        [_shot("Pastoral", 8, 25), _shot("Pastoral", 8, 27)]
    )
    assert len(groups) == 2
    assert all(len(g) == 1 for g in groups)


def test_group_consecutive_separates_chapters():
    groups = ScreenshotArchiver._group_consecutive(
        [_shot("Pastoral", 8, 25), _shot("Pastoral", 9, 26)]
    )
    assert len(groups) == 2


def test_group_consecutive_separates_books():
    groups = ScreenshotArchiver._group_consecutive(
        [_shot("BookA", 1, 1), _shot("BookB", 1, 2)]
    )
    assert len(groups) == 2


def test_group_consecutive_sorts_out_of_order_pages():
    groups = ScreenshotArchiver._group_consecutive(
        [_shot("Pastoral", 8, 27), _shot("Pastoral", 8, 25), _shot("Pastoral", 8, 26)]
    )
    assert len(groups) == 1
    assert _pages(groups[0]) == [25, 26, 27]


def test_group_consecutive_unparseable_names_are_singletons():
    day = date(2026, 7, 4)
    groups = ScreenshotArchiver._group_consecutive([
        ("Book", day, "/screenshots/Book/img001.bmp"),
        ("Book", day, "/screenshots/Book/img002.bmp"),
    ])
    assert len(groups) == 2
    assert all(len(g) == 1 for g in groups)


def test_group_consecutive_caps_run_length():
    shots = [_shot("Pastoral", 8, p) for p in range(1, 10)]  # 9 consecutive pages
    groups = ScreenshotArchiver._group_consecutive(shots, max_run=6)
    assert [len(g) for g in groups] == [6, 3]


# ------------------------------------------------------------------ #
# _stitch_pngs                                                         #
# ------------------------------------------------------------------ #

def test_stitch_pngs_stacks_vertically():
    a = ScreenshotArchiver._bmp_to_png(_make_bmp(width=4, height=3))
    b = ScreenshotArchiver._bmp_to_png(_make_bmp(width=6, height=5))
    img = Image.open(io.BytesIO(ScreenshotArchiver._stitch_pngs([a, b])))
    assert img.size == (6, 8)  # width = max(4, 6), height = 3 + 5


def test_stitch_pngs_single_image_unchanged_size():
    a = ScreenshotArchiver._bmp_to_png(_make_bmp(width=4, height=3))
    img = Image.open(io.BytesIO(ScreenshotArchiver._stitch_pngs([a])))
    assert img.size == (4, 3)
