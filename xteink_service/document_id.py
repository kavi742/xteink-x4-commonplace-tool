"""
KOReader / CrossPoint document ID — partial MD5 hash.

Algorithm (from CrossPoint firmware source KOReaderDocumentId.cpp):
  getOffset(i): returns 0 for i < 0, else 1024 << (2*i)
  Loop: for i = -1 to 10 (12 offsets total):
    0, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304,
    16777216, 67108864, 268435456, 1073741824
  Skip offset if >= file size. Read min(1024, remaining) bytes. MD5 all chunks.
"""
import hashlib

# getOffset(i): 0 for i=-1, else 1024 << (2*i) for i=0..10
_OFFSETS: list[int] = [0] + [1024 << (2 * i) for i in range(11)]
_CHUNK = 1024


def compute(data: bytes) -> str:
    """Return the KOReader partial-MD5 document ID for the given file bytes."""
    m = hashlib.md5()
    for offset in _OFFSETS:
        if offset >= len(data):
            continue
        m.update(data[offset : offset + _CHUNK])
    return m.hexdigest()


def min_bytes_needed(file_size: int) -> int:
    """Return how many bytes must be downloaded to compute the hash."""
    for offset in reversed(_OFFSETS):
        if offset < file_size:
            return min(offset + _CHUNK, file_size)
    return min(_CHUNK, file_size)
