"""Lossless, versioned backup encoding; local libraries remain ordinary JSON.

Readers always accept legacy JSON. Compressed writes require an explicit rollout
setting, so installing compatible readers cannot migrate production storage.
An old JSON-only reader fails to parse the binary header instead of mistaking a
manifest for an empty library. Git CAS always uses the encoded blob's identity.
"""
from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path
import struct
import zlib

MAGIC = b"TILJSON1"
HEADER = struct.Struct(">8sQ32s")
GIT_BLOB_LIMIT = 100 * 1024 * 1024
MAX_JSON_BYTES = 512 * 1024 * 1024
COMPRESSION_THRESHOLD = 1024 * 1024


class LibraryEncodingError(ValueError):
    """Unsupported/corrupt/oversized storage; never discard the original work."""


def encode_library_bytes(raw: bytes, *, storage_format: str | None = None) -> bytes:
    mode = storage_format if storage_format is not None else os.environ.get(
        "GITHUB_BACKUP_STORAGE_FORMAT", "json")
    if mode not in {"json", "gzip-v1"}:
        raise LibraryEncodingError("Unsupported GITHUB_BACKUP_STORAGE_FORMAT; backup retained locally.")
    if len(raw) > MAX_JSON_BYTES:
        raise LibraryEncodingError("Library exceeds the verified 512 MiB decoded-size bound; recovery retained.")
    encoded = raw
    if mode == "gzip-v1" and len(raw) >= COMPRESSION_THRESHOLD:
        encoded = HEADER.pack(MAGIC, len(raw), hashlib.sha256(raw).digest()) + gzip.compress(
            raw, compresslevel=6, mtime=0)
    if len(encoded) > GIT_BLOB_LIMIT:
        raise LibraryEncodingError("Encoded backup exceeds GitHub's 100 MiB blob limit; recovery retained. No retry can fix capacity.")
    return encoded


def decode_library_bytes(encoded: bytes) -> bytes:
    if not encoded.startswith(MAGIC):
        if encoded.startswith(b"TILJSON"):
            raise LibraryEncodingError("Unsupported cloud-library encoding version.")
        if len(encoded) > MAX_JSON_BYTES:
            raise LibraryEncodingError("Legacy library exceeds the decoded-size bound.")
        return encoded
    if len(encoded) < HEADER.size:
        raise LibraryEncodingError("Truncated cloud-library header.")
    _, size, expected_hash = HEADER.unpack_from(encoded)
    if size > MAX_JSON_BYTES:
        raise LibraryEncodingError("Compressed library exceeds the decoded-size bound.")
    try:
        decoder = zlib.decompressobj(wbits=31)
        raw = decoder.decompress(encoded[HEADER.size:], size + 1)
    except zlib.error as exc:
        raise LibraryEncodingError("Damaged compressed cloud library.") from exc
    if (len(raw) != size or not decoder.eof or decoder.unconsumed_tail
            or decoder.unused_data or hashlib.sha256(raw).digest() != expected_hash):
        raise LibraryEncodingError("Cloud-library length, checksum, or stream integrity check failed.")
    return raw


def read_library_file(path: str | Path) -> str:
    """For read-only audits of a Git-cloned backup, never write the clone."""
    return decode_library_bytes(Path(path).read_bytes()).decode("utf-8")
