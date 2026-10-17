"""PLAT-9234: Drive storage-usage metering — occurrence, outbox, delivery.

Regression contract for the R2 slice 4 emitter: the absolute per-owner
storage gauge (``storage.bytes_current``, LATEST, byte) is enqueued into a
transactional outbox on every ``StorageQuota.used_bytes`` change, with
idempotent event_id, zero-delta suppression, and replay-safe enqueue.
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_tz
from unittest import mock

from django.test import TestCase

from drive.metering import (
    OccurrenceConflict,
    OccurrenceOutbox,
    SOURCE_DRIVE_STORAGE,
    STORAGE_BYTES_CURRENT,
    enqueue_occurrence,
    storage_usage_occurrence,
)


class _Quota:
    """Minimal StorageQuota stand-in (no DB needed for builder tests)."""

    def __init__(self, owner_id='user-123', used_bytes=1024):
        self.owner_id = owner_id
        self.used_bytes = used_bytes


class StorageUsageOccurrenceTests(TestCase):
    def test_emits_absolute_gauge_occurrence(self):
        row = storage_usage_occurrence(_Quota(used_bytes=4096))
        self.assertIsNotNone(row)
        self.assertEqual(row.source, SOURCE_DRIVE_STORAGE)
        self.assertEqual(row.event_type, 'io.shizuha.usage.measured.v1')
        self.assertTrue(row.event_id.startswith('drive:storage:user-123:4096:'))
        self.assertEqual(row.subject, 'user:user-123')
        m = row.data['measurements'][0]
        self.assertEqual(m['meter'], STORAGE_BYTES_CURRENT)
        self.assertEqual(m['quantity'], 4096)
        self.assertEqual(m['unit'], 'byte')

    def test_zero_delta_suppressed(self):
        self.assertIsNone(
            storage_usage_occurrence(_Quota(used_bytes=1024), previous_used_bytes=1024)
        )
        self.assertEqual(OccurrenceOutbox.objects.count(), 0)

    def test_changed_bytes_emit(self):
        row = storage_usage_occurrence(_Quota(used_bytes=2048), previous_used_bytes=1024)
        self.assertIsNotNone(row)
        self.assertIn(':2048:', row.event_id)

    def test_negative_bytes_clamped(self):
        row = storage_usage_occurrence(_Quota(used_bytes=-5))
        self.assertEqual(row.data['measurements'][0]['quantity'], 0)

    def test_event_id_deterministic_per_state(self):
        at = datetime(2026, 9, 19, 3, 0, 0, tzinfo=dt_tz.utc)
        a = storage_usage_occurrence(_Quota(used_bytes=4096), occurred_at=at)
        b = storage_usage_occurrence(_Quota(used_bytes=4096), occurred_at=at)
        self.assertEqual(a.event_id, b.event_id)
        # Same identity + same payload -> replay success (same row returned).
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(OccurrenceOutbox.objects.count(), 1)

    def test_payer_is_owner_not_org_share(self):
        # Org-shared storage does NOT change ownership: the subject is the
        # file owner directly (DRV-181/PLAT-8918 org-share legs).
        row = storage_usage_occurrence(_Quota(owner_id='owner-9', used_bytes=10))
        self.assertEqual(row.subject, 'user:owner-9')


class EnqueueReplaySafetyTests(TestCase):
    def test_new_identity_appends_pending(self):
        row = enqueue_occurrence(
            source=SOURCE_DRIVE_STORAGE,
            event_id='drive:storage:u1:1:1',
            event_type='io.shizuha.usage.measured.v1',
            occurred_at=datetime.now(dt_tz.utc),
            subject='user:u1',
            resource_type='drive_storage',
            resource_id='u1',
            data={'measurements': [{'meter': 'm', 'unit': 'byte', 'quantity': 1}]},
        )
        self.assertEqual(row.state, OccurrenceOutbox.State.PENDING)

    def test_same_identity_same_hash_replays(self):
        kw = dict(
            source=SOURCE_DRIVE_STORAGE,
            event_id='drive:storage:u1:1:1',
            event_type='io.shizuha.usage.measured.v1',
            occurred_at=datetime.now(dt_tz.utc),
            subject='user:u1',
            resource_type='drive_storage',
            resource_id='u1',
        )
        data = {'measurements': [{'meter': 'm', 'unit': 'byte', 'quantity': 1}]}
        first = enqueue_occurrence(**kw, data=data)
        second = enqueue_occurrence(**kw, data=dict(data))
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OccurrenceOutbox.objects.count(), 1)

    def test_same_identity_different_hash_conflicts(self):
        kw = dict(
            source=SOURCE_DRIVE_STORAGE,
            event_id='drive:storage:u1:1:1',
            event_type='io.shizuha.usage.measured.v1',
            occurred_at=datetime.now(dt_tz.utc),
            subject='user:u1',
            resource_type='drive_storage',
            resource_id='u1',
        )
        enqueue_occurrence(**kw, data={'measurements': [{'meter': 'm', 'unit': 'byte', 'quantity': 1}]})
        with self.assertRaises(OccurrenceConflict):
            enqueue_occurrence(**kw, data={'measurements': [{'meter': 'm', 'unit': 'byte', 'quantity': 2}]})

    def test_missing_identity_raises(self):
        with self.assertRaises(ValueError):
            enqueue_occurrence(
                source='', event_id='x', event_type='t',
                occurred_at=datetime.now(dt_tz.utc), subject='s',
                resource_type='r', resource_id='1', data={},
            )


class DeliveryTests(TestCase):
    def _pending_row(self):
        return storage_usage_occurrence(_Quota(used_bytes=777))

    def test_deliver_marks_delivered_on_accept(self):
        from drive import metering_delivery
        self._pending_row()
        stats = metering_delivery.deliver_pending(
            post=lambda *a, **k: _FakeResponse({'results': [{'status': 'accepted'}]}))
        self.assertEqual(stats['delivered'], 1)
        self.assertEqual(OccurrenceOutbox.objects.filter(
            state=OccurrenceOutbox.State.DELIVERED).count(), 1)

    def test_transport_outage_defers_rows(self):
        from drive import metering_delivery
        self._pending_row()
        stats = metering_delivery.deliver_pending(
            post=lambda *a, **k: (_ for _ in ()).throw(
                metering_delivery.MeteringCoreUnavailable('down')))
        self.assertEqual(stats['unavailable'], 1)
        # Rows stay retryable (FAILED-with-error per the scs shape; the next
        # quota change re-emits the absolute gauge regardless).
        self.assertEqual(OccurrenceOutbox.objects.filter(
            state=OccurrenceOutbox.State.PENDING).count() +
            OccurrenceOutbox.objects.filter(
                state=OccurrenceOutbox.State.FAILED).count(), 1)

    def test_unconfigured_core_is_unavailable_not_crash(self):
        from drive import metering_delivery
        self._pending_row()
        with mock.patch.dict('os.environ', {}, clear=True):
            stats = metering_delivery.deliver_pending()
        self.assertEqual(stats['unavailable'], 1)

    def test_envelope_is_deterministic(self):
        from drive import metering_delivery
        row = self._pending_row()
        e1 = metering_delivery.build_envelope(row)
        e2 = metering_delivery.build_envelope(row)
        self.assertEqual(e1, e2)
        self.assertEqual(e1['specversion'], '1.0')
        self.assertEqual(e1['id'], row.event_id)
        self.assertEqual(e1['data']['meter'], STORAGE_BYTES_CURRENT)


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload
