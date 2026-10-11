from siterag.domain.hashing import make_chunk_id, make_chunks_hash, make_url_id, sha1_hex


def test_url_id_is_sha1_prefix() -> None:
    assert make_url_id("https://x.co.il/") == sha1_hex("https://x.co.il/")[:16]


def test_chunk_id_deterministic_and_format() -> None:
    a = make_chunk_id("u" * 16, ["תביעות", "נזקי מים"], 2)
    b = make_chunk_id("u" * 16, ["תביעות", "נזקי מים"], 2)
    assert a == b
    url_id, heading_hash, ordinal = a.split(":")
    assert url_id == "u" * 16 and len(heading_hash) == 8 and ordinal == "2"
    assert make_chunk_id("u" * 16, ["תביעות"], 2) != a


def test_chunks_hash_depends_on_ids_and_texts() -> None:
    base = make_chunks_hash([("c1", "a"), ("c2", "b")])
    assert base == make_chunks_hash([("c1", "a"), ("c2", "b")])
    assert base != make_chunks_hash([("c1", "a"), ("c2", "B")])
