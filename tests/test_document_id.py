from xteink_service.document_id import compute, _OFFSETS


def test_offsets_match_spec():
    """Offsets must be 256, 1024, 4096, ... per CrossPoint firmware source."""
    expected = [256, 1024, 4096, 16384, 65536, 262144, 1048576,
                4194304, 16777216, 67108864, 268435456, 1073741824]
    assert _OFFSETS == expected


def test_compute_empty_file():
    """Empty file produces MD5 of empty string."""
    import hashlib
    assert compute(b"") == hashlib.md5(b"").hexdigest()


def test_compute_small_file():
    """File smaller than first offset (256) produces MD5 of the first chunk."""
    import hashlib
    data = b"x" * 200   # 200 bytes < 256 (first offset skipped)
    # No offset is reachable, so hash is MD5 of empty
    assert compute(data) == hashlib.md5(b"").hexdigest()


def test_compute_uses_offset_256():
    """File >= 256 bytes includes the chunk at offset 256."""
    import hashlib
    data = b"A" * 2000
    m = hashlib.md5()
    m.update(data[256:256 + 1024])   # offset 256, chunk 1024
    m.update(data[1024:1024 + 1024]) # offset 1024, chunk 1024
    assert compute(data) == m.hexdigest()
