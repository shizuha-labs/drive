"""DRV-105 change-feed / delta contract — ordered mutations + server cursor.

The change feed is the authoritative remote-change discovery surface for the
Drive sync engine (DRV-104 HLD §6/§9). It replaces the initial-snapshot-only
``updated_at`` listing with a monotonic, opaque, client-safe cursor over
ordered mutations (create/update/rename/move/trash/restore/delete), carrying
tombstones for deletes and a snapshot-fence bootstrap for fresh installs.

Contract:
- ``GET /api/changes?cursor=<opaque>`` → ``{changes, next_cursor, has_more}``
  in ascending mutation order; an empty delta returns the same cursor. A
  cursor that points before the compaction boundary (GC'd tombstones) returns
  ``410 Gone`` with ``cursor_expired: true`` so a lagging client re-bootstraps
  from a fresh fence instead of silently diverging.
- ``GET /api/changes/bootstrap`` → ``{fence_cursor, snapshot}`` — a fresh
  client acquires the fence (current feed head), lists the tree as-of that
  fence, then replays ``changes?cursor=<fence>`` strictly after it.
- Deletes are tombstones (payload carries last-known state) and GC after the
  retention window so clients converge and GC locally.
- Conditional writes return 412 on a stale generation (never a silent
  overwrite); conflict-artifact creation is idempotent per a stable conflict
  identity + client idempotency key.
"""
import base64
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import Change, ConflictArtifact, File, Folder

# Default change-feed page size.
CHANGES_PAGE_SIZE = 500
# Tombstone retention (delete mutations GC after this many days).
CHANGE_TOMBSTONE_RETENTION_DAYS = 30


def encode_cursor(change_id):
    """Encode a change id as an opaque, client-safe cursor."""
    return base64.urlsafe_b64encode(str(change_id).encode('ascii')).decode('ascii')


def decode_cursor(cursor):
    """Decode an opaque cursor to a change id; ``None`` if invalid.

    A missing/empty cursor means "from the beginning" (cursor 0). An invalid
    cursor decodes to ``None`` so the caller can surface a 400 rather than
    silently treating a corrupt cursor as the feed head.
    """
    if not cursor:
        return 0
    try:
        return int(base64.urlsafe_b64decode(cursor.encode('ascii')).decode('ascii'))
    except Exception:
        return None


def record_change(
    *,
    owner_id,
    resource_type,
    resource_id,
    mutation,
    generation=1,
    parent_id=None,
    name='',
    payload=None,
    tombstone=False,
):
    """Record one ordered mutation in the change feed.

    ``tombstone=True`` (delete mutations) sets an ``expires_at`` so the row is
    garbage-collected after the retention window while still letting sync
    clients converge on the deletion.
    """
    expires_at = None
    if tombstone:
        retention = getattr(
            settings,
            'DRIVE_CHANGE_TOMBSTONE_RETENTION_DAYS',
            CHANGE_TOMBSTONE_RETENTION_DAYS,
        )
        expires_at = timezone.now() + timedelta(days=retention)
    return Change.objects.create(
        owner_id=str(owner_id),
        resource_type=resource_type,
        resource_id=resource_id,
        mutation=mutation,
        generation=generation,
        parent_id=parent_id,
        name=name,
        payload=payload or {},
        expires_at=expires_at,
    )


def snapshot_file(file_obj):
    """Build a change-feed payload snapshot for a File."""
    return {
        'id': str(file_obj.id),
        'name': file_obj.name,
        'parent_id': str(file_obj.folder_id) if file_obj.folder_id else None,
        'file_type': file_obj.file_type,
        'mime_type': file_obj.mime_type,
        'size': file_obj.size,
        'version': file_obj.version,
        'is_trashed': file_obj.is_trashed,
        'updated_at': file_obj.updated_at.isoformat() if file_obj.updated_at else None,
    }


def snapshot_folder(folder_obj):
    """Build a change-feed payload snapshot for a Folder."""
    return {
        'id': str(folder_obj.id),
        'name': folder_obj.name,
        'parent_id': str(folder_obj.parent_id) if folder_obj.parent_id else None,
        'is_trashed': folder_obj.is_trashed,
        'updated_at': folder_obj.updated_at.isoformat() if folder_obj.updated_at else None,
    }


