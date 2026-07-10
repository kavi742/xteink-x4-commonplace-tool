"""
Run one sync cycle: connect to the X4, download all screenshots, write vault,
then resolve any unresolved KOReader hashes while the device is still reachable.
Usage:
  docker run ... python -m xteink_service.sync_once <host> <vault_path>
  or via env: DEVICE_HOST=... VAULT_PATH=... python -m xteink_service.sync_once
"""
import asyncio
import logging
import os
import sys

from xteink_service.archiver import ScreenshotArchiver

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def main(host: str, vault: str, state_db: str) -> None:
    archiver = ScreenshotArchiver(vault, host, state_db)
    await archiver.run_sync()

    # Proactively map every epub filename → title while the device is reachable
    # (File Transfer mode, port 80 open). This ensures the first KOReader sync
    # for any book writes to the vault immediately — no lag waiting for a second
    # File Transfer session.
    koreader_db = os.getenv("KOREADER_DB", "/data/state/koreader.db")
    try:
        from xteink_service.alias import _preload_all_aliases, _scan_resolve
        await _preload_all_aliases(state_db, host)   # map all epubs on device
        await _scan_resolve(state_db, koreader_db, host)  # also resolve any pending hashes
    except Exception as exc:
        logger.debug("Alias scan skipped: %s", exc)


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else os.getenv("DEVICE_HOST", "crosspoint.local")
    vault = sys.argv[2] if len(sys.argv) > 2 else os.getenv("VAULT_PATH", "/data/vault")
    state_db = sys.argv[3] if len(sys.argv) > 3 else os.getenv("STATE_DB", "/data/state/state.db")
    try:
        asyncio.run(main(host, vault, state_db))
    except KeyboardInterrupt:
        pass
