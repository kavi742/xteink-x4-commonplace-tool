from unittest.mock import AsyncMock, patch

from xteink_service.status_display import x4_status


async def test_show_sends_correct_protocol():
    """show() with no data sends START:msg:0, reads DONE (text-only, no bar)."""
    ws = AsyncMock()
    ws.recv = AsyncMock(return_value="DONE")

    with patch(
        "xteink_service.status_display.websockets.connect",
        new=AsyncMock(return_value=ws),
    ):
        async with x4_status("test.local") as show:
            await show("Hello")

    ws.send.assert_called_once_with("START:Hello:0:/")
    ws.close.assert_called_once()


async def test_show_with_data_streams_bytes_and_fills_bar():
    """show(msg, data=b'...') sends actual bytes so the progress bar fills correctly."""
    ws = AsyncMock()
    ws.recv = AsyncMock(side_effect=["READY", "DONE"])

    with patch(
        "xteink_service.status_display.websockets.connect",
        new=AsyncMock(return_value=ws),
    ):
        async with x4_status("test.local") as show:
            await show("Transferring", data=b"BMPDATA")

    ws.send.assert_any_call("START:Transferring:7:/")
    ws.send.assert_any_call(b"BMPDATA")
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
