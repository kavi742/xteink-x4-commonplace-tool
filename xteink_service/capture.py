"""
Capture one screenshot from the X4, save PNG + OCR text to /output/.
Run via: docker run ... python -m xteink_service.capture <host>
"""
import asyncio
import os
import sys

import aiohttp

from xteink_service.archiver import ScreenshotArchiver
from xteink_service.status_display import x4_status


async def main(host: str) -> None:
    a = ScreenshotArchiver("/vault", host)
    timeout = aiohttp.ClientTimeout(total=10)

    print(f"Connecting to {host}...")
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            shots = await a._list_screenshots(s)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        print(f"Cannot reach {host}: {e}")
        print("Is the X4 in File Transfer mode?")
        sys.exit(1)

    if not shots:
        print("No screenshots found on device — is it in File Transfer mode?")
        sys.exit(1)

    book, day, path = shots[0]
    print(f"Screenshot : {path}")
    print(f"Book       : {book}  |  Day: {day}")

    async with x4_status(host) as show:
        label = a._status_label(book, path, 1, 1)
        await show(label)

        async with aiohttp.ClientSession(timeout=timeout) as s:
            bmp = await a._download_file(s, path)

        png = a._bmp_to_png(bmp)
        ocr_text = a._ocr_image(png)
        if ocr_text:
            png = a._embed_ocr_in_png(png, ocr_text)

        with open("/output/sample_screenshot.png", "wb") as f:
            f.write(png)
        print("Saved     : /output/sample_screenshot.png")

        text = ocr_text or ""
        with open("/output/sample_ocr.txt", "w") as f:
            f.write(text)
        print("Saved     : /output/sample_ocr.txt")
        if ocr_text:
            print("            (OCR text also embedded in PNG iTXt metadata)")

        await show(f"1 from {book[:12]}  DONE")
        await asyncio.sleep(5)  # brief hold so message is visible; production sync uses 30s

    print("\n--- OCR output ---")
    print(text if text else "(empty — blank page or no recognisable text)")


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "crosspoint.local"
    try:
        asyncio.run(main(host))
    except KeyboardInterrupt:
        pass
