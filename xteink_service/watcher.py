import asyncio
import logging

import aiohttp

logger = logging.getLogger(__name__)


async def poll_for_device(host: str = "crosspoint.local", interval: int = 5) -> str:
    """Poll /api/status until the X4 responds, then return host."""
    logger.info("Watching for X4 at %s (poll every %ds)...", host, interval)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                timeout = aiohttp.ClientTimeout(total=3)
                async with session.get(
                    f"http://{host}/api/status", timeout=timeout
                ) as resp:
                    if resp.status == 200:
                        logger.info("X4 online at %s", host)
                        return host
            except Exception:
                pass
            await asyncio.sleep(interval)


async def wait_for_offline(host: str, interval: int = 5) -> None:
    """Block until the X4 stops responding, so the next poll cycle is a fresh trigger."""
    logger.debug("Waiting for X4 at %s to go offline...", host)
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                timeout = aiohttp.ClientTimeout(total=3)
                async with session.get(
                    f"http://{host}/api/status", timeout=timeout
                ) as resp:
                    if resp.status != 200:
                        logger.info("X4 offline (status %d)", resp.status)
                        return
            except Exception:
                logger.info("X4 offline (unreachable)")
                return
            await asyncio.sleep(interval)
