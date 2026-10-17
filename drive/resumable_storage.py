"""Shared staging storage for resumable uploads.

Production Drive replicas use S3/MinIO for file storage.  Resumable chunks must
use that same shared backend: ``MEDIA_ROOT`` is intentionally empty in S3 mode,
so treating it as a local staging directory makes uploads depend on whichever
pod received the session-creation request.

The ``storage:`` marker distinguishes new shared sessions from legacy local
``temp_path`` rows so in-flight/local-development uploads remain compatible.
"""
from __future__ import annotations

import os
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage


STORAGE_MARKER = "storage:"


def create_staging_path(owner_id: object, upload_id: str) -> str:
    owner = str(owner_id).replace("/", "_")
    return f"{STORAGE_MARKER}resumable_uploads/{owner}/{upload_id}"


def is_shared_staging_path(temp_path: str) -> bool:
    return bool(temp_path and temp_path.startswith(STORAGE_MARKER))


def _prefix(temp_path: str) -> str:
    if not is_shared_staging_path(temp_path):
        raise ValueError("Not a shared resumable-upload staging path")
    return temp_path[len(STORAGE_MARKER):].strip("/")


def _chunk_path(temp_path: str, offset: int) -> str:
    return f"{_prefix(temp_path)}/{offset:020d}.part"


def write_chunk(temp_path: str, offset: int, body: bytes) -> None:
    if is_shared_staging_path(temp_path):
        path = _chunk_path(temp_path, offset)
        if default_storage.exists(path):
            default_storage.delete(path)
        saved_path = default_storage.save(path, ContentFile(body))
        if saved_path != path:
            default_storage.delete(saved_path)
            raise OSError(f"Storage could not preserve resumable chunk path {path}")
        return
    with open(temp_path, "ab") as stream:
        stream.write(body)


def _shared_chunks(temp_path: str) -> list[tuple[int, str, int]]:
    prefix = _prefix(temp_path)
    try:
        _, names = default_storage.listdir(prefix)
    except (FileNotFoundError, OSError):
        return []
    chunks = []
    for name in names:
        if not name.endswith(".part"):
            continue
        try:
            offset = int(name[:-5])
        except ValueError:
            continue
        path = f"{prefix}/{name}"
        chunks.append((offset, path, default_storage.size(path)))
    return sorted(chunks)


def iter_staged_blocks(temp_path: str, block_size: int = 1024 * 1024):
    if not is_shared_staging_path(temp_path):
        with open(temp_path, "rb") as stream:
            for block in iter(lambda: stream.read(block_size), b""):
                yield block
        return

    expected_offset = 0
    for offset, path, size in _shared_chunks(temp_path):
        if offset != expected_offset:
            raise OSError(
                f"Resumable staging has a gap at byte {expected_offset}; next chunk starts at {offset}"
            )
        read_size = 0
        with default_storage.open(path, "rb") as stream:
            for block in iter(lambda: stream.read(block_size), b""):
                read_size += len(block)
                yield block
        if read_size != size:
            raise OSError(f"Resumable chunk size changed while reading {path}")
        expected_offset += size


def read_staged_range(temp_path: str, start: int, size: int) -> bytes:
    if size <= 0:
        return b""
    if not is_shared_staging_path(temp_path):
        with open(temp_path, "rb") as stream:
            stream.seek(start)
            return stream.read(size)

    end = start + size
    output = bytearray()
    for offset, path, chunk_size in _shared_chunks(temp_path):
        chunk_end = offset + chunk_size
        if chunk_end <= start:
            continue
        if offset >= end:
            break
        read_start = max(start, offset) - offset
        read_end = min(end, chunk_end) - offset
        with default_storage.open(path, "rb") as stream:
            if read_start:
                stream.seek(read_start)
            output.extend(stream.read(read_end - read_start))
    return bytes(output)


def delete_staging(temp_path: str) -> int:
    """Delete staged bytes and return the number of physical objects/files."""
    if not temp_path:
        return 0
    if is_shared_staging_path(temp_path):
        deleted = 0
        for _, path, _ in _shared_chunks(temp_path):
            default_storage.delete(path)
            deleted += 1
        return deleted
    if os.path.exists(temp_path):
        os.remove(temp_path)
        return 1
    return 0


def staging_exists(temp_path: str) -> bool:
    if is_shared_staging_path(temp_path):
        return bool(_shared_chunks(temp_path))
    return bool(temp_path and Path(temp_path).exists())
