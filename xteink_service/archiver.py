import asyncio
import io
import logging
import re
from datetime import datetime

import aiohttp
import pytesseract
from PIL import Image, PngImagePlugin

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

                try:
                    screenshots = await self._list_screenshots(session)
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning("Cannot reach X4 at %s: %s", self.device_host, e)
                    await show("X4 not reachable")
                    return

                if not screenshots:
                    await show("No new screenshots")
                    await asyncio.sleep(5)
                    return

                total = len(screenshots)
                book_counts: dict[str, int] = {}

                for idx, (book, day, filepath) in enumerate(screenshots, 1):
                    label = self._status_label(book, filepath, idx, total)
                    await show(label)

                    content = await self._download_file(session, filepath)
                    png_data = self._bmp_to_png(content)
                    ocr_text = self._ocr_image(png_data)
                    if ocr_text:
                        png_data = self._embed_ocr_in_png(png_data, ocr_text)

                    book_counts[book] = book_counts.get(book, 0) + 1

                    # ponytail: state dedup + vault write wired in Phase 5/7
                    logger.info("Downloaded %s  ocr=%s", filepath,
                                f"{len(ocr_text)} chars" if ocr_text else "empty")

                # Summary — one line per book, then DONE
                summary = "  ".join(
                    f"{count} from {book[:12]}" for book, count in book_counts.items()
                ) + "  DONE"
                await show(summary)
                await asyncio.sleep(30)  # hold until user disconnects

    # ------------------------------------------------------------------ #
    # Data fetching                                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_filename(name: str) -> dict:
        """
        Extract chapter/page from a Crosspoint screenshot filename.
        Example: Pastoral_ch8_p25_20pct_480360.bmp -> {chapter: 8, page: 25}
        Returns empty dict if the pattern doesn't match.
        """
        m = re.search(r'_ch(\d+)_p(\d+)', name)
        if not m:
            return {}
        return {"chapter": int(m.group(1)), "page": int(m.group(2))}

    @staticmethod
    def _status_label(book: str, filepath: str, idx: int, total: int) -> str:
        """Build a concise status message for the X4 screen."""
        filename = filepath.rsplit("/", 1)[-1]
        info = ScreenshotArchiver._parse_filename(filename)
        if info:
            return f"[{idx}/{total}] ch{info['chapter']} p{info['page']} {book[:14]}"
        return f"[{idx}/{total}] {book[:20]}"

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

    @staticmethod
    def _embed_ocr_in_png(png_data: bytes, ocr_text: str) -> bytes:
        """Embed OCR text as an iTXt metadata chunk in the PNG bytes."""
        img = Image.open(io.BytesIO(png_data))
        info = PngImagePlugin.PngInfo()
        info.add_itxt("ocr_text", ocr_text)
        out = io.BytesIO()
        img.save(out, format="PNG", pnginfo=info)
        return out.getvalue()
