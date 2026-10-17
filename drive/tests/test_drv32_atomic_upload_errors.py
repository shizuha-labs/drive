"""DRV-32: upload/write failures return structured errors and leave no zombies."""
import os
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from drive.models import File, StorageQuota

User = get_user_model()
UPLOAD_URL = '/api/files/'
MEDIA_ROOT = '/tmp/drive-drv32-test-media'


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class AtomicUploadErrorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='nagi-drv32', email='nagi32@example.com', password='x')
        self.client.force_authenticate(user=self.user)
        self.quota, _ = StorageQuota.objects.get_or_create(owner_id=self.user.id)
        self.quota.quota_bytes = 10**9
        self.quota.save(update_fields=['quota_bytes'])
        p = patch('drive.views.process_file_upload.delay')
        self.addCleanup(p.stop)
        p.start()

    def tearDown(self):
        if os.path.isdir(MEDIA_ROOT):
            for dirpath, _, filenames in os.walk(MEDIA_ROOT, topdown=False):
                for filename in filenames:
                    os.remove(os.path.join(dirpath, filename))
                if dirpath != MEDIA_ROOT:
                    os.rmdir(dirpath)

    def _upload(self, content=b'atomic bytes'):
        return self.client.post(
            UPLOAD_URL,
            {'file': SimpleUploadedFile('atomic.txt', content, content_type='text/plain')},
            format='multipart',
        )

    def _stored_files(self):
        found = []
        if os.path.isdir(MEDIA_ROOT):
            for dirpath, _, filenames in os.walk(MEDIA_ROOT):
                for filename in filenames:
                    found.append(os.path.join(dirpath, filename))
        return found

    def assert_upload_error_shape(self, resp, code):
        self.assertEqual(resp.data['error'], code)
        self.assertIsInstance(resp.data['message'], str)
        uuid.UUID(resp.data['request_id'])

    def test_storage_failure_creates_no_file_row_and_no_storage_object(self):
        with patch('django.core.files.storage.filesystem.FileSystemStorage.save', side_effect=OSError('disk full')):
            resp = self._upload()

        self.assertEqual(resp.status_code, 500, resp.content)
        self.assert_upload_error_shape(resp, 'upload_failed')
        self.assertEqual(File.objects.count(), 0)
        self.assertEqual(self._stored_files(), [])

    def test_db_failure_after_storage_write_cleans_up_storage_object(self):
        with patch('django.db.models.base.Model.save_base', side_effect=DatabaseError('db down')):
            resp = self._upload()

        self.assertEqual(resp.status_code, 500, resp.content)
        self.assert_upload_error_shape(resp, 'upload_failed')
        self.assertEqual(File.objects.count(), 0)
        self.assertEqual(self._stored_files(), [])

    def test_quota_error_uses_structured_shape_and_no_file(self):
        self.quota.quota_bytes = 1
        self.quota.save(update_fields=['quota_bytes'])

        resp = self._upload(b'too large')

        self.assertEqual(resp.status_code, 413, resp.content)
        self.assert_upload_error_shape(resp, 'quota_exceeded')
        self.assertEqual(resp.data['details']['requested_bytes'], len(b'too large'))
        self.assertEqual(File.objects.count(), 0)
