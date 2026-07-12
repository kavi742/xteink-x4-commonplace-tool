import asyncio
import contextlib
import logging

import aiohttp

logger = logging.getLogger(__name__)


@contextlib.asynccontextmanager
async def x4_status(host: str):
    """
    Yield a ``show(message, data=None)`` status callable.

    Historically this streamed ``START:<msg>:<size>:/`` frames to the X4's
    port-81 WebSocket to show progress on the e-ink screen. That "protocol" is
    actually the device's *file-upload* channel: every status message persisted
    as a junk file at the device root (0-byte files like "Resolving titles",
    "Mapping 207 book(s)", "9 from Fifteen-Dogs  DONE"). We now log status
    server-side instead — visible in ``docker compose logs`` — and never write
    to the device, so no junk files are created. The ``host`` and ``data``
    parameters are accepted for backwards compatibility and ignored.
    """
    async def show(message: str, data: bytes | None = None) -> None:
        logger.info("X4 status: %s", message)

    yield show


async def _delete_device_file(
    session: aiohttp.ClientSession, host: str, path: str
) -> bool:
    """Delete one file on the device. Returns True on success.

    The Crosspoint file API only documents GET, so try the conventional REST
    delete shape; a 404/405 simply means the firmware doesn't support it.
    """
    try:
        async with session.delete(
            f"http://{host}/api/files",
            params={"path": path},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            return resp.status in (200, 204)
    except Exception:
        return False


async def cleanup_device_junk(host: str, show=None) -> int:
    """Remove leftover 0-byte status-message files at the device root.

    Earlier versions streamed status text as ``START:<msg>:0:/``, which the
    device's file-upload protocol saved as 0-byte files at ``/``. This deletes
    them. Only 0-byte, non-directory entries **directly under the root** are
    touched — book folders and their contents are never deleted. Returns the
    number of files removed.
    """
    removed = 0
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.get(
                f"http://{host}/api/files", params={"path": ""}
            ) as resp:
                if resp.status != 200:
                    return 0
                items = await resp.json()
            junk = [
                it for it in items
                if not it.get("isDirectory") and int(it.get("size", 0) or 0) == 0
            ]
            for it in junk:
                if await _delete_device_file(session, host, f"/{it['name']}"):
                    removed += 1
                    logger.info("Removed device junk file: %s", it["name"])
    except Exception as exc:
        logger.debug("device junk cleanup skipped: %s", exc)
    if removed and show:
        await show(f"Removed {removed} junk file(s)")
    return removed


if __name__ == "__main__":
    # Live test: python xteink_service/status_display.py [host] [message]
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _host = sys.argv[1] if len(sys.argv) > 1 else "crosspoint.local"
    _msg = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else "Hello from xteink-service"

    async def _test() -> None:
        async with x4_status(_host) as show:
            await show(_msg)

    try:
        asyncio.run(_test())
    except KeyboardInterrupt:
        pass
