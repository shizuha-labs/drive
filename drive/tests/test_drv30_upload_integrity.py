"""DRV-30: direct upload SHA-256 integrity metadata and mismatch rejection."""
import hashlib
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from rest_framework.test import APIClient

from drive.models import File, ResumableUpload, StorageQuota
from drive.serializers import FileUploadSerializer, UploadReadFailed

User = get_user_model()
UPLOAD_URL = '/api/files/'


class UploadIntegrityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='nagi-drv30', email='nagi@example.com', password='x')
        self.client.force_authenticate(user=self.user)
        StorageQuota.objects.get_or_create(owner_id=self.user.id, defaults={'quota_bytes': 10**9})
        p = patch('drive.views.process_file_upload.delay')
        self.addCleanup(p.stop)
        p.start()

    def _upload(self, content, expected_sha256=None):
        data = {'file': SimpleUploadedFile('integrity.txt', content, content_type='text/plain')}
        if expected_sha256 is not None:
            data['expected_sha256'] = expected_sha256
        return self.client.post(UPLOAD_URL, data, format='multipart')

    def test_successful_upload_stores_and_returns_canonical_sha256_metadata(self):
        content = b'shizuha drive integrity bytes'
        digest = hashlib.sha256(content).hexdigest()

        resp = self._upload(content, expected_sha256=digest)

        self.assertEqual(resp.status_code, 201, resp.content)
        file_obj = File.objects.get(owner_id=self.user.id)
        self.assertEqual(file_obj.checksum, digest)
        self.assertEqual(file_obj.checksum_sha256, digest)
        self.assertEqual(file_obj.checksum_algorithm, 'sha256')
        self.assertEqual(resp.data['checksum'], digest)
        self.assertEqual(resp.data['checksum_algorithm'], 'sha256')

    def test_checksum_mismatch_returns_409_and_creates_no_file(self):
        resp = self._upload(b'actual bytes', expected_sha256='0' * 64)

        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(resp.data['error']['code'], 'checksum_mismatch')
        self.assertEqual(resp.data['error']['details']['expected_sha256'], '0' * 64)
        self.assertEqual(File.objects.count(), 0)

    def test_invalid_expected_checksum_format_returns_400_and_creates_no_file(self):
        resp = self._upload(b'actual bytes', expected_sha256='NOT-A-SHA')

        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertEqual(resp.data['error']['code'], 'invalid_checksum_format')
        self.assertEqual(File.objects.count(), 0)

    def test_stream_error_before_commit_creates_no_file(self):
        class TruncatedUpload(SimpleUploadedFile):
            def chunks(self, chunk_size=None):
                yield b'prefix'
                raise OSError('truncated stream')

        upload = TruncatedUpload('broken.txt', b'prefix-lost', content_type='text/plain')
        request = RequestFactory().post(UPLOAD_URL)
        request.user = self.user
        serializer = FileUploadSerializer(data={'file': upload}, context={'request': request})
        self.assertTrue(serializer.is_valid(), serializer.errors)

        with self.assertRaises(UploadReadFailed) as raised:
            serializer.save()
        self.assertEqual(raised.exception.detail['error']['code'], 'upload_read_failed')
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(File.objects.count(), 0)


