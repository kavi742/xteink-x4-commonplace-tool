from unittest.mock import AsyncMock, MagicMock, patch

from xteink_service.watcher import poll_for_device, wait_for_offline


def _mock_session(status: int):
    """Return a patched aiohttp.ClientSession that always responds with `status`."""
    resp = MagicMock(status=status)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.get = MagicMock(return_value=resp)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


async def test_poll_returns_host_on_200():
    with patch("xteink_service.watcher.aiohttp.ClientSession", return_value=_mock_session(200)):
        result = await poll_for_device("test.local", interval=0)
    assert result == "test.local"


async def test_wait_for_offline_returns_on_non_200():
    with patch("xteink_service.watcher.aiohttp.ClientSession", return_value=_mock_session(503)):
        await wait_for_offline("test.local", interval=0)  # must not hang


async def test_wait_for_offline_returns_on_connection_error():
    session = MagicMock()
    session.get = MagicMock(side_effect=ConnectionError)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    with patch("xteink_service.watcher.aiohttp.ClientSession", return_value=session):
        await wait_for_offline("test.local", interval=0)  # must not hang
