"""DRV-25 P1: optimistic concurrency / base-version check on file update and upload_version.

INV-1: No silent lost update — any write based on a stale version is rejected with 409.

Coverage:
  update (PATCH):
    1. Stale base_version → 409 with current_version
    2. Missing base_version on clobber-capable field → 400
    3. Correct base_version → 200, version incremented
    4. Pure idempotent field (is_starred) → no base_version required

  upload_version (POST /files/{id}/upload_version/):
    5. Stale base_version → 409
    6. Missing base_version → 400
    7. Correct base_version → 200, FileVersion snapshot created, version incremented
    8. Two-writer race: both supply same base_version → one wins, one 409
"""
import hashlib
import io
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase, APITransactionTestCase

from drive.models import File, FileVersion

User = get_user_model()


def _mk_file(owner, version=1, name='doc.txt'):
    return File.objects.create(
        name=name,
        owner_id=str(owner.id),
        file=SimpleUploadedFile(name, b'hello world', content_type='text/plain'),
        mime_type='text/plain',
        size=11,
        file_type='document',
        version=version,
    )


class FileUpdateBaseVersionTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='drv25u', email='drv25u@shizuha.com', password='x')
        self.client.force_authenticate(user=self.user)
        self.file = _mk_file(self.user)

    def _url(self, fk=None):
        return f'/api/files/{fk or self.file.id}/'

    def test_stale_base_version_returns_409(self):
        resp = self.client.patch(self._url(), {'name': 'new.txt', 'base_version': 0}, format='multipart')
        self.assertEqual(resp.status_code, 409)
        self.assertIn('current_version', resp.json())
        self.assertEqual(resp.json()['current_version'], 1)

    def test_missing_base_version_on_clobber_field_returns_400(self):
        resp = self.client.patch(self._url(), {'name': 'new.txt'}, format='multipart')
        self.assertEqual(resp.status_code, 400)

    def test_correct_base_version_succeeds_and_increments_version(self):
        resp = self.client.patch(self._url(), {'name': 'renamed.txt', 'base_version': 1}, format='multipart')
        self.assertEqual(resp.status_code, 200)
        self.file.refresh_from_db()
        self.assertEqual(self.file.name, 'renamed.txt')
        self.assertEqual(self.file.version, 2)

    def test_starred_toggle_requires_no_base_version(self):
        resp = self.client.patch(self._url(), {'is_starred': True}, format='multipart')
        self.assertEqual(resp.status_code, 200)
        self.file.refresh_from_db()
        self.assertTrue(self.file.is_starred)
        # Version unchanged — idempotent toggle
        self.assertEqual(self.file.version, 1)

    def test_list_includes_current_version_for_safe_followup_writes(self):
        self.file.version = 7
        self.file.save(update_fields=['version'])

        resp = self.client.get('/api/files/')

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        rows = payload.get('results', payload)
        listed = next(row for row in rows if row['id'] == str(self.file.id))
        self.assertEqual(listed['version'], 7)


