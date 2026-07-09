import asyncio
import hashlib
import io
import logging
import re
from datetime import datetime

import aiohttp
import pytesseract
from PIL import Image, PngImagePlugin

from xteink_service.status_display import x4_status
from xteink_service.state import SyncState
from xteink_service.vault_writer import VaultWriter

logger = logging.getLogger(__name__)


class ScreenshotArchiver:
    """Downloads screenshots from the X4 and writes them to the Obsidian vault."""

    def __init__(self, vault_path: str, device_host: str = "crosspoint.local", state_db: str = "state.db"):
        self.vault_path = vault_path
        self.device_host = device_host
        self._state = SyncState(state_db)
        self._vault = VaultWriter(vault_path)

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

                # ponytail: stitch consecutive pages within one sync run only;
                # crossing sessions would mean rewriting already-written entries.
                groups = self._group_consecutive(screenshots)
                total = len(groups)
                book_counts: dict[str, int] = {}

                for gi, group in enumerate(groups, 1):
                    book, day = group[0][1], group[0][2]

                    # Download the not-yet-synced pages of this group
                    pages = []  # (idx, filepath, content, content_hash)
                    for idx, _book, _day, filepath in group:
                        if self._state.is_path_synced(filepath):
                            continue
                        content = await self._download_file(session, filepath)
                        content_hash = hashlib.sha256(content).hexdigest()
                        if self._state.is_synced(filepath, content_hash):
                            continue
                        # Stream bytes through the status WebSocket — fills progress bar accurately
                        await show(self._status_label(book, filepath, gi, total), data=content)
                        pages.append((idx, filepath, content, content_hash))

                    if not pages:
                        continue

                    # Convert + OCR each page; join OCR text across the run
                    png_pages, ocr_parts = [], []
                    for _idx, _fp, content, _hash in pages:
                        png = self._bmp_to_png(content)
                        text = self._ocr_image(png)
                        png_pages.append(png)
                        if text:
                            ocr_parts.append(text)

                    ocr_text = "\n\n".join(ocr_parts) or None
                    png_data = png_pages[0] if len(png_pages) == 1 else self._stitch_pngs(png_pages)
                    if ocr_text:
                        png_data = self._embed_ocr_in_png(png_data, ocr_text)

                    # Reuse the first unsynced page's listing slot as the filename index
                    write_index = min(idx for idx, *_ in pages)
                    embed = self._vault.write_screenshot(book, day, png_data, write_index)
                    self._vault.write_screenshot_meta(
                        embed, pages[0][1], pages[0][3], book, day.isoformat(), ocr_text
                    )
                    self._vault.append_to_daily_note(book, day, embed, ocr_text)
                    for _idx, filepath, _content, content_hash in pages:
                        self._state.mark_synced(
                            filepath, content_hash, book, day.isoformat(), ocr_text,
                            vault_png_path=embed,
                        )

                    book_counts[book] = book_counts.get(book, 0) + len(pages)
                    logger.info("Archived %s (%d page%s)  ocr=%s", embed, len(pages),
                                "" if len(pages) == 1 else "s",
                                f"{len(ocr_text)} chars" if ocr_text else "empty")

                # Summary — one line per book, then DONE
                summary = "  ".join(
                    f"{count} from {book[:12]}" for book, count in book_counts.items()
                ) + "  DONE"
                await show(summary)
                # ntfy notification
                if book_counts:
                    try:
                        import urllib.request, os
                        topic = os.getenv("NTFY_TOPIC", "")
                        if topic:
                            msg = "Synced: " + ", ".join(
                                f"{c} from {b}" for b, c in book_counts.items()
                            )
                            req = urllib.request.Request(
                                topic, data=msg.encode(),
                                headers={"Title": "xteink screenshot sync", "Content-Type": "text/plain"},
                                method="POST",
                            )
                            urllib.request.urlopen(req, timeout=5)
                    except Exception:
                        pass
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
    def _group_consecutive(screenshots, max_run: int = 6):
        """
        Collapse runs of consecutive pages into groups for stitching.

        A run is same book, same day, same chapter, and contiguous page numbers
        (from `_parse_filename`), capped at `max_run` pages. Screenshots whose
        filename has no chapter/page, or that break the sequence, stay as
        singleton groups — i.e. today's per-screenshot behaviour.

        `screenshots` is the (book, day, filepath) list from `_list_screenshots`.
        Returns a list of groups; each group is a list of (idx, book, day, filepath)
        where idx is the 1-based listing position (the vault filename slot).
        """
        indexed = [
            (i, book, day, filepath)
            for i, (book, day, filepath) in enumerate(screenshots, 1)
        ]

        def page_key(item):
            info = ScreenshotArchiver._parse_filename(item[3].rsplit("/", 1)[-1])
            return (item[1], item[2], info.get("chapter", -1), info.get("page", -1))

        groups, run, prev = [], [], None
        for idx, book, day, filepath in sorted(indexed, key=page_key):
            info = ScreenshotArchiver._parse_filename(filepath.rsplit("/", 1)[-1])
            cur = (book, day, info["chapter"], info["page"]) if info else None
            if (cur and run and prev
                    and cur[:3] == prev[:3] and cur[3] == prev[3] + 1
                    and len(run) < max_run):
                run.append((idx, book, day, filepath))
            else:
                if run:
                    groups.append(run)
                run = [(idx, book, day, filepath)]
            prev = cur
        if run:
            groups.append(run)
        return groups

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
    def _stitch_pngs(pngs: list[bytes]) -> bytes:
        """
        Vertically concatenate PNGs in reading order into one image.
        Width = widest page; narrower pages sit left-aligned on white. No scaling.
        """
        imgs = [Image.open(io.BytesIO(p)).convert("RGB") for p in pngs]
        width = max(im.width for im in imgs)
        height = sum(im.height for im in imgs)
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        y = 0
        for im in imgs:
            canvas.paste(im, (0, y))
            y += im.height
        out = io.BytesIO()
        canvas.save(out, format="PNG")
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
