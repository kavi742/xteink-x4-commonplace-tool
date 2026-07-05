"""
xteink-service entry point.
Runs the device watcher loop and KOReader sync server concurrently.

Usage:
    python -m xteink_service          # via __main__.py
    docker-compose up                 # Docker (CMD in Dockerfile)
"""
import asyncio
import logging
import os

import uvicorn

from xteink_service.archiver import ScreenshotArchiver
from xteink_service.koreader_sync import app as koreader_app
from xteink_service.watcher import poll_for_device, wait_for_offline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)
logger = logging.getLogger(__name__)


async def watcher_loop(host: str, vault: str, state_db: str) -> None:
    """Poll for device → sync screenshots + aliases → wait for offline → repeat."""
    koreader_db = os.getenv("KOREADER_DB", "/data/state/koreader.db")

    while True:
        logger.info("Waiting for X4 to enter File Transfer mode...")
        await poll_for_device(host)

        logger.info("X4 online — starting sync")
        archiver = ScreenshotArchiver(vault, host, state_db)
        await archiver.run_sync()

        # Resolve any new KOReader hashes while device is reachable
        try:
            from xteink_service.alias import _scan_resolve
            await _scan_resolve(state_db, koreader_db, host)
        except Exception as exc:
            logger.debug("Alias scan skipped: %s", exc)

        logger.info("Sync complete — waiting for X4 to go offline")
        await wait_for_offline(host)


async def main() -> None:
    host      = os.getenv("DEVICE_HOST", "crosspoint.local")
    vault     = os.getenv("VAULT_PATH",  "/data/vault")
    state_db  = os.getenv("STATE_DB",    "/data/state/state.db")
    port      = int(os.getenv("PORT", "8090"))

    logger.info("Starting xteink-service")
    logger.info("  device   : %s", host)
    logger.info("  vault    : %s", vault)
    logger.info("  state db : %s", state_db)
    logger.info("  port     : %d", port)

    config = uvicorn.Config(
        koreader_app,
        host="0.0.0.0",
        port=port,
        log_level="info",
    )
    server = uvicorn.Server(config)

    # Run KOReader sync server + device watcher loop concurrently.
    # If either task raises, the other is cancelled and the process exits.
    await asyncio.gather(
        server.serve(),
        watcher_loop(host, vault, state_db),
    )
