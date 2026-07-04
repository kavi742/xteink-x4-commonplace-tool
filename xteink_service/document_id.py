"""
KOReader / CrossPoint document ID — partial MD5 hash.

Algorithm (from CrossPoint firmware source KOReaderDocumentId.cpp):
  Read 1024 bytes at each of these offsets (skip if beyond EOF):
  256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304,
  16777216, 67108864, 268435456, 1073741824
  MD5 of the concatenation of all retrieved chunks.
"""
import hashlib

# Offsets: 1024 << (2*i) for i = -1 to 10
_OFFSETS: list[int] = [1024 << (2 * i) if i >= 0 else 1024 >> 2 for i in range(-1, 11)]
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