def get_changes(owner_id, cursor=0, limit=CHANGES_PAGE_SIZE):
    """Return ``(changes, next_cursor, has_more)`` for the owner's feed.

    ``changes`` is a list of Change rows with ``id > cursor`` in ascending
    order. ``next_cursor`` is the id of the last returned change (or the input
    cursor when the delta is empty). ``has_more`` is True when another page
    exists beyond the returned slice.
    """
    qs = (
        Change.objects
        .filter(owner_id=str(owner_id), id__gt=cursor)
        .order_by('id')
    )
    page = list(qs[:limit + 1])
    has_more = len(page) > limit
    page = page[:limit]
    next_cursor = page[-1].id if page else cursor
    return page, next_cursor, has_more


def get_fence(owner_id):
    """Return the current change-feed head (fence cursor) for the owner."""
    last = (
        Change.objects
        .filter(owner_id=str(owner_id))
        .order_by('-id')
        .first()
    )
    return last.id if last else 0


def get_compaction_boundary(owner_id):
    """Return the oldest retained change id for the owner (compaction boundary).

    Changes with ``id < boundary`` have been garbage-collected (expired
    tombstones removed by :func:`gc_expired_tombstones`). ``None`` when the
    owner has no changes at all.
    """
    oldest = (
        Change.objects
        .filter(owner_id=str(owner_id))
        .order_by('id')
        .first()
    )
    return oldest.id if oldest else None


def cursor_is_expired(owner_id, cursor):
    """True when ``cursor`` points before the compaction boundary.

    A cursor older than the oldest retained change id cannot reconstruct the
    delta — the intervening changes were GC'd — so a lagging client must
    re-bootstrap from a fresh fence rather than silently receive an empty
    delta. Cursor 0 ("from the beginning") is expired only when the owner's
    retained feed no longer starts at id 1 (GC already happened).
    """
    boundary = get_compaction_boundary(owner_id)
    if boundary is None:
        return False
    if cursor == 0:
        return boundary > 1
    return cursor < boundary


def bootstrap_snapshot(owner_id, fence=0):
    """Return the initial snapshot as-of the fence (files + folders).

    The caller must pair this with ``get_fence``: list the tree anchored to the
    fence, then replay ``changes?cursor=<fence>`` strictly after it so the
    snapshot and the replay are disjoint and complete (no gap, no overlap).

    Only resources whose create mutation has ``id <= fence`` are included; a
    resource created after the fence appears only in the replay (its create
    mutation), keeping the two surfaces disjoint.
    """
    # Map resource_id -> earliest create change id.
    created_at_id = {}
    for c in (
        Change.objects
        .filter(owner_id=str(owner_id), mutation='create')
        .order_by('id')
    ):
        created_at_id.setdefault(str(c.resource_id), c.id)

    files = File.objects.filter(owner_id=str(owner_id), is_trashed=False)
    folders = Folder.objects.filter(owner_id=str(owner_id), is_trashed=False)
    return {
        'files': [
            snapshot_file(f) for f in files
            if created_at_id.get(str(f.id), 0) <= fence
        ],
        'folders': [
            snapshot_folder(f) for f in folders
            if created_at_id.get(str(f.id), 0) <= fence
        ],
    }


def create_conflict_artifact(
    *,
    owner_id,
    resource_id,
    conflict_identity,
    idempotency_key,
    name,
    payload=None,
):
    """Idempotently create a conflict artifact.

    Two concurrent/retried conflict creates with the same conflict identity +
    idempotency key produce exactly one artifact with one stored
    ``first_detected_at``; the replay returns the existing artifact.
    Returns ``(artifact, created)``.
    """
    defaults = {
        'name': name,
        'payload': payload or {},
        'first_detected_at': timezone.now(),
    }
    artifact, created = ConflictArtifact.objects.get_or_create(
        owner_id=str(owner_id),
        resource_id=resource_id,
        conflict_identity=conflict_identity,
        idempotency_key=idempotency_key,
        defaults=defaults,
    )
    return artifact, created


def gc_expired_tombstones():
    """Delete change-feed tombstones past their retention window.

    Returns the number of rows deleted. Safe to run on a schedule; a client
    whose cursor lagged past compaction re-bootstraps from a fresh fence.
    """
    deleted, _ = (
        Change.objects
        .filter(expires_at__isnull=False, expires_at__lte=timezone.now())
        .delete()
    )
    return deleted
