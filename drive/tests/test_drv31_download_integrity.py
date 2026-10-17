"""DRV-31: verify stored bytes before returning downloads."""
import hashlib
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from drive.models import Activity, File, FileType, StorageQuota

User = get_user_model()


@override_settings(MEDIA_ROOT='/tmp/drive-drv31-test-media')
class DownloadIntegrityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='nagi-drv31', email='nagi31@example.com', password='x')
        self.client.force_authenticate(user=self.user)
        StorageQuota.objects.get_or_create(owner_id=self.user.id, defaults={'quota_bytes': 10**9})
        p = patch('drive.views.process_file_upload.delay')
        self.addCleanup(p.stop)
        p.start()

    def tearDown(self):
        # Keep test media isolated and idempotent.
        root = '/tmp/drive-drv31-test-media'
        if os.path.isdir(root):
            for dirpath, _, filenames in os.walk(root, topdown=False):
                for filename in filenames:
                    os.remove(os.path.join(dirpath, filename))
                if dirpath != root:
                    os.rmdir(dirpath)

    def _create_file(self, content: bytes, checksum=True):
        digest = hashlib.sha256(content).hexdigest() if checksum else ''
        return File.objects.create(
            name='download.txt',
            owner_id=self.user.id,
            file=SimpleUploadedFile('download.txt', content, content_type='text/plain'),
            file_type=FileType.DOCUMENT,
            mime_type='text/plain',
            size=len(content),
            extension='txt',
            checksum_sha256=digest,
        )

    def test_corrupt_storage_returns_500_without_streaming_corrupt_bytes(self):
        original = b'expected bytes'
        file_obj = self._create_file(original)
        with open(file_obj.file.path, 'wb') as fh:
            fh.write(b'corrupt bytes that must not be streamed')

        resp = self.client.get(f'/api/files/{file_obj.id}/download/')

        self.assertEqual(resp.status_code, 500, resp.content)
        self.assertEqual(resp.data['error'], 'checksum_mismatch')
        self.assertIn('checksum', resp.data['message'])
        self.assertNotIn(b'corrupt bytes', resp.content)
        self.assertFalse(Activity.objects.filter(file=file_obj, action='download').exists())

    def test_legacy_file_without_checksum_downloads_with_unavailable_header(self):
        content = b'legacy bytes with no checksum'
        file_obj = self._create_file(content, checksum=False)

        resp = self.client.get(f'/api/files/{file_obj.id}/download/')

        self.assertEqual(resp.status_code, 200, getattr(resp, 'content', b''))
        self.assertEqual(resp['X-Checksum-Available'], 'false')
        self.assertEqual(resp['X-Drive-Integrity'], 'unverified-legacy')
        self.assertEqual(b''.join(resp.streaming_content), content)
        activity = Activity.objects.get(file=file_obj, action='download')
        self.assertEqual(activity.details['integrity_status'], 'unverified_legacy')

    def test_matching_checksum_downloads_with_verified_headers(self):
        content = b'verified bytes'
        digest = hashlib.sha256(content).hexdigest()
        file_obj = self._create_file(content)

        resp = self.client.get(f'/api/files/{file_obj.id}/download/')

        self.assertEqual(resp.status_code, 200, getattr(resp, 'content', b''))
        self.assertEqual(resp['X-Checksum-Available'], 'true')
        self.assertEqual(resp['X-Drive-Integrity'], 'verified')
        self.assertEqual(resp['X-Drive-Checksum-SHA256'], digest)
        self.assertEqual(b''.join(resp.streaming_content), content)
