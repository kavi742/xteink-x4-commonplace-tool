"""
Browse the device's book library during File Transfer mode,
compute the CrossPoint partial-MD5 hash for each EPUB found,
and print the results.

Usage inside Docker:
  python -m xteink_service.hash_books <host> [known_hash ...]

With known_hash arguments the script marks matches with MATCH.
"""
import asyncio
import hashlib
import sys

import aiohttp

from xteink_service.document_id import compute


def _koreader_lua_hash(data: bytes) -> str:
    """KOReader Lua util.getFileHashPart — pos=0, step=4096, step*=4 each iteration."""
    m = hashlib.md5()
    pos, step = 0, 4096
    while pos < len(data):
        m.update(data[pos : pos + 1024])
        pos += step
        step *= 4
    return m.hexdigest()


async def list_books(session: aiohttp.ClientSession, host: str) -> list[dict]:
    """Return all EPUB/PDF file entries found anywhere under / on the device."""
    books: list[dict] = []
    seen: set[str] = set()

    async def walk(path: str, depth: int = 0) -> None:
        if depth > 2 or path in seen:
            return
        seen.add(path)
        try:
            async with session.get(
                f"http://{host}/api/files",
                params={"path": path},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return
                items = await resp.json()
        except Exception as e:
            print(f"  warn: listing {path!r} failed: {e}")
            return

        for item in items:
            if item.get("isDirectory"):
                await walk(f"{path}/{item['name']}", depth + 1)
            else:
                if item["name"].lower().endswith((".epub", ".pdf", ".mobi", ".azw3")):
                    books.append({"name": item["name"], "path": f"{path}/{item['name']}"})

    await walk("")
    return books


async def main(host: str, known_hashes: set[str]) -> None:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        print(f"Browsing {host} for book files...")
        books = await list_books(session, host)

        if not books:
            print("No book files found. Is the device in File Transfer mode?")
            sys.exit(1)

        print(f"Found {len(books)} book(s). Downloading to compute hashes...\n")

        for book in books:
            print(f"  {book['path']}")
            try:
                async with session.get(
                    f"http://{host}/download",
                    params={"path": book["path"]},
                ) as resp:
                    data = await resp.read()
                content_hash = compute(data)
                lua_hash = _koreader_lua_hash(data)
                filename = book["path"].split("/")[-1]
                filename_hash = hashlib.md5(filename.encode()).hexdigest()
                path_hash = hashlib.md5(book["path"].encode()).hexdigest()

                for label, h in [
                    ("content-hash (CrossPoint spec)", content_hash),
                    ("content-hash (KOReader Lua)   ", lua_hash),
                    ("filename-md5                  ", filename_hash),
                    ("path-md5                      ", path_hash),
                ]:
                    match = "  <-- MATCH" if h in known_hashes else ""
                    print(f"    {label}: {h}{match}")
                print(f"    size                          : {len(data):,} bytes")
            except Exception as e:
                print(f"    error: {e}")
            print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m xteink_service.hash_books <host> [hash ...]")
        sys.exit(1)
    _host = sys.argv[1]
    _known = set(sys.argv[2:])
    try:
        asyncio.run(main(_host, _known))
    except KeyboardInterrupt:
        pass
