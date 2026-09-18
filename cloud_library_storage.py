"""Lossless cloud-only encoding. No record deletion, pruning, or local migration.

Readers accept legacy JSON and a checksummed envelope. Encoding is opt-in until
all clients have been upgraded; an already compressed document stays compressed.
The limits bound memory and leave provider headroom, rather than silently evicting
research evidence. Local StrategyStore files always remain ordinary JSON.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import zlib

MIB = 1024 * 1024
HARD_LIMIT_BYTES = 100 * MIB
WRITE_LIMIT_BYTES = 95 * MIB
MAX_LOGICAL_BYTES = 512 * MIB
MIN_COMPRESS_BYTES = MIB
WORK_RESERVE_BYTES = 5 * MIB
MARKER = '_trading_lab_storage'
FORMAT = 'zlib+base64+json-v1'


class CloudStorageError(ValueError):
    """Secret-safe capacity or integrity failure; never discard evidence."""


def compression_enabled() -> bool:
    return os.environ.get('TRADING_LAB_COMPRESSED_CLOUD_STORAGE', '') == '1'


def check_write_size(size: int, *, reserve_bytes: int = 0) -> None:
    if size + reserve_bytes > WRITE_LIMIT_BYTES:
        raise CloudStorageError(
            f'Cloud research storage [size_limit]: {size} bytes plus '
            f'{reserve_bytes} bytes reserve exceeds the {WRITE_LIMIT_BYTES}-byte '
            f'safe write budget (GitHub hard limit {HARD_LIMIT_BYTES}). '
            'No upload or evidence deletion was attempted. Upgrade all readers '
            'before enabling lossless compression, or migrate to larger storage; '
            'repeated retries cannot resolve capacity exhaustion.'
        )


def encode_library(raw: bytes, *, compress: bool = False) -> bytes:
    if len(raw) > MAX_LOGICAL_BYTES:
        raise CloudStorageError('Cloud research storage [size_limit]: expanded library exceeds the 512 MiB restore bound; evidence must be archived to larger storage, never pruned.')
    # Keep the input bytes exactly, including key order, numeric spellings and
    # Unicode escapes: both full-library and embedded provenance hashes survive.
    result = raw
    if compress and len(raw) >= MIN_COMPRESS_BYTES:
        packed = zlib.compress(raw, level=6)
        envelope = {MARKER: {
            'format': FORMAT, 'sha256': hashlib.sha256(raw).hexdigest(),
            'uncompressed_bytes': len(raw), 'compressed_bytes': len(packed),
            'payload': base64.b64encode(packed).decode('ascii'),
        }}
        candidate = json.dumps(envelope, separators=(',', ':')).encode('utf-8')
        if len(candidate) < len(raw):
            result = candidate
    check_write_size(len(result))
    return result


def decode_library(stored: bytes) -> tuple[bytes, bool]:
    if len(stored) > HARD_LIMIT_BYTES:
        raise CloudStorageError('Cloud library exceeds the bounded download size.')
    try:
        value = json.loads(stored)
        if not isinstance(value, dict):
            raise CloudStorageError('Cloud library must contain a JSON object.')
        if MARKER not in value:
            return stored, False
        if set(value) != {MARKER}:
            raise CloudStorageError('Ambiguous cloud storage envelope; refusing partial evidence.')
        archive = value[MARKER]
        if not isinstance(archive, dict) or archive.get('format') != FORMAT:
            raise CloudStorageError('Unsupported cloud storage format; upgrade the reader.')
        size = archive.get('uncompressed_bytes')
        packed_size = archive.get('compressed_bytes')
        if type(size) is not int or not 0 < size <= MAX_LOGICAL_BYTES:
            raise CloudStorageError('Cloud archive expanded size is outside the restore bound.')
        if type(packed_size) is not int or not 0 < packed_size <= HARD_LIMIT_BYTES:
            raise CloudStorageError('Cloud archive compressed size is outside the restore bound.')
        packed = base64.b64decode(archive['payload'], validate=True)
        if len(packed) != packed_size:
            raise CloudStorageError('Cloud archive compressed-size verification failed.')
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(packed, size + 1)
        if (len(raw) != size or not decompressor.eof or decompressor.unused_data
                or decompressor.unconsumed_tail):
            raise CloudStorageError('Cloud archive expanded-size verification failed.')
        if hashlib.sha256(raw).hexdigest() != archive.get('sha256'):
            raise CloudStorageError('Cloud archive SHA-256 verification failed.')
        decoded = json.loads(raw)
        if not isinstance(decoded, dict) or MARKER in decoded:
            raise CloudStorageError('Invalid or nested cloud library envelope.')
        return raw, True
    except CloudStorageError:
        raise
    except (KeyError, TypeError, ValueError, UnicodeError, zlib.error) as exc:
        raise CloudStorageError('Cloud library decoding or integrity verification failed.') from exc


def storage_preflight(raw: bytes, *, compress: bool = False) -> dict:
    encoded = encode_library(raw, compress=compress)
    check_write_size(len(encoded), reserve_bytes=WORK_RESERVE_BYTES)
    return {'logical_bytes': len(raw), 'stored_bytes': len(encoded),
            'hard_limit_bytes': HARD_LIMIT_BYTES, 'write_limit_bytes': WRITE_LIMIT_BYTES,
            'headroom_bytes': WRITE_LIMIT_BYTES - len(encoded),
            'work_reserve_bytes': WORK_RESERVE_BYTES, 'compression': encoded != raw,
            'retention_policy': 'preserve_all_records'}
