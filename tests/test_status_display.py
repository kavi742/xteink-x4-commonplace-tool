from unittest.mock import AsyncMock, patch

from xteink_service.status_display import x4_status


async def test_show_sends_correct_protocol():
    """show() sends START, waits for READY, sends dummy byte, drains response."""
    ws = AsyncMock()
    ws.recv = AsyncMock(side_effect=["READY", "DONE"])

    with patch(
        "xteink_service.status_display.websockets.connect",
        new=AsyncMock(return_value=ws),
    ):
        async with x4_status("test.local") as show:
            await show("Hello")

    ws.send.assert_any_call("START:Hello:1:/")
    ws.send.assert_any_call(b"X")
    ws.close.assert_called_once()


async def test_graceful_degradation_on_connection_failure():
    """x4_status yields a no-op callable when the WebSocket connection fails."""
    with patch(
        "xteink_service.status_display.websockets.connect",
        side_effect=ConnectionRefusedError,
    ):
        async with x4_status("unreachable.local") as show:
            await show("should not crash")  # must not raise


async def test_show_tolerates_unexpected_server_response():
    """show() silently ignores a non-READY server response."""
    ws = AsyncMock()
    ws.recv = AsyncMock(return_value="ERROR")

    with patch(
        "xteink_service.status_display.websockets.connect",
        new=AsyncMock(return_value=ws),
    ):
        async with x4_status("test.local") as show:
            await show("Hello")  # must not raise

    ws.close.assert_called_once()
