"""
DRV-6: quota enforcement at upload — block over-limit with a structured error,
warn (non-blocking) when usage crosses the threshold.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from drive.models import StorageQuota, File

User = get_user_model()
UPLOAD_URL = '/api/files/'


class QuotaEnforcementTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='ryo-drv6', email='r@x.com', password='x')
        self.client.force_authenticate(user=self.user)
        self.quota, _ = StorageQuota.objects.get_or_create(owner_id=self.user.id)
        # Isolate quota logic from the async post-upload task (no Celery broker).
        _p = patch('drive.views.process_file_upload.delay')
        self.addCleanup(_p.stop)
        _p.start()

    def _set_quota(self, quota_bytes, used_bytes=0):
        self.quota.quota_bytes = quota_bytes
        self.quota.used_bytes = used_bytes
        self.quota.save(update_fields=['quota_bytes', 'used_bytes'])

    def _upload(self, content):
        f = SimpleUploadedFile('t.txt', content, content_type='text/plain')
        return self.client.post(UPLOAD_URL, {'file': f}, format='multipart')

    def test_over_quota_blocked_with_structured_error(self):
        self._set_quota(quota_bytes=5)
        resp = self._upload(b'x' * 100)
        self.assertEqual(resp.status_code, 413, resp.content)
        self.assertEqual(resp.data['error'], 'quota_exceeded')
        self.assertIn('request_id', resp.data)
        err = resp.data['details']
        self.assertEqual(err['requested_bytes'], 100)
        self.assertEqual(err['quota_bytes'], 5)
        self.assertIn('available_bytes', err)
        self.assertEqual(File.objects.count(), 0)

    @override_settings(DRIVE_QUOTA_WARN_THRESHOLD_PERCENT=90)
    def test_under_quota_uploads_clean_no_warning(self):
        self._set_quota(quota_bytes=10 ** 9)
        resp = self._upload(b'hello world')
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertNotIn('quota_warning', resp.data)

    @override_settings(DRIVE_QUOTA_WARN_THRESHOLD_PERCENT=90)
    def test_upload_near_threshold_warns(self):
        self._set_quota(quota_bytes=100, used_bytes=85)
        resp = self._upload(b'hello')  # +5 -> 90 used = 90%
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertIn('quota_warning', resp.data)
        self.assertEqual(resp.data['quota_warning']['code'], 'quota_threshold')
        self.assertGreaterEqual(resp.data['quota_warning']['usage_percent'], 90)
