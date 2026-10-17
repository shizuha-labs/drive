"""DRV-10: chunked/resumable upload API."""
import hashlib
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from drive.models import File, ResumableUpload, StorageQuota

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='drive-drv10-'), ALLOWED_FILE_TYPES=['text/plain'])
class ResumableUploadTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='drv10', email='drv10@example.com', password='x')
        self.client.force_authenticate(user=self.user)
        self.quota, _ = StorageQuota.objects.get_or_create(owner_id=self.user.id)
        self.quota.quota_bytes = 1024 * 1024
        self.quota.save(update_fields=['quota_bytes'])
        p = patch('drive.views.process_file_upload.delay')
        self.addCleanup(p.stop)
        p.start()

    def _create_session(self, content):
        return self.client.post('/api/uploads/resumable/', {
            'name': 'big.txt',
            'size': len(content),
            'mime_type': 'text/plain',
            'checksum_sha256': hashlib.sha256(content).hexdigest(),
        }, format='json')

    def test_chunk_resume_and_complete_creates_file(self):
        content = b'hello ' + b'x' * 1024 + b' world'
        create = self._create_session(content)
        self.assertEqual(create.status_code, 201, create.content)
        upload_id = create.data['id']

        first = content[:500]
        second = content[500:]
        r1 = self.client.put(f'/api/uploads/resumable/{upload_id}/chunk/', {
            'offset': 0,
            'chunk': SimpleUploadedFile('part1', first, content_type='application/octet-stream'),
        }, format='multipart')
        self.assertEqual(r1.status_code, 200, r1.content)
        self.assertEqual(r1.data['uploaded_bytes'], len(first))
        status = self.client.get(f'/api/uploads/resumable/{upload_id}/')
        self.assertEqual(status.data['uploaded_bytes'], len(first))

        r2 = self.client.put(f'/api/uploads/resumable/{upload_id}/chunk/', {
            'offset': len(first),
            'chunk': SimpleUploadedFile('part2', second, content_type='application/octet-stream'),
        }, format='multipart')
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertTrue(r2.data['complete'])

        done = self.client.post(f'/api/uploads/resumable/{upload_id}/complete/')
        self.assertEqual(done.status_code, 201, done.content)
        f = File.objects.get(id=done.data['id'])
        self.assertEqual(f.size, len(content))
        self.assertEqual(f.checksum_sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(f.file.read(), content)
        session = ResumableUpload.objects.get(id=upload_id)
        self.assertEqual(session.status, ResumableUpload.Status.COMPLETED)

    def test_offset_mismatch_is_resume_signal(self):
        content = b'abcdef'
        upload_id = self._create_session(content).data['id']
        resp = self.client.put(f'/api/uploads/resumable/{upload_id}/chunk/', {
            'offset': 3,
            'chunk': SimpleUploadedFile('part', b'abc', content_type='application/octet-stream'),
        }, format='multipart')
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.data['expected_offset'], 0)

    def test_quota_checked_before_session(self):
        self.quota.quota_bytes = 5
        self.quota.save(update_fields=['quota_bytes'])
        resp = self._create_session(b'x' * 100)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Storage quota exceeded', str(resp.data))


    def test_complete_returns_created_when_async_enqueue_fails(self):
        content = b'resumable completion should not depend on celery'
        create = self._create_session(content)
        self.assertEqual(create.status_code, 201, create.content)
        upload_id = create.data['id']
        chunk = self.client.put(f'/api/uploads/resumable/{upload_id}/chunk/', {
            'offset': 0,
            'chunk': SimpleUploadedFile('all', content, content_type='application/octet-stream'),
        }, format='multipart')
        self.assertEqual(chunk.status_code, 200, chunk.content)

        with patch('drive.views.process_file_upload.delay', side_effect=RuntimeError('broker unavailable')):
            done = self.client.post(f'/api/uploads/resumable/{upload_id}/complete/')

        self.assertEqual(done.status_code, 201, done.content)
        self.assertTrue(File.objects.filter(id=done.data['id']).exists())
        session = ResumableUpload.objects.get(id=upload_id)
        self.assertEqual(session.status, ResumableUpload.Status.COMPLETED)
