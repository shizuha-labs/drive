"""DRV-105: Drive change-feed / delta contract.

Covers the ordered mutation feed + opaque cursor, the snapshot-fence
bootstrap (exactly-once convergence with no gap under concurrent mutation),
conditional-write 412, idempotent conflict-artifact creation, and tombstone
GC after retention.
"""
import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from drive.change_feed import (
    encode_cursor,
    decode_cursor,
    record_change,
    create_conflict_artifact,
    gc_expired_tombstones,
)
from drive.models import Change, ConflictArtifact, File, Folder

User = get_user_model()


def _mk_user(username='drv105u'):
    return User.objects.create_user(
        username=username, email=f'{username}@example.com', password='x',
    )


def _mk_file(owner, name='doc.txt', folder=None):
    return File.objects.create(
        owner_id=str(owner.id),
        name=name,
        folder=folder,
        file=SimpleUploadedFile(name, b'hello world', content_type='text/plain'),
        mime_type='text/plain',
        size=11,
        file_type='document',
    )


def _mk_folder(owner, name='folder'):
    return Folder.objects.create(owner_id=str(owner.id), name=name)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='drive-drv105-'))
class ChangeFeedApiTests(APITestCase):
    """GET /api/changes — ordered mutations + opaque cursor round-trip."""

    def setUp(self):
        self.user = _mk_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        # PostgreSQL sequences are non-transactional: Django TestCase rolls back
        # the transaction but NOT the sequence, so Change ids keep incrementing
        # across tests. That makes get_compaction_boundary() return id > 1 and
        # cursor 0 ("from the beginning") look expired (410) even with a fresh
        # feed. Reset the Change sequence so each test starts with boundary == 1.
        # (reset_sequences is only supported on TransactionTestCase, so do the
        # targeted reset here instead of changing the whole class to the slower
        # TransactionTestCase model.)
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, 'id'), 1, false)",
                    [Change._meta.db_table],
                )

    def _api_file(self, name='doc.txt'):
        """Upload a file through the API so the create mutation is recorded."""
        resp = self.client.post(
            '/api/files/',
            {'file': SimpleUploadedFile(name, b'hello world', content_type='text/plain')},
            format='multipart',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        return File.objects.get(id=resp.json()['id'])

    def _api_folder(self, name='folder'):
        resp = self.client.post('/api/folders/', {'name': name}, format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        return Folder.objects.get(id=resp.json()['id'])

    def test_cursor_round_trip_and_ordered_mutations(self):
        f1 = self._api_file('a.txt')
        f2 = self._api_file('b.txt')
        folder = self._api_folder()

        resp = self.client.get('/api/changes/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # create mutations for the two files + one folder, in ascending order.
        self.assertEqual(len(body['changes']), 3)
        self.assertEqual(body['changes'][0]['mutation'], 'create')
        self.assertEqual(body['changes'][0]['resource_id'], str(f1.id))
        self.assertEqual(body['changes'][1]['resource_id'], str(f2.id))
        self.assertEqual(body['changes'][2]['resource_id'], str(folder.id))
        self.assertTrue(body['has_more'] is False)
        self.assertNotEqual(body['next_cursor'], '')

        # Replay from the next cursor → empty delta (fast path).
        resp2 = self.client.get(f"/api/changes/?cursor={body['next_cursor']}")
        body2 = resp2.json()
        self.assertEqual(body2['changes'], [])
        self.assertEqual(body2['next_cursor'], body['next_cursor'])

    def test_mutation_sequence_create_update_move_trash(self):
        f = self._api_file('orig.txt')
        # update (rename) via PATCH with base_version
        resp = self.client.patch(
            f'/api/files/{f.id}/',
            {'name': 'renamed.txt', 'base_version': 1},
            format='multipart',
        )
        self.assertEqual(resp.status_code, 200)
        # move
        folder = self._api_folder()
        resp = self.client.post(f'/api/files/{f.id}/move/', {'folder': str(folder.id)}, format='json')
        self.assertEqual(resp.status_code, 200)
        # trash
        resp = self.client.delete(f'/api/files/{f.id}/')
        self.assertEqual(resp.status_code, 204)

        resp = self.client.get('/api/changes/')
        body = resp.json()
        file_mutations = [
            c['mutation'] for c in body['changes']
            if c['resource_id'] == str(f.id)
        ]
        self.assertEqual(file_mutations, ['create', 'rename', 'move', 'trash'])
        # generations advance monotonically for the file
        file_gens = [
            c['generation'] for c in body['changes']
            if c['resource_id'] == str(f.id)
        ]
        self.assertEqual(file_gens, [1, 2, 2, 2])

    def test_invalid_cursor_returns_400(self):
        resp = self.client.get('/api/changes/?cursor=%%%not-base64%%%')
        self.assertEqual(resp.status_code, 400)

    def test_expired_cursor_returns_410(self):
        """A cursor pointing before the compaction boundary → 410 + re-bootstrap."""
        self._api_file('a.txt')
        self._api_file('b.txt')
        # Consume one page of 1 → next_cursor points at the first change (id=1).
        resp = self.client.get('/api/changes/?limit=1')
        self.assertEqual(resp.status_code, 200)
        stale_cursor = resp.json()['next_cursor']
        self.assertEqual(decode_cursor(stale_cursor), 1)
        # Simulate compaction: GC the first change row (oldest retained → id=2).
        first = Change.objects.order_by('id').first()
        Change.objects.filter(id__lte=first.id).delete()
        # The stale cursor now points before the compaction boundary → 410.
        resp = self.client.get(f'/api/changes/?cursor={stale_cursor}')
        self.assertEqual(resp.status_code, status.HTTP_410_GONE)
        body = resp.json()
        self.assertTrue(body.get('cursor_expired'))
        self.assertIn('bootstrap', body.get('retry', ''))

    def test_valid_cursor_after_partial_compaction_still_works(self):
        """A cursor at/after the compaction boundary is not spuriously expired."""
        self._api_file('a.txt')
        self._api_file('b.txt')
        resp = self.client.get('/api/changes/?limit=1')
        stale_cursor = resp.json()['next_cursor']  # id=1
        # Consume the rest → cursor advances to id=2.
        resp2 = self.client.get(f'/api/changes/?cursor={stale_cursor}')
        self.assertEqual(resp2.status_code, 200)
        valid_cursor = resp2.json()['next_cursor']
        self.assertEqual(decode_cursor(valid_cursor), 2)
        # GC the first change row (compaction boundary → id=2).
        first = Change.objects.order_by('id').first()
        Change.objects.filter(id__lte=first.id).delete()
        # A cursor equal to the boundary is still valid → empty delta, not 410.
        resp3 = self.client.get(f'/api/changes/?cursor={valid_cursor}')
        self.assertEqual(resp3.status_code, 200)
        self.assertEqual(resp3.json()['changes'], [])

    def test_from_beginning_cursor_expired_after_gc(self):
        """Cursor 0 (no cursor) is expired once the retained feed no longer starts at id 1."""
        self._api_file('a.txt')
        self._api_file('b.txt')
        first = Change.objects.order_by('id').first()
        Change.objects.filter(id__lte=first.id).delete()
        resp = self.client.get('/api/changes/')
        self.assertEqual(resp.status_code, status.HTTP_410_GONE)
        self.assertTrue(resp.json().get('cursor_expired'))

    def test_owner_scoped(self):
        other = _mk_user('drv105other')
        _mk_file(other, 'other.txt')
        self._api_file('mine.txt')
        resp = self.client.get('/api/changes/')
        body = resp.json()
        self.assertEqual(len(body['changes']), 1)
        self.assertEqual(body['changes'][0]['resource_id'], str(File.objects.get(name='mine.txt').id))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='drive-drv105-'))
class ChangeBootstrapTests(TestCase):
    """Snapshot-fence bootstrap — exactly-once convergence with no gap."""

    def test_bootstrap_fence_then_replay_is_disjoint_and_complete(self):
        user = _mk_user()
        f1 = _mk_file(user, 'a.txt')
        f2 = _mk_file(user, 'b.txt')
        folder = _mk_folder(user)
        # Record create mutations for the pre-fence resources so the fence
        # logic can distinguish them from post-fence creates.
        for f in (f1, f2):
            record_change(
                owner_id=str(user.id), resource_type=Change.RESOURCE_FILE,
                resource_id=f.id, mutation='create', generation=1, name=f.name,
            )
        record_change(
            owner_id=str(user.id), resource_type=Change.RESOURCE_FOLDER,
            resource_id=folder.id, mutation='create', generation=1, name=folder.name,
        )

        # Simulate a fresh client: acquire the fence, then a concurrent
        # mutation lands (create c.txt + move a.txt) during the listing.
        from drive.change_feed import get_fence, bootstrap_snapshot, get_changes
        fence = get_fence(str(user.id))
        concurrent = _mk_file(user, 'c.txt')
        concurrent_folder = _mk_folder(user, 'concurrent-folder')
        record_change(
            owner_id=str(user.id), resource_type=Change.RESOURCE_FILE,
            resource_id=concurrent.id, mutation='create', generation=1, name=concurrent.name,
        )
        record_change(
            owner_id=str(user.id), resource_type=Change.RESOURCE_FOLDER,
            resource_id=concurrent_folder.id, mutation='create', generation=1,
            name=concurrent_folder.name,
        )
        # move a.txt concurrently
        f1.folder = concurrent_folder
        f1.save()
        record_change(
            owner_id=str(user.id), resource_type=Change.RESOURCE_FILE,
            resource_id=f1.id, mutation='move', generation=1,
            parent_id=concurrent_folder.id, name=f1.name,
        )

        snapshot = bootstrap_snapshot(str(user.id), fence=fence)
        snapshot_ids = {x['id'] for x in snapshot['files']} | {x['id'] for x in snapshot['folders']}

        # Replay strictly after the fence.
        changes, next_cursor, has_more = get_changes(str(user.id), cursor=fence)
        replay_ids = {str(c.resource_id) for c in changes}

        # The snapshot (as-of fence) + replay (after fence) cover every
        # resource exactly once — no gap, no overlap.
        all_ids = snapshot_ids | replay_ids
        self.assertIn(str(f1.id), all_ids)
        self.assertIn(str(f2.id), all_ids)
        self.assertIn(str(folder.id), all_ids)
        self.assertIn(str(concurrent.id), all_ids)
        self.assertIn(str(concurrent_folder.id), all_ids)
        # The concurrent create is in the replay, not the snapshot.
        self.assertNotIn(str(concurrent.id), snapshot_ids)
        self.assertIn(str(concurrent.id), replay_ids)
        # The concurrent move of a.txt is in the replay (f1 was in the snapshot).
        self.assertIn(str(f1.id), snapshot_ids)
        self.assertIn(str(f1.id), replay_ids)

    def test_bootstrap_endpoint_returns_fence_and_snapshot(self):
        user = _mk_user()
        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.post(
            '/api/files/',
            {'file': SimpleUploadedFile('a.txt', b'hello world', content_type='text/plain')},
            format='multipart',
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        resp = client.get('/api/changes/bootstrap/')
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn('fence_cursor', body)
        self.assertIn('snapshot', body)
        self.assertEqual(len(body['snapshot']['files']), 1)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='drive-drv105-'))
