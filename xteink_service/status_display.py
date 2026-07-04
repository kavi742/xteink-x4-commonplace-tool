import asyncio
import contextlib
import logging

import websockets

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def x4_status(host: str):
    """
    Async context manager: connect to the X4 status WebSocket (port 81).
    Yields a show(message) callable; no-ops gracefully if connection fails.
    """
    ws = None
    try:
        ws = await websockets.connect(f"ws://{host}:81/")
        logger.debug("WebSocket connected to %s:81", host)

        async def show(message: str, data: bytes | None = None) -> None:
            try:
                if data is not None:
                    # Genuine progress bar — stream actual bytes
                    await ws.send(f"START:{message}:{len(data)}:/")
                    if await ws.recv() != "READY":
                        return
                    chunk = 4096
                    for i in range(0, len(data), chunk):
                        await ws.send(data[i : i + chunk])
                    await ws.recv()  # DONE
                else:
                    # Text-only status message, no progress bar
                    await ws.send(f"START:{message}:0:/")
                    await ws.recv()  # DONE
            except Exception:
                pass

        yield show
    except Exception as e:
        logger.warning("Could not connect to X4 status display: %s", e)

        async def _noop(_: str, __: bytes | None = None) -> None:
            pass

        yield _noop
    finally:
        if ws:
            await ws.close()


if __name__ == "__main__":
    # Live test: python xteink_service/status_display.py [host] [message]
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _host = sys.argv[1] if len(sys.argv) > 1 else "crosspoint.local"
    _msg = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "Hello from xteink-service"

    async def _test() -> None:
        async with x4_status(_host) as show:
            await show(_msg)
            await asyncio.sleep(5)  # keep visible on screen

    try:
        asyncio.run(_test())
    except KeyboardInterrupt:
        pass
