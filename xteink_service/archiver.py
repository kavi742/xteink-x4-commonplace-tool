import asyncio
import io
import logging
from datetime import datetime

import aiohttp
from PIL import Image

logger = logging.getLogger(__name__)


class ScreenshotArchiver:
    """Downloads screenshots from the X4 and writes them to the Obsidian vault."""

    def __init__(self, vault_path: str, device_host: str = "crosspoint.local", state_db: str = "state.db"):
        self.vault_path = vault_path
        self.device_host = device_host
        # ponytail: state and vault wired in later phases
        self._state_db = state_db

    async def run_sync(self) -> None:
        raise NotImplementedError("Phase 4 in progress")

    # ------------------------------------------------------------------ #
    # Data fetching                                                        #
    # ------------------------------------------------------------------ #

    async def _list_screenshots(self, session: aiohttp.ClientSession) -> list[tuple[str, object, str]]:
        """
        Return (book, day, filepath) for every BMP under /screenshots/.
        Does NOT download — dedup check happens before download in run_sync.
        """
        base = f"http://{self.device_host}/api/files"

        async with session.get(base, params={"path": "/screenshots"}) as resp:
            items = await resp.json()

        results = []
        for item in items:
            if not item["isDirectory"]:
                continue
            book = item["name"]

            async with session.get(base, params={"path": f"/screenshots/{book}"}) as resp:
                files = await resp.json()

            for f in files:
                if f["isDirectory"] or not f["name"].endswith(".bmp"):
                    continue
                filepath = f"/screenshots/{book}/{f['name']}"
                mtime = f.get("mtime", datetime.now().timestamp())
                day = datetime.fromtimestamp(mtime).date()
                results.append((book, day, filepath))

        logger.debug("Listed %d screenshot(s) across %d book(s)", len(results),
                     len({r[0] for r in results}))
        return results

    async def _download_file(self, session: aiohttp.ClientSession, path: str) -> bytes:
        async with session.get(
            f"http://{self.device_host}/download", params={"path": path}
        ) as resp:
            return await resp.read()

    @staticmethod
    def _bmp_to_png(bmp_data: bytes) -> bytes:
        img = Image.open(io.BytesIO(bmp_data))
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
