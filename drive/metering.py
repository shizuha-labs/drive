"""
Drive metering federation — canonical storage-usage occurrence outbox.

PLAT-9234 (R2 slice 4; parent PLAT-7476): Drive emits
``io.shizuha.usage.measured.v1`` storage meters to the neutral ingest,
mirroring the scs#90 storage meter + cortex#211 transactional-outbox shape
(ADR-0032 D16 semantics, HLD §5):

- Each ``StorageQuota.used_bytes`` change writes ONE canonical occurrence
  into the outbox in the SAME transaction as the quota save, so a remote
  Metering outage can never silently drop usage.
- The occurrence is the ABSOLUTE per-owner gauge (``storage.bytes_current``,
  LATEST, byte) — not a delta — matching the scs#90 ``storage.bytes`` shape:
  idempotent and replay-safe by event_id.
- event_id ``drive:storage:<owner_id>:<used_bytes>:<updated_at-epoch>``:
  re-delivering the same occurrence is a no-op by identity; a changed
  payload for the same identity is a conflict (409 semantics), never a
  silent replace.
- Zero-suppression: folder_count-only updates (used_bytes unchanged) emit
  nothing.
- Payer mapping: ``owner_id`` is the billing subject directly (user-owned
  storage); org-shared storage does NOT change ownership — bytes stay
  billed to the file owner (DRV-181/PLAT-8918 org-share legs).
"""
import hashlib
import json
import logging
import uuid

from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)

# Canonical identities (HLD §3/§4).
SOURCE_DRIVE_STORAGE = 'urn:shizuha:drive:storage'
EVENT_TYPE_USAGE_MEASURED = 'io.shizuha.usage.measured.v1'
STORAGE_BYTES_CURRENT = 'storage.bytes_current'


class OccurrenceOutbox(models.Model):
    """Append-only canonical resource-occurrence outbox row.

    One row = one accepted resource occurrence: immutable
    (source, event_id) identity, canonical payload hash, server-derived
    billing subject, and delivery state.  Idempotency is replay-by-hash;
    a changed payload for the same identity is a conflict, never a rewrite.
    """

    class State(models.TextChoices):
        PENDING = 'pending', 'Pending'
        DELIVERED = 'delivered', 'Delivered'
        FAILED = 'failed', 'Failed'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(max_length=255)
    event_id = models.CharField(max_length=255)
    event_type = models.CharField(max_length=255)
    occurred_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    # Server-derived billing subject — NEVER a body-supplied value.
    subject = models.CharField(max_length=255)
    actor_user_id = models.CharField(max_length=255, blank=True)
    organization_id = models.CharField(max_length=255, blank=True)

    resource_type = models.CharField(max_length=64)
    resource_id = models.CharField(max_length=255)

    data = models.JSONField(default=dict)
    data_sha256 = models.CharField(max_length=64)

    state = models.CharField(
        max_length=16, choices=State.choices, default=State.PENDING, db_index=True
    )
    delivery_attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['source', 'event_id'],
                name='drive_occurrence_outbox_source_event_uniq',
            ),
        ]
        indexes = [
            models.Index(
                fields=['state', 'recorded_at'], name='drive_outbox_state_age_idx'
            ),
        ]

    @staticmethod
    def canonical_hash(data) -> str:
        canonical = json.dumps(data, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def __str__(self):
        return f'{self.source} {self.event_id} [{self.state}]'


class OccurrenceConflict(Exception):
    """Same (source, event_id) re-enqueued with a different canonical payload."""


def enqueue_occurrence(
    *,
    source: str,
    event_id: str,
    event_type: str,
    occurred_at,
    subject: str,
    resource_type: str,
    resource_id: str,
    data: dict,
    actor_user_id: str = '',
    organization_id: str = '',
):
    """Append one canonical resource occurrence, replay-safe by (source, id).

    - Same (source, event_id) + same canonical payload hash -> returns the
      existing row (successful replay, exactly one projection effect).
    - Same (source, event_id) + different hash -> raises OccurrenceConflict
      (409 idempotency_conflict); the original is never replaced.
    - New identity -> appends a PENDING row for at-least-once delivery.

    Callers MUST invoke this inside the same ``transaction.atomic`` block as
    the product mutation the occurrence describes (HLD §5).
    """
    if not source or not event_id:
        raise ValueError('source and event_id are required')
    payload_hash = OccurrenceOutbox.canonical_hash(data)

    existing = OccurrenceOutbox.objects.filter(source=source, event_id=event_id).first()
    if existing is not None:
        if existing.data_sha256 == payload_hash:
            return existing
        raise OccurrenceConflict(
            f'{source}/{event_id} already recorded with a different payload'
        )

    return OccurrenceOutbox.objects.create(
        source=source,
        event_id=event_id,
        event_type=event_type,
        occurred_at=occurred_at,
        subject=subject,
        resource_type=resource_type,
        resource_id=resource_id,
        data=data,
        data_sha256=payload_hash,
        actor_user_id=actor_user_id or '',
        organization_id=organization_id or '',
    )


def storage_usage_occurrence(quota, *, previous_used_bytes=None, occurred_at=None):
    """Build + enqueue the absolute per-owner storage gauge occurrence.

    Emitted when ``StorageQuota.used_bytes`` changes (pre_save/post_save
    value compare).  Absolute bytes (LATEST gauge), zero-delta suppressed,
    replay-safe by event_id.  Returns the outbox row, the existing replay
    row, or None when suppressed.
    """
    used = max(0, int(quota.used_bytes or 0))
    if previous_used_bytes is not None and int(previous_used_bytes) == used:
        return None  # zero-suppression: folder_count-only update

    occurred_at = occurred_at or timezone.now()
    epoch = int(occurred_at.timestamp())
    event_id = f'drive:storage:{quota.owner_id}:{used}:{epoch}'
    subject = f'user:{quota.owner_id}'

    data = {
        'billing_account_id': subject,
        'actor_user_id': str(quota.owner_id or ''),
        'resource': {'type': 'drive_storage', 'id': str(quota.owner_id)},
        'measurements': [
            {
                'meter': STORAGE_BYTES_CURRENT,
                'quantity': used,
                'unit': 'byte',
                'dimensions': {
                    'product': 'drive',
                    'storage_class': 'user_files',
                },
            },
        ],
        'request_id': f'drive:storage:{quota.owner_id}:{epoch}',
    }
    return enqueue_occurrence(
        source=SOURCE_DRIVE_STORAGE,
        event_id=event_id,
        event_type=EVENT_TYPE_USAGE_MEASURED,
        occurred_at=occurred_at,
        subject=subject,
        resource_type='drive_storage',
        resource_id=str(quota.owner_id),
        data=data,
        actor_user_id=str(quota.owner_id or ''),
    )
