"""
Run one sync cycle: connect to the X4, download all screenshots, write vault.
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


async def main(host: str, vault: str, state_db: str) -> None:
    archiver = ScreenshotArchiver(vault, host, state_db)
    await archiver.run_sync()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else os.getenv("DEVICE_HOST", "crosspoint.local")
    vault = sys.argv[2] if len(sys.argv) > 2 else os.getenv("VAULT_PATH", "/data/vault")
    state_db = sys.argv[3] if len(sys.argv) > 3 else os.getenv("STATE_DB", "/tmp/state.db")
    try:
        asyncio.run(main(host, vault, state_db))
    except KeyboardInterrupt:
        pass
