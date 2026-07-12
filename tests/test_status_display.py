"""Tests for xteink_service.status_display after the port-81 refactor.

Status text is now logged server-side (never written to the device), so no junk
files are created. cleanup_device_junk removes 0-byte files the old mechanism
left at the device root.
"""
import logging

import pytest

from xteink_service import status_display
from xteink_service.status_display import cleanup_device_junk, x4_status


class _FakeResp:
    def __init__(self, status=200, json_data=None):
        self.status = status
        self._json = json_data

    async def json(self):
        return self._json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    """Minimal stand-in for aiohttp.ClientSession used by cleanup."""

    def __init__(self, items, get_status=200, delete_status=200):
        self._items = items
        self._get_status = get_status
        self._delete_status = delete_status
        self.deleted: list[str] = []

    def get(self, url, params=None, **kw):
        return _FakeResp(self._get_status, self._items)

    def delete(self, url, params=None, **kw):
        self.deleted.append(params["path"])
        return _FakeResp(self._delete_status)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch_session(monkeypatch, session):
    monkeypatch.setattr(
        status_display.aiohttp, "ClientSession", lambda *a, **kw: session
    )


async def test_show_logs_and_never_touches_device(monkeypatch, caplog):
    """show() only logs — it must not open any aiohttp session."""
    def _boom(*a, **kw):
        raise AssertionError("show() must not contact the device")

    monkeypatch.setattr(status_display.aiohttp, "ClientSession", _boom)
    with caplog.at_level(logging.INFO):
        async with x4_status("crosspoint.local") as show:
            await show("Resolving titles...")
            await show("label", data=b"\x00\x01\x02")  # data is ignored, no write

    assert "X4 status: Resolving titles..." in caplog.text


async def test_cleanup_deletes_only_zero_byte_root_files(monkeypatch):
    items = [
        {"name": "Resolving titles", "isDirectory": False, "size": 0},   # junk
        {"name": "Mapping 207 book(s)", "isDirectory": False, "size": 0},  # junk
        {"name": "Alexis, Andre", "isDirectory": True, "size": 0},        # book folder
        {"name": "keepme.epub", "isDirectory": False, "size": 1234},      # real file
    ]
    session = _FakeSession(items)
    _patch_session(monkeypatch, session)

    removed = await cleanup_device_junk("crosspoint.local")

    assert removed == 2
    assert session.deleted == ["/Resolving titles", "/Mapping 207 book(s)"]


async def test_cleanup_handles_unreachable_device(monkeypatch):
    session = _FakeSession([], get_status=404)
    _patch_session(monkeypatch, session)
    assert await cleanup_device_junk("crosspoint.local") == 0
    assert session.deleted == []


async def test_cleanup_counts_only_successful_deletes(monkeypatch):
    items = [{"name": "junk", "isDirectory": False, "size": 0}]
    session = _FakeSession(items, delete_status=405)  # firmware rejects delete
    _patch_session(monkeypatch, session)
    assert await cleanup_device_junk("crosspoint.local") == 0
