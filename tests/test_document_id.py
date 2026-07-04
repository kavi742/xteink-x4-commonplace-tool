from xteink_service.document_id import compute, _OFFSETS


def test_offsets_match_spec():
    """First offset is 0 (getOffset(-1)=0), then 1024, 4096, ... per firmware source."""
    expected = [0, 1024, 4096, 16384, 65536, 262144, 1048576,
                4194304, 16777216, 67108864, 268435456, 1073741824]
    assert _OFFSETS == expected


def test_compute_empty_file():
    """Empty file produces MD5 of empty string."""
    import hashlib
    assert compute(b"") == hashlib.md5(b"").hexdigest()


def test_compute_reads_from_offset_zero():
    """First chunk is always read from byte 0."""
    import hashlib
    data = b"A" * 2000
    m = hashlib.md5()
    m.update(data[0:1024])    # offset 0
    m.update(data[1024:2048]) # offset 1024
    assert compute(data) == m.hexdigest()


def test_compute_uses_offset_4096():
    """File >= 4096 bytes includes third chunk."""
    import hashlib
    data = b"B" * 5120
    m = hashlib.md5()
    m.update(data[0:1024])
    m.update(data[1024:2048])
    m.update(data[4096:5120])
    assert compute(data) == m.hexdigest()