class ConditionalWriteTests(APITestCase):
    """DRV-105 conditional-write — stale generation returns 412, never overwrite."""

    def setUp(self):
        self.user = _mk_user()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.file = _mk_file(self.user)

    def test_stale_if_match_returns_412(self):
        resp = self.client.patch(
            f'/api/files/{self.file.id}/',
            {'name': 'new.txt'},
            format='multipart',
            HTTP_IF_MATCH='1',
        )
        # version is 1; a second writer bumps to 2 first, then this stale write → 412
        self.file.version = 2
        self.file.save()
        resp = self.client.patch(
            f'/api/files/{self.file.id}/',
            {'name': 'new.txt'},
            format='multipart',
            HTTP_IF_MATCH='1',
        )
        self.assertEqual(resp.status_code, status.HTTP_412_PRECONDITION_FAILED)
        body = resp.json()
        self.assertTrue(body.get('conflict'))
        self.assertEqual(body['current_version'], 2)

    def test_matching_if_match_succeeds(self):
        resp = self.client.patch(
            f'/api/files/{self.file.id}/',
            {'name': 'new.txt', 'base_version': 1},
            format='multipart',
            HTTP_IF_MATCH='1',
        )
        self.assertEqual(resp.status_code, 200)
        self.file.refresh_from_db()
        self.assertEqual(self.file.name, 'new.txt')

    def test_x_drive_generation_header(self):
        self.file.version = 5
        self.file.save()
        resp = self.client.patch(
            f'/api/files/{self.file.id}/',
            {'name': 'new.txt', 'base_version': 5},
            format='multipart',
            HTTP_X_DRIVE_GENERATION='4',
        )
        self.assertEqual(resp.status_code, status.HTTP_412_PRECONDITION_FAILED)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='drive-drv105-'))