class UploadChecksumMd5StabilityTests(TestCase):
    """PLAT-4252: MD5 is non-crypto fingerprint; SHA-256 remains integrity authority.

    - usedforsecurity=False preserves the historical MD5 compatibility fingerprint
    - expected_sha256 mismatch still rejects (SHA-256 enforcement)
    - successful upload stores both checksum_md5 and checksum_sha256
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='plat4252-md5', email='plat4252@example.com', password='x'
        )
        self.client.force_authenticate(user=self.user)
        StorageQuota.objects.get_or_create(owner_id=self.user.id, defaults={'quota_bytes': 10**9})
        p = patch('drive.views.process_file_upload.delay')
        self.addCleanup(p.stop)
        p.start()

    def test_md5_hex_stable_with_usedforsecurity_false(self):
        content = b'plat-4252 md5 stability payload'
        # Reference digest without the flag (historical on-disk format)
        expected_md5 = hashlib.md5(content, usedforsecurity=False).hexdigest()

        digest = hashlib.sha256(content).hexdigest()
        resp = self.client.post(
            UPLOAD_URL,
            {
                'file': SimpleUploadedFile('md5-stable.txt', content, content_type='text/plain'),
                'expected_sha256': digest,
            },
            format='multipart',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        file_obj = File.objects.get(owner_id=self.user.id)
        self.assertEqual(file_obj.checksum_md5, expected_md5)
        self.assertEqual(file_obj.checksum_sha256, digest)
        self.assertEqual(file_obj.checksum_algorithm, 'sha256')
        self.assertEqual(file_obj.checksum, digest)

    def test_sha256_enforcement_still_rejects_mismatch_when_md5_would_match(self):
        """Integrity gate is SHA-256: wrong expected_sha256 fails even if MD5 of bytes is fine."""
        content = b'same bytes for md5'
        # The non-cryptographic MD5 compatibility fingerprint remains well-defined.
        self.assertTrue(hashlib.md5(content, usedforsecurity=False).hexdigest())
        resp = self.client.post(
            UPLOAD_URL,
            {
                'file': SimpleUploadedFile('md5-ok-sha-bad.txt', content, content_type='text/plain'),
                'expected_sha256': 'a' * 64,  # valid format, wrong digest
            },
            format='multipart',
        )
        self.assertEqual(resp.status_code, 409, resp.content)
        self.assertEqual(resp.data['error']['code'], 'checksum_mismatch')
        self.assertEqual(File.objects.count(), 0)


class ResumableUploadChecksumMd5StabilityTests(TestCase):
    """PLAT-4252: resumable completion keeps MD5 metadata non-authoritative."""

    def setUp(self):
        self.media_root = tempfile.TemporaryDirectory(prefix='drive-plat4252-')
        self.addCleanup(self.media_root.cleanup)
        settings_override = override_settings(
            MEDIA_ROOT=self.media_root.name,
            ALLOWED_FILE_TYPES=['text/plain'],
        )
        settings_override.enable()
        self.addCleanup(settings_override.disable)

        self.client = APIClient()
        self.user = User.objects.create_user(
            username='plat4252-resumable',
            email='plat4252-resumable@example.com',
            password='x',
        )
        self.client.force_authenticate(user=self.user)
        StorageQuota.objects.get_or_create(
            owner_id=self.user.id,
            defaults={'quota_bytes': 10**9},
        )
        processing = patch('drive.views.process_file_upload.delay')
        self.addCleanup(processing.stop)
        processing.start()

    def _create_and_fill_session(self, content, expected_sha256):
        created = self.client.post(
            '/api/uploads',
            {
                'filename': 'resumable-md5.txt',
                'total_size': len(content),
                'mime_type': 'text/plain',
                'expected_sha256': expected_sha256,
            },
            format='json',
        )
        self.assertEqual(created.status_code, 201, created.content)
        session_id = created.data['session_id']
        uploaded = self.client.put(
            f'/api/uploads/{session_id}/chunks',
            data=content,
            content_type='application/octet-stream',
            HTTP_CONTENT_RANGE=f'bytes 0-{len(content) - 1}/{len(content)}',
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.content)
        return session_id

    def test_resumable_completion_stores_historical_md5_fingerprint(self):
        content = b'plat-4252 resumable md5 stability payload'
        expected_md5 = hashlib.md5(content, usedforsecurity=False).hexdigest()
        expected_sha256 = hashlib.sha256(content).hexdigest()
        session_id = self._create_and_fill_session(content, expected_sha256)

        response = self.client.post(
            f'/api/uploads/{session_id}/commit',
            {'expected_sha256': expected_sha256},
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.content)
        file_obj = File.objects.get(id=response.data['id'])
        self.assertEqual(file_obj.checksum_md5, expected_md5)
        self.assertEqual(file_obj.checksum_sha256, expected_sha256)
        self.assertEqual(file_obj.checksum_algorithm, 'sha256')
        self.assertEqual(file_obj.checksum, expected_sha256)

    def test_resumable_completion_rejects_wrong_sha256_without_file(self):
        content = b'plat-4252 resumable sha256 mismatch payload'
        session_id = self._create_and_fill_session(content, 'a' * 64)

        response = self.client.post(
            f'/api/uploads/{session_id}/commit',
            {},
            format='json',
        )

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data['error']['code'], 'checksum_mismatch')
        self.assertEqual(File.objects.count(), 0)
        session = ResumableUpload.objects.get(id=session_id)
        self.assertEqual(session.status, ResumableUpload.Status.ABORTED)
        self.assertIsNone(session.file_id)
