"""DRV-112 — Dashboard storage accounting must be coherent with live files.

Regression: a populated account whose stored ``StorageQuota.used_bytes`` has
drifted below the live non-trashed File bytes must not render a Dashboard whose
Storage Overview (used_human) understates the visible Storage-by-File-Type
totals. The dashboard derives the Drive-owned usage from the SAME accounting
boundary as ``file_stats`` (the live File rows), so:

  - ``quota.used_bytes`` >= sum of ``file_stats[].total_size``
  - ``quota.file_count`` == sum of ``file_stats[].count``
  - ``quota.by_service.drive`` agrees with the Drive-owned active-file count/bytes
"""
from types import SimpleNamespace

from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from drive.models import File, FileType, StorageQuota
from drive.views import DashboardView


def _user(uid):
    return SimpleNamespace(
        id=uid, pk=uid, is_authenticated=True, is_superuser=False,
        is_staff=True,  # staff bypasses org-membership explicit-scope checks
        organization_id=None, organization_ids=[], email=f'{uid}@example.com',
        organization_memberships={}, organization_role=None,
    )


def _create_file(owner_id, *, size, file_type=FileType.DOCUMENT, name='doc.txt'):
    return File.objects.create(
        owner_id=owner_id,
        name=name,
        mime_type='text/plain',
        size=size,
        file_type=file_type,
    )


class DashboardQuotaCoherenceTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def _dashboard(self, user):
        request = self.factory.get('/shizuha-drive/api/dashboard/')
        force_authenticate(request, user=user)
        return DashboardView.as_view()(request)

    def test_dashboard_derives_used_bytes_from_live_files_not_stale_quota(self):
        """DRV-112 repro: stale stored quota (1280) vs live files (1436)."""
        owner = 'zen-drv112'
        # Stale stored quota that understates the live files (the bug state).
        StorageQuota.objects.create(
            owner_id=owner, used_bytes=1280, file_count=33, folder_count=83,
        )
        # Live files: 31 documents totalling 1436 bytes (30×46 + 1×56).
        for i in range(30):
            _create_file(owner, size=46, name=f'doc-{i}.txt')
        _create_file(owner, size=56, name='doc-30.txt')

        resp = self._dashboard(_user(owner))
        self.assertEqual(resp.status_code, 200)

        quota = resp.data['quota']
        file_stats = resp.data['file_stats']

        # The overview must derive from the live files, not the stale stored row.
        self.assertEqual(quota['used_bytes'], 1436)
        self.assertEqual(quota['file_count'], 31)
        self.assertEqual(quota['used_human'], '1.4 KB')

        # Sum of displayed file-type bytes cannot exceed displayed used bytes.
        stats_total = sum(stat['total_size'] for stat in file_stats)
        stats_count = sum(stat['count'] for stat in file_stats)
        self.assertLessEqual(stats_total, quota['used_bytes'])
        self.assertEqual(stats_total, 1436)
        self.assertEqual(stats_count, quota['file_count'])

        # by_service.drive agrees with the Drive-owned active-file count/bytes.
        drive = quota['by_service']['drive']
        self.assertEqual(drive['used_bytes'], 1436)
        self.assertEqual(drive['file_count'], 31)

    def test_dashboard_empty_account_returns_zero_coherently(self):
        owner = 'empty-drv112'
        StorageQuota.objects.create(owner_id=owner, used_bytes=0, file_count=0)

        resp = self._dashboard(_user(owner))
        self.assertEqual(resp.status_code, 200)
        quota = resp.data['quota']
        self.assertEqual(quota['used_bytes'], 0)
        self.assertEqual(quota['file_count'], 0)
        self.assertEqual(resp.data['file_stats'], [])

    def test_dashboard_excludes_trashed_files_from_usage(self):
        owner = 'trash-drv112'
        StorageQuota.objects.create(owner_id=owner, used_bytes=0, file_count=0)
        _create_file(owner, size=500, name='live.txt')
        trashed = _create_file(owner, size=900, name='trashed.txt')
        trashed.is_trashed = True
        trashed.save(update_fields=['is_trashed'])

        resp = self._dashboard(_user(owner))
        self.assertEqual(resp.status_code, 200)
        quota = resp.data['quota']
        # Only the live file counts; the trashed file is excluded.
        self.assertEqual(quota['used_bytes'], 500)
        self.assertEqual(quota['file_count'], 1)
        self.assertEqual(resp.data['file_stats'][0]['total_size'], 500)
