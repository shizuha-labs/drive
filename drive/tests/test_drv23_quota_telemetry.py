"""DRV-23 — free external-user quota provisioning + usage telemetry."""
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from drive.models import StorageQuota
from drive.views import DriveTelemetryView


class _U:
    def __init__(self, uid='u1', is_staff=False):
        self.id = uid
        self.pk = uid
        self.is_staff = is_staff
        self.is_authenticated = True


class DRV23FreeQuotaTests(TestCase):
    def test_provision_free_is_5gb_and_idempotent(self):
        q, created = StorageQuota.provision_free('ext-user-1')
        self.assertTrue(created)
        self.assertEqual(q.quota_bytes, 5 * 1024 ** 3)        # 5 GiB free tier
        self.assertEqual(StorageQuota.FREE_EXTERNAL_QUOTA_BYTES, 5 * 1024 ** 3)
        q2, created2 = StorageQuota.provision_free('ext-user-1')
        self.assertFalse(created2)                             # idempotent
        self.assertEqual(q.id, q2.id)
        self.assertEqual(StorageQuota.objects.filter(owner_id='ext-user-1').count(), 1)

    def test_default_quota_already_free_tier(self):
        # External users auto-get a 5GB quota via get_or_create on first use.
        q, _ = StorageQuota.objects.get_or_create(owner_id='ext-user-2')
        self.assertEqual(q.quota_bytes, 5 * 1024 ** 3)


class DRV23TelemetryTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        GB = 1024 ** 3
        StorageQuota.objects.create(owner_id='a', quota_bytes=5 * GB, used_bytes=0)          # idle
        StorageQuota.objects.create(owner_id='b', quota_bytes=5 * GB, used_bytes=1 * GB)      # active
        StorageQuota.objects.create(owner_id='c', quota_bytes=5 * GB, used_bytes=5 * GB)      # at quota

    def _get(self, user=None, **params):
        req = self.factory.get('/api/drive/internal/telemetry/', params)
        if user is not None:
            force_authenticate(req, user=user)
        return DriveTelemetryView.as_view()(req)

    def test_non_staff_and_anonymous_forbidden(self):
        self.assertEqual(self._get().status_code, 401)              # anon: not authenticated
        self.assertEqual(self._get(_U(is_staff=False)).status_code, 403)  # authed non-staff

    def test_staff_aggregates(self):
        m = self._get(_U(is_staff=True)).data
        GB = 1024 ** 3
        self.assertEqual(m['total_users'], 3)
        self.assertEqual(m['active_users'], 2)        # b + c have used_bytes > 0
        self.assertEqual(m['users_at_quota'], 1)      # c is at quota
        self.assertEqual(m['total_used_bytes'], 6 * GB)
        self.assertEqual(m['total_quota_bytes'], 15 * GB)
        self.assertEqual(m['storage_pressure_pct'], 40.0)   # 6/15
        self.assertEqual(m['free_tier_quota_bytes'], 5 * GB)

    def test_prometheus_export(self):
        resp = self._get(_U(is_staff=True), export='prometheus')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp['Content-Type'].startswith('text/plain'))
        body = resp.content.decode()
        self.assertIn('drive_active_users 2', body)
        self.assertIn('drive_users_at_quota 1', body)
        self.assertIn('drive_storage_pressure_pct 40.0', body)
