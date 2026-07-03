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

        async def show(message: str) -> None:
            try:
                await ws.send(f"START:{message}:1:/")
                if await ws.recv() == "READY":
                    await ws.send(b"X")
                    await ws.recv()  # drain DONE / PROGRESS:1:1
            except Exception:
                pass

        yield show
    except Exception as e:
        logger.warning("Could not connect to X4 status display: %s", e)

        async def _noop(_: str) -> None:
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
            logger.info("Sent: %r — check X4 screen", _msg)
            await asyncio.sleep(5)  # keep visible

    try:
        asyncio.run(_test())
    except KeyboardInterrupt:
        pass
