from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
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


    session = MagicMock()
    session.get = MagicMock(side_effect=[
        _resp([
            {"name": "BookA", "isDirectory": True},
            {"name": "BookB", "isDirectory": True},
        ]),
        _resp([{"name": "a.bmp", "isDirectory": False, "mtime": 1751500800}]),
        _resp([{"name": "b.bmp", "isDirectory": False, "mtime": 1751500800},
               {"name": "c.bmp", "isDirectory": False, "mtime": 1751500800}]),
    ])

    results = await archiver._list_screenshots(session)

    assert len(results) == 3
    assert {r[0] for r in results} == {"BookA", "BookB"}
