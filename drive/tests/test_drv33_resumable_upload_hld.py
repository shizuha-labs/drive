"""DRV-33: HLD resumable upload contract."""
import hashlib
import tempfile
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from drive.models import File, ResumableUpload, StorageQuota
from drive.resumable_storage import is_shared_staging_path, staging_exists

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='drive-drv33-'), ALLOWED_FILE_TYPES=['text/plain'])
class ResumableUploadContractTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='drv33', email='drv33@example.com', password='x')
        self.client.force_authenticate(user=self.user)
        self.quota, _ = StorageQuota.objects.get_or_create(owner_id=self.user.id)
        self.quota.quota_bytes = 1024 * 1024
        self.quota.save(update_fields=['quota_bytes'])
        p = patch('drive.views.process_file_upload.delay')
        self.addCleanup(p.stop)
        p.start()

    def _create_session(self, content, **extra):
        payload = {
            'filename': 'large.txt',
            'total_size': len(content),
            'mime_type': 'text/plain',
            'expected_sha256': hashlib.sha256(content).hexdigest(),
        }
        payload.update(extra)
        response = self.client.post('/api/uploads', payload, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        return response.data['session_id']

    def _put_chunk(self, session_id, start, chunk, total, **headers):
        end = start + len(chunk) - 1
        return self.client.put(
            f'/api/uploads/{session_id}/chunks',
            data=chunk,
            content_type='application/octet-stream',
            HTTP_CONTENT_RANGE=f'bytes {start}-{end}/{total}',
            **headers,
        )

    def test_init_chunk_probe_commit_creates_invisible_until_complete_file_with_checksum(self):
        content = b'hello ' + b'x' * 2048 + b' world'
        session_id = self._create_session(content)

        first = content[:777]
        second = content[777:]
        first_digest = hashlib.sha256(first).hexdigest()
        r1 = self._put_chunk(session_id, 0, first, len(content), HTTP_X_DRIVE_CHUNK_SHA256=first_digest)
        self.assertEqual(r1.status_code, 200, r1.content)
        self.assertEqual(r1.data['committed_offset'], len(first))
        self.assertEqual(File.objects.count(), 0)

        probe = self.client.get(f'/api/uploads/{session_id}')
        self.assertEqual(probe.status_code, 200, probe.content)
        self.assertEqual(probe.data['committed_offset'], len(first))
        self.assertEqual(probe.data['total_size'], len(content))

        r2 = self._put_chunk(session_id, len(first), second, len(content))
        self.assertEqual(r2.status_code, 200, r2.content)
        self.assertEqual(r2.data['committed_offset'], len(content))

        committed = self.client.post(f'/api/uploads/{session_id}/commit', {'expected_sha256': hashlib.sha256(content).hexdigest()}, format='json')
        self.assertEqual(committed.status_code, 201, committed.content)
        file_obj = File.objects.get(id=committed.data['id'])
        self.assertEqual(file_obj.checksum_sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(file_obj.file.read(), content)

        duplicate_commit = self.client.post(f'/api/uploads/{session_id}/commit', {}, format='json')
        self.assertEqual(duplicate_commit.status_code, 200, duplicate_commit.content)
        self.assertEqual(duplicate_commit.data['id'], committed.data['id'])
        self.assertEqual(File.objects.count(), 1)

    def test_resume_reupload_of_committed_chunk_is_idempotent_when_bytes_match(self):
        content = b'abcdefghi'
        session_id = self._create_session(content)
        chunk = content[:3]
        first = self._put_chunk(session_id, 0, chunk, len(content))
        self.assertEqual(first.status_code, 200, first.content)

        retry = self._put_chunk(session_id, 0, chunk, len(content))
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(retry.data['committed_offset'], 3)

        stale_different = self._put_chunk(session_id, 0, b'xyz', len(content))
        self.assertEqual(stale_different.status_code, 409)
        self.assertEqual(stale_different.data['error']['code'], 'upload_offset_conflict')

    def test_offset_gap_and_chunk_checksum_mismatch_do_not_advance_offset(self):
        content = b'abcdef'
        session_id = self._create_session(content)

        gap = self._put_chunk(session_id, 3, b'def', len(content))
        self.assertEqual(gap.status_code, 409)
        self.assertEqual(gap.data['error']['details']['committed_offset'], 0)

        bad_digest = self._put_chunk(session_id, 0, b'abc', len(content), HTTP_X_DRIVE_CHUNK_SHA256='0' * 64)
        self.assertEqual(bad_digest.status_code, 409)
        self.assertEqual(bad_digest.data['error']['code'], 'chunk_checksum_mismatch')
        self.assertEqual(ResumableUpload.objects.get(id=session_id).uploaded_bytes, 0)

    def test_final_checksum_mismatch_leaves_no_visible_file_and_deletes_temp(self):
        content = b'good-bytes'
        session_id = self._create_session(content, expected_sha256='f' * 64)
        session = ResumableUpload.objects.get(id=session_id)
        temp_path = session.temp_path
        self.assertTrue(is_shared_staging_path(temp_path))
        upload = self._put_chunk(session_id, 0, content, len(content))
        self.assertEqual(upload.status_code, 200, upload.content)

        response = self.client.post(f'/api/uploads/{session_id}/commit', {}, format='json')
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.data['error']['code'], 'checksum_mismatch')
        self.assertFalse(File.objects.exists())
        self.assertFalse(staging_exists(temp_path))

    def test_expired_session_returns_404_and_cleanup_command_removes_staging(self):
        content = b'expire-me'
        session_id = self._create_session(content)
        session = ResumableUpload.objects.get(id=session_id)
        temp_path = session.temp_path
        session.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        session.save(update_fields=['expires_at'])

        probe = self.client.get(f'/api/uploads/{session_id}')
        self.assertEqual(probe.status_code, 404)
        session.refresh_from_db()
        self.assertEqual(session.status, ResumableUpload.Status.ABORTED)

        # A separate active expired session is cleaned by the operational command.
        session_id_2 = self._create_session(content)
        session2 = ResumableUpload.objects.get(id=session_id_2)
        temp_path2 = session2.temp_path
        session2.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        session2.save(update_fields=['expires_at'])
        out = StringIO()
        call_command('cleanup_resumable_uploads', stdout=out)
        self.assertIn('Aborted 2 expired resumable upload session', out.getvalue())
        session2.refresh_from_db()
        self.assertEqual(session2.status, ResumableUpload.Status.ABORTED)
        self.assertFalse(staging_exists(temp_path2))
        self.assertFalse(staging_exists(temp_path))

    def test_chunks_survive_missing_local_media_between_requests(self):
        """Shared staging must not depend on the session-creating pod."""
        content = b'cross-replica-' + b'x' * 4096
        session_id = self._create_session(content)
        session = ResumableUpload.objects.get(id=session_id)
        self.assertTrue(is_shared_staging_path(session.temp_path))

        first = content[:2048]
        self.assertEqual(self._put_chunk(session_id, 0, first, len(content)).status_code, 200)
        # The legacy implementation opened ``session.temp_path`` directly on
        # every request. Simulate the next replica not having that local file;
        # shared-storage chunk I/O must remain unaffected.
        with patch('drive.views.open', side_effect=FileNotFoundError('other pod'), create=True):
            second = self._put_chunk(session_id, len(first), content[len(first):], len(content))
            self.assertEqual(second.status_code, 200, second.content)
            committed = self.client.post(
                f'/api/uploads/{session_id}/commit',
                {'expected_sha256': hashlib.sha256(content).hexdigest()},
                format='json',
            )

        self.assertEqual(committed.status_code, 201, committed.content)
        self.assertEqual(File.objects.get(id=committed.data['id']).file.read(), content)
