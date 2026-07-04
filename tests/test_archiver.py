from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from xteink_service.archiver import ScreenshotArchiver


def _resp(data):
    """Return an async context manager mock that yields .json() -> data."""
    r = AsyncMock()
    r.json = AsyncMock(return_value=data)
    r.__aenter__ = AsyncMock(return_value=r)
    r.__aexit__ = AsyncMock(return_value=False)
    return r


def _make_archiver():
    return ScreenshotArchiver("/vault", "crosspoint.local")


async def test_list_screenshots_returns_bmps_only():
    """Only .bmp files inside book directories are returned."""
    archiver = _make_archiver()

    session = MagicMock()
    session.get = MagicMock(side_effect=[
        _resp([
            {"name": "MyBook", "isDirectory": True},
            {"name": "stray.txt", "isDirectory": False},   # ignored
        ]),
        _resp([
            {"name": "shot001.bmp", "isDirectory": False, "mtime": 1751500800},
            {"name": "shot002.bmp", "isDirectory": False, "mtime": 1751500800},
            {"name": "thumb.png",   "isDirectory": False, "mtime": 1751500800},  # ignored
            {"name": "subdir",      "isDirectory": True,  "mtime": 1751500800},  # ignored
        ]),
    ])

    results = await archiver._list_screenshots(session)

    assert len(results) == 2
    books, days, paths = zip(*results)
    assert all(b == "MyBook" for b in books)
    assert all(p.endswith(".bmp") for p in paths)


async def test_list_screenshots_groups_by_mtime():
    """day is derived from the file's mtime."""
    archiver = _make_archiver()

    mtime = 1751500800  # 2025-07-02 or similar fixed timestamp
    session = MagicMock()
    session.get = MagicMock(side_effect=[
        _resp([{"name": "Book", "isDirectory": True}]),
        _resp([{"name": "a.bmp", "isDirectory": False, "mtime": mtime}]),
    ])

    results = await archiver._list_screenshots(session)

    assert len(results) == 1
    _, day, _ = results[0]
    from datetime import datetime
    assert day == datetime.fromtimestamp(mtime).date()


async def test_list_screenshots_multiple_books():
    """Each book directory is listed separately."""
    archiver = _make_archiver()

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