class ConflictArtifactTests(TestCase):
    """Idempotent conflict-artifact creation (keep-both)."""

    def test_two_creates_same_identity_produce_one_artifact(self):
        user = _mk_user()
        f = _mk_file(user)
        identity = 'device-1:journal-op-42:observed-gen-3'
        key = 'conflict-uuid-abc'
        a1, created1 = create_conflict_artifact(
            owner_id=str(user.id), resource_id=f.id,
            conflict_identity=identity, idempotency_key=key,
            name='report (conflicted copy 2026-08-16 10:00 UTC)',
        )
        a2, created2 = create_conflict_artifact(
            owner_id=str(user.id), resource_id=f.id,
            conflict_identity=identity, idempotency_key=key,
            name='report (conflicted copy 2026-08-16 10:00 UTC)',
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(a1.id, a2.id)
        self.assertEqual(ConflictArtifact.objects.count(), 1)
        self.assertEqual(a1.first_detected_at, a2.first_detected_at)

    def test_conflict_endpoint_idempotent(self):
        user = _mk_user()
        f = _mk_file(user)
        client = APIClient()
        client.force_authenticate(user=user)
        payload = {
            'resource_id': str(f.id),
            'conflict_identity': 'dev-9:op-1:gen-2',
            'idempotency_key': 'key-1',
            'name': 'x (conflicted copy)',
        }
        r1 = client.post('/api/changes/conflicts/', payload, format='json')
        r2 = client.post('/api/changes/conflicts/', payload, format='json')
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json()['id'], r2.json()['id'])
        self.assertTrue(r1.json()['created'])
        self.assertFalse(r2.json()['created'])
        self.assertEqual(ConflictArtifact.objects.count(), 1)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='drive-drv105-'))
class TombstoneTests(TestCase):
    """Deletes appear as ordered tombstones and GC after retention."""

    def test_delete_tombstone_and_gc(self):
        user = _mk_user()
        f = _mk_file(user)
        record_change(
            owner_id=str(user.id), resource_type=Change.RESOURCE_FILE,
            resource_id=f.id, mutation='delete', generation=1,
            name=f.name, tombstone=True,
        )
        tombstone = Change.objects.filter(mutation='delete').first()
        self.assertIsNotNone(tombstone)
        self.assertIsNotNone(tombstone.expires_at)

        # Not yet expired → not GC'd.
        self.assertEqual(gc_expired_tombstones(), 0)
        # Expire it → GC'd.
        Change.objects.filter(pk=tombstone.pk).update(
            expires_at=timezone.now() - timezone.timedelta(days=1),
        )
        self.assertEqual(gc_expired_tombstones(), 1)
        self.assertEqual(Change.objects.filter(mutation='delete').count(), 0)

    def test_cursor_encode_decode(self):
        self.assertEqual(decode_cursor(encode_cursor(42)), 42)
        self.assertEqual(decode_cursor(''), 0)
        self.assertIsNone(decode_cursor('not-base64!'))
