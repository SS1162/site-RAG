"""Hash helpers: content hashes, URL ids and deterministic chunk ids."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable


def sha256_hex(data: bytes | str) -> str:
    """Return the SHA-256 hex digest of bytes or a UTF-8 string."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha1_hex(data: bytes | str) -> str:
    """Return the SHA-1 hex digest of bytes or a UTF-8 string."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha1(data).hexdigest()


def make_url_id(normalized_url: str) -> str:
    """Return the 16-hex-char id of an already normalized URL."""
    return sha1_hex(normalized_url)[:16]


def make_chunk_id(url_id: str, heading_path: list[str], ordinal: int) -> str:
    """Return a deterministic chunk id: ``{url_id}:{sha1(heading_path_json)[:8]}:{ordinal}``."""
    heading_path_joined = json.dumps(heading_path, ensure_ascii=False)
    return f"{url_id}:{sha1_hex(heading_path_joined)[:8]}:{ordinal}"


def make_chunks_hash(chunks: Iterable[tuple[str, str]]) -> str:
    """Return the hash of a page's chunk set, given ``(chunk_id, text)`` pairs in order."""
    payload = json.dumps([[chunk_id, text] for chunk_id, text in chunks], ensure_ascii=False)
    return sha256_hex(payload)
