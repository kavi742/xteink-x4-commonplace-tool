import asyncio
import io
import logging
from datetime import datetime

import aiohttp
import pytesseract
from PIL import Image

from xteink_service.status_display import x4_status

logger = logging.getLogger(__name__)


class ScreenshotArchiver:
    """Downloads screenshots from the X4 and writes them to the Obsidian vault."""

    def __init__(self, vault_path: str, device_host: str = "crosspoint.local", state_db: str = "state.db"):
        self.vault_path = vault_path
        self.device_host = device_host
        # ponytail: state and vault wired in later phases
        self._state_db = state_db

    async def run_sync(self) -> None:
        """Sync all screenshots from the device, showing progress on its screen."""
        async with aiohttp.ClientSession() as session:
            async with x4_status(self.device_host) as show:
                await show("Syncing screenshots...")

                screenshots = await self._list_screenshots(session)
                if not screenshots:
                    await show("No new screenshots")
                    await asyncio.sleep(2)
                    return

                total = len(screenshots)
                for idx, (book, day, filepath) in enumerate(screenshots, 1):
                    await show(f"Screenshot {idx}/{total} \u2014 {book[:20]}")

                    content = await self._download_file(session, filepath)
                    png_data = self._bmp_to_png(content)
                    ocr_text = self._ocr_image(png_data)

                    # ponytail: state dedup + vault write wired in Phase 5/7
                    logger.info(
                        "Downloaded %s  ocr=%s",
                        filepath,
                        f"{len(ocr_text)} chars" if ocr_text else "empty",
                    )

                await show(f"\u2705 {total} screenshot(s) ready")
                await asyncio.sleep(3)

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

    @staticmethod
    def _ocr_image(png_data: bytes) -> str | None:
        """
        Extract text from a PNG via Tesseract.
        Returns None (and logs a warning) if Tesseract is unavailable or fails.
        Empty output after stripping is also treated as None.
        """
        try:
            img = Image.open(io.BytesIO(png_data))
            text = pytesseract.image_to_string(img).strip()
            return text or None
        except Exception as exc:
            logger.warning("OCR failed, skipping text extraction: %s", exc)
            return None