class FileUploadVersionTest(APITransactionTestCase):
    # DRV-71: TransactionTestCase (not the default TestCase) so setUp fixtures are
    # COMMITTED and therefore visible to the writer threads' own DB connections in
    # test_two_writer_race_one_wins_one_409 — under a plain TestCase the fixture
    # rows live in an uncommitted transaction the threads cannot see, so both
    # writers 404'd. Each writer thread also closes its connection (below) so no
    # dangling session blocks the post-test table truncation.
    def setUp(self):
        self.user = User.objects.create_user(username='drv25v', email='drv25v@shizuha.com', password='x')
        self.client.force_authenticate(user=self.user)
        self.file = _mk_file(self.user)

    def _url(self, fk=None):
        return f'/api/files/{fk or self.file.id}/upload_version/'

    def _new_file(self, content=b'new content'):
        return SimpleUploadedFile('doc.txt', content, content_type='text/plain')

    def test_stale_base_version_returns_409(self):
        resp = self.client.post(self._url(), {'base_version': 0, 'file': self._new_file()}, format='multipart')
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()['current_version'], 1)

    def test_missing_base_version_returns_400(self):
        resp = self.client.post(self._url(), {'file': self._new_file()}, format='multipart')
        self.assertEqual(resp.status_code, 400)

    def test_correct_base_version_snapshots_and_increments(self):
        with mock.patch('drive.views.process_file_upload.delay'):
            resp = self.client.post(
                self._url(),
                {'base_version': 1, 'file': self._new_file(b'v2 content')},
                format='multipart',
            )
        self.assertEqual(resp.status_code, 200)
        self.file.refresh_from_db()
        self.assertEqual(self.file.version, 2)

        # FileVersion snapshot of v1 must exist
        snap = FileVersion.objects.get(file=self.file, version_number=1)
        self.assertEqual(snap.created_by_id, str(self.user.id))

    def test_version_upload_replaces_integrity_metadata(self):
        old_sha256 = hashlib.sha256(b'hello world').hexdigest()
        self.file.checksum_md5 = hashlib.md5(b'hello world', usedforsecurity=False).hexdigest()
        self.file.checksum_sha256 = old_sha256
        self.file.checksum = old_sha256
        self.file.checksum_algorithm = 'sha256'
        self.file.save()
        new_content = b'new bytes with a different checksum'
        new_sha256 = hashlib.sha256(new_content).hexdigest()

        with mock.patch('drive.views.process_file_upload.delay'):
            resp = self.client.post(
                self._url(),
                {'base_version': 1, 'file': self._new_file(new_content)},
                format='multipart',
            )

        self.assertEqual(resp.status_code, 200)
        self.file.refresh_from_db()
        self.assertEqual(self.file.checksum_md5, hashlib.md5(new_content, usedforsecurity=False).hexdigest())
        self.assertEqual(self.file.checksum_sha256, new_sha256)
        self.assertEqual(self.file.checksum, new_sha256)
        self.assertEqual(self.file.checksum_algorithm, 'sha256')

        download = self.client.get(f'/api/files/{self.file.id}/download/')
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download['X-Drive-Integrity'], 'verified')
        self.assertEqual(download['X-Drive-Checksum-SHA256'], new_sha256)
        self.assertEqual(b''.join(download.streaming_content), new_content)

    def test_two_writer_race_one_wins_one_409(self):
        """Concurrent writers both supply base_version=1; exactly one must win."""
        from django.db import connection
        if connection.vendor == 'sqlite':
            self.skipTest(
                'SQLite test DB serializes/locks concurrent writes at the table level; '
                'the optimistic-concurrency race contract is covered on production DB backends.'
            )
        import threading
        results = []

        def _upload(content):
            # Each thread gets its own client to avoid shared state.
            from rest_framework.test import APIClient
            from django.db import connection as thread_connection
            c = APIClient()
            c.force_authenticate(user=self.user)
            try:
                with mock.patch('drive.views.process_file_upload.delay'):
                    r = c.post(
                        self._url(),
                        {'base_version': 1, 'file': SimpleUploadedFile('doc.txt', content, content_type='text/plain')},
                        format='multipart',
                    )
                results.append(r.status_code)
            finally:
                # DRV-71: close this thread's DB connection so it doesn't linger and
                # block TransactionTestCase's table truncation on teardown.
                thread_connection.close()

        t1 = threading.Thread(target=_upload, args=(b'writer-A',))
        t2 = threading.Thread(target=_upload, args=(b'writer-B',))
        t1.start(); t2.start()
        t1.join(); t2.join()

        self.assertIn(200, results, 'At least one writer must succeed')
        self.assertIn(409, results, 'At least one writer must be rejected (stale)')
        self.file.refresh_from_db()
        self.assertEqual(self.file.version, 2, 'Version should be 2 after exactly one successful write')
