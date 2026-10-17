"""VEN-153 — data export (ZIP download) and offboarding (30-day grace deletion)."""
import json
import os
import tempfile
import uuid
import zipfile
import io
from datetime import timedelta
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from drive.models import (
    DeletionRequest, DeletionProcessorState, File, FileVersion, Folder, ResumableUpload, Activity,
    FileActivity, Share, ShareAccessLog, ServiceStorageUsage,
)
from drive.views import DriveTelemetryView, ExportView, OffboardingView
from drivemanager.urls import offboarding_statusz


def _stream_content(resp):
    """Consume a streaming response and return bytes."""
    content = b''.join(resp.streaming_content)
    # APIRequestFactory bypasses Django's request handler. Calling response.close()
    # directly here would emit request_finished and close TestCase's PostgreSQL
    # transaction connection, so invoke the registered file closer itself.
    resp.file_to_stream.close()
    return content


class _U:
    """Minimal user stub for test auth."""
    def __init__(self, uid='u1', email='user@example.com', is_staff=False):
        self.id = uid
        self.pk = uid
        self.email = email
        self.is_staff = is_staff
        self.is_authenticated = True


def _make_file(**kwargs):
    """Create a File with actual storage content so export can read it."""
    content = kwargs.pop('content', b'file content')
    f = File.objects.create(**kwargs)
    f.file.save(kwargs.get('name', 'untitled'), ContentFile(content), save=True)
    return f


class VEN153ExportTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = _U(uid='export-user', email='alice@example.com')

    def _export(self, user=None):
        req = self.factory.post('/api/drive/export/')
        force_authenticate(req, user=user or self.user)
        return ExportView.as_view()(req)

    def test_export_returns_zip_with_no_files(self):
        """Export with no files returns a valid empty ZIP."""
        resp = self._export()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/zip')
        self.assertIn('shizuha-drive-export', resp['Content-Disposition'])
        # Verify it's a valid ZIP
        zf = zipfile.ZipFile(io.BytesIO(_stream_content(resp)))
        self.assertEqual(zf.namelist(), ['manifest.json'])
        manifest = json.loads(zf.read('manifest.json'))
        self.assertTrue(manifest['complete'])
        self.assertEqual(manifest['blob_count'], 0)

    def test_export_includes_user_files(self):
        """Export includes all non-trashed files."""
        folder = Folder.objects.create(owner_id=self.user.id, name='Photos')
        _make_file(
            owner_id=self.user.id, folder=folder,
            name='photo.jpg', mime_type='image/jpeg', size=1024,
        )
        _make_file(
            owner_id=self.user.id, folder=folder,
            name='doc.pdf', mime_type='application/pdf', size=2048,
        )
        resp = self._export()
        self.assertEqual(resp.status_code, 200)
        zf = zipfile.ZipFile(io.BytesIO(_stream_content(resp)))
        names = zf.namelist()
        self.assertIn('files/Photos/photo.jpg', names)
        self.assertIn('files/Photos/doc.pdf', names)
        self.assertIn('manifest.json', names)

    def test_export_includes_trash_versions_and_complete_manifest(self):
        """The explicit all-data contract covers trash and retained versions."""
        _make_file(
            owner_id=self.user.id, name='active.txt',
            mime_type='text/plain', size=100, is_trashed=False,
        )
        trashed = _make_file(
            owner_id=self.user.id, name='deleted.txt',
            mime_type='text/plain', size=200, is_trashed=True,
        )
        version = FileVersion.objects.create(
            file=trashed,
            version_number=1,
            size=11,
            created_by_id=self.user.id,
        )
        version.file_data.save('deleted-v1.txt', ContentFile(b'old content'), save=True)
        resp = self._export()
        zf = zipfile.ZipFile(io.BytesIO(_stream_content(resp)))
        names = zf.namelist()
        self.assertIn('files/active.txt', names)
        self.assertIn('.trash/deleted.txt', names)
        version_path = f'.versions/{trashed.id}/v1/deleted.txt'
        self.assertIn(version_path, names)
        manifest = json.loads(zf.read('manifest.json'))
        self.assertTrue(manifest['complete'])
        self.assertEqual(manifest['blob_count'], 3)
        self.assertEqual(
            {entry['kind'] for entry in manifest['entries']},
            {'current_file', 'trashed_file', 'file_version'},
        )

    def test_export_reads_source_in_bounded_chunks(self):
        """A large source object is never read with an unbounded/full-file call."""
        content = b'x' * (2 * 1024 * 1024 + 17)
        f = _make_file(
            owner_id=self.user.id,
            name='large.bin',
            mime_type='application/octet-stream',
            size=len(content),
            content=content,
        )
        f = File.objects.get(pk=f.pk)
        storage = f.file.storage
        reads = []

        class TrackingReader(io.BytesIO):
            def read(self, size=-1):
                reads.append(size)
                if size < 0 or size > 1024 * 1024:
                    raise AssertionError(f'unbounded export read: {size}')
                return super().read(size)

        with patch.object(storage, 'open', return_value=TrackingReader(content)):
            resp = self._export()
            archive = zipfile.ZipFile(io.BytesIO(_stream_content(resp)))
        self.assertEqual(archive.read('files/large.bin'), content)
        self.assertTrue(reads)
        self.assertLessEqual(max(reads), 1024 * 1024)

    def test_export_uses_normal_file_response_resource_closer(self):
        """Closing FileResponse closes and removes its staged temporary file."""
        resp = self._export()
        staged_path = resp.file_to_stream.name
        self.assertTrue(os.path.exists(staged_path))
        self.assertIn(resp.file_to_stream.close, resp._resource_closers)
        b''.join(resp.streaming_content)
        resp.file_to_stream.close()
        self.assertFalse(os.path.exists(staged_path))

    def test_export_requires_auth(self):
        """Unauthenticated requests get 401."""
        req = self.factory.post('/api/drive/export/')
        resp = ExportView.as_view()(req)
        self.assertEqual(resp.status_code, 401)

    def test_export_fails_on_storage_read_error(self):
        """A storage read error causes HTTP 500, not a partial archive."""
        f = _make_file(
            owner_id=self.user.id, name='bad.txt',
            mime_type='text/plain', size=200,
        )
        storage = f.file.storage
        with patch.object(storage, 'open', side_effect=Exception('storage read error')):
            resp = self._export()
        self.assertEqual(resp.status_code, 500)


class VEN153OffboardingTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.user = _U(uid='offboard-user', email='bob@example.com')

    def _post(self, data=None, user=None):
        req = self.factory.post('/api/drive/offboard/', data or {}, format='json')
        force_authenticate(req, user=user or self.user)
        return OffboardingView.as_view()(req)

    def _get(self, user=None):
        req = self.factory.get('/api/drive/offboard/')
        force_authenticate(req, user=user or self.user)
        return OffboardingView.as_view()(req)

    def _delete(self, user=None):
        req = self.factory.delete('/api/drive/offboard/')
        force_authenticate(req, user=user or self.user)
        return OffboardingView.as_view()(req)

    def test_create_deletion_request(self):
        """POST creates a pending DeletionRequest with 30-day grace."""
        resp = self._post({'reason': 'Switching to another service'})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['status'], 'pending')
        self.assertEqual(resp.data['owner_id'], self.user.id)
        self.assertIsNotNone(resp.data['grace_expires_at'])
        self.assertGreater(resp.data['days_remaining'], 25)  # ~30 days

        dr = DeletionRequest.objects.get(owner_id=self.user.id)
        self.assertEqual(dr.reason, 'Switching to another service')
        self.assertEqual(dr.email, 'bob@example.com')

    def test_create_without_reason(self):
        """POST without reason still creates the request."""
        resp = self._post({})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['status'], 'pending')

    def test_duplicate_request_rejected(self):
        """Production race order: committed winner makes the loser return 409."""
        self._post()
        resp = self._post()
        self.assertEqual(resp.status_code, 409)
        self.assertIn('already have a pending', resp.data['error'])
        self.assertEqual(
            DeletionRequest.objects.filter(
                owner_id=self.user.id,
                status=DeletionRequest.STATUS_PENDING,
            ).count(),
            1,
        )

    def test_get_returns_active_request(self):
        """GET returns the user's deletion request."""
        self._post()
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.data['deletion_request'])
        self.assertEqual(resp.data['deletion_request']['status'], 'pending')

    def test_get_returns_none_when_no_request(self):
        """GET returns null when no deletion request exists."""
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.data['deletion_request'])

    def test_cancel_pending_request(self):
        """DELETE cancels a pending request."""
        self._post()
        resp = self._delete()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['status'], 'cancelled')

        dr = DeletionRequest.objects.get(owner_id=self.user.id)
        self.assertEqual(dr.status, 'cancelled')
        self.assertIsNotNone(dr.cancelled_at)

    def test_cancel_without_request_returns_404(self):
        """DELETE without a pending request returns 404."""
        resp = self._delete()
        self.assertEqual(resp.status_code, 404)

    def test_cancel_already_cancelled_returns_404(self):
        """DELETE on a cancelled request returns 404 (no pending)."""
        self._post()
        self._delete()
        resp = self._delete()
        self.assertEqual(resp.status_code, 404)

    def test_cancel_after_grace_expiry_returns_conflict(self):
        """Cancellation closes at expiry even if the worker has not run yet."""
        DeletionRequest.objects.create(
            owner_id=self.user.id,
            grace_expires_at=timezone.now() - timedelta(seconds=1),
        )
        resp = self._delete()
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(
            DeletionRequest.objects.get(owner_id=self.user.id).status,
            DeletionRequest.STATUS_PENDING,
        )

    def test_requires_auth(self):
        """Unauthenticated requests get 401."""
        for method in [self.factory.post, self.factory.get, self.factory.delete]:
            req = method('/api/drive/offboard/')
            resp = OffboardingView.as_view()(req)
            self.assertEqual(resp.status_code, 401)

    def test_grace_period_configurable(self):
        """Grace period respects DRIVE_DELETION_GRACE_DAYS setting."""
        with override_settings(DRIVE_DELETION_GRACE_DAYS=7):
            resp = self._post()
            self.assertEqual(resp.status_code, 201)
            dr = DeletionRequest.objects.get(owner_id=self.user.id)
            expected = timezone.now() + timedelta(days=7)
            # Allow 1s clock skew
            self.assertAlmostEqual(
                dr.grace_expires_at.timestamp(),
                expected.timestamp(),
                delta=2,
            )


class VEN153DeletionRequestModelTests(TestCase):
    def test_is_expired_true_when_past_grace(self):
        """is_expired is True for pending requests past grace_expires_at."""
        dr = DeletionRequest.objects.create(
            owner_id='u1',
            grace_expires_at=timezone.now() - timedelta(hours=1),
        )
        self.assertTrue(dr.is_expired)

    def test_is_expired_false_when_within_grace(self):
        """is_expired is False for pending requests within grace period."""
        dr = DeletionRequest.objects.create(
            owner_id='u1',
            grace_expires_at=timezone.now() + timedelta(days=29),
        )
        self.assertFalse(dr.is_expired)

    def test_is_expired_false_when_cancelled(self):
        """is_expired is False for cancelled requests even past grace."""
        dr = DeletionRequest.objects.create(
            owner_id='u1',
            status=DeletionRequest.STATUS_CANCELLED,
            grace_expires_at=timezone.now() - timedelta(hours=1),
        )
        self.assertFalse(dr.is_expired)

    def test_cancel_raises_on_completed(self):
        """cancel() raises ValidationError on non-pending requests."""
        dr = DeletionRequest.objects.create(
            owner_id='u1',
            status=DeletionRequest.STATUS_COMPLETED,
            grace_expires_at=timezone.now() + timedelta(days=30),
        )
        with self.assertRaises(Exception):
            dr.cancel()

    def test_complete_sets_timestamp(self):
        """complete() sets completed_at and status."""
        dr = DeletionRequest.objects.create(
            owner_id='u1',
            grace_expires_at=timezone.now() + timedelta(days=30),
        )
        dr.complete()
        self.assertEqual(dr.status, 'completed')
        self.assertIsNotNone(dr.completed_at)

    def test_stale_cancel_cannot_overwrite_completed_purge(self):
        """Production order: stale reader -> worker completion -> cancel attempt."""
        dr = DeletionRequest.objects.create(
            owner_id='stale-cancel-user',
            grace_expires_at=timezone.now() + timedelta(days=30),
        )
        stale = DeletionRequest.objects.get(pk=dr.pk)
        current = DeletionRequest.objects.get(pk=dr.pk)
        current.complete()

        with self.assertRaises(Exception):
            stale.cancel()

        dr.refresh_from_db()
        self.assertEqual(dr.status, DeletionRequest.STATUS_COMPLETED)


class VEN153PurgeRetryTests(TestCase):
    """VEN-153 review fix: a storage failure during permanent purge must leave the
    File row intact for a retry (not orphan the blob), and the DeletionRequest
    stays pending until every blob is actually deleted."""

    def test_storage_failure_keeps_file_row_then_retry_purges(self):
        from drive.tasks import process_expired_deletion_requests

        owner = 'purge-user'
        f = _make_file(owner_id=owner, name='doc.txt', mime_type='text/plain', size=4, content=b'data')
        blob_path = f.file.name
        storage = f.file.storage
        DeletionRequest.objects.create(
            owner_id=owner,
            grace_expires_at=timezone.now() - timedelta(days=1),
        )

        # First run: storage.delete raises → the File row must SURVIVE (blob not
        # orphaned), the DeletionRequest stays pending, and the blob is still there.
        with patch.object(storage, 'delete', side_effect=Exception('storage down')):
            res = process_expired_deletion_requests()
        self.assertEqual(res['processed'], 0)
        dr = DeletionRequest.objects.get(owner_id=owner)
        self.assertEqual(dr.status, DeletionRequest.STATUS_PENDING)
        self.assertTrue(
            File.objects.filter(id=f.id).exists(),
            'File row must survive a storage failure so the blob can be retried',
        )
        self.assertTrue(storage.exists(blob_path))

        # Second run: storage works → the file + blob are purged and the request completes.
        res2 = process_expired_deletion_requests()
        self.assertEqual(res2['processed'], 1)
        dr.refresh_from_db()
        self.assertEqual(dr.status, DeletionRequest.STATUS_COMPLETED)
        self.assertFalse(File.objects.filter(id=f.id).exists())
        self.assertFalse(storage.exists(blob_path))

    def test_active_resumable_temp_failure_keeps_retry_target_then_completes(self):
        """file=None upload bytes are removed before their DB row is discarded."""
        from drive.tasks import process_expired_deletion_requests

        owner = 'resumable-purge-user'
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            temp_dir = os.path.join(media_root, 'resumable_uploads', owner)
            os.makedirs(temp_dir)
            temp_path = os.path.join(temp_dir, 'active.part')
            with open(temp_path, 'wb') as stream:
                stream.write(b'uncommitted bytes')
            upload = ResumableUpload.objects.create(
                owner_id=owner,
                name='active.bin',
                size=17,
                temp_path=temp_path,
                status=ResumableUpload.Status.ACTIVE,
                expires_at=timezone.now() + timedelta(hours=1),
                file=None,
            )
            dr = DeletionRequest.objects.create(
                owner_id=owner,
                grace_expires_at=timezone.now() - timedelta(days=1),
            )

            with patch('drive.tasks.os.remove', side_effect=OSError('disk busy')):
                first = process_expired_deletion_requests()
            self.assertEqual(first['processed'], 0)
            dr.refresh_from_db()
            self.assertEqual(dr.status, DeletionRequest.STATUS_PENDING)
            self.assertEqual(dr.owner_id, owner)
            self.assertTrue(ResumableUpload.objects.filter(pk=upload.pk).exists())
            self.assertTrue(os.path.exists(temp_path))

            second = process_expired_deletion_requests()
            self.assertEqual(second['processed'], 1)
            dr.refresh_from_db()
            self.assertEqual(dr.status, DeletionRequest.STATUS_COMPLETED)
            self.assertEqual(dr.owner_id, '')
            self.assertFalse(ResumableUpload.objects.filter(pk=upload.pk).exists())
            self.assertFalse(os.path.exists(temp_path))


class VEN153ResidualPurgeTests(TestCase):
    """VEN-153 review fix: the offboarding purge must clean or anonymise every
    user-scoped record, not just files/folders/shares/quota."""

    def test_residual_data_is_purged_or_anonymised(self):
        from drive.tasks import process_expired_deletion_requests

        owner = 'residual-user'
        now = timezone.now()

        # Create a DeletionRequest
        dr = DeletionRequest.objects.create(
            owner_id=owner,
            email='user@example.com',
            reason='test offboarding',
            grace_expires_at=now - timedelta(days=1),
        )

        # Seed residual data across all user-scoped tables
        # 1. ResumableUpload
        ru = ResumableUpload.objects.create(
            owner_id=owner, name='pending_upload.txt',
            size=100, temp_path='uploads/tmp/pending',
            status=ResumableUpload.Status.ACTIVE,
            expires_at=now + timedelta(hours=1),
        )

        # 2. Activity (user_id and actor_id)
        Activity.objects.create(
            user_id=owner, actor_id=owner,
            action='create', event_type='create',
            resource_name='myfile.txt',
            details={'key': 'value'}, metadata={'note': 'test'},
        )
        # Activity where user is only the actor (different user_id)
        Activity.objects.create(
            user_id='other', actor_id=owner,
            action='share', event_type='share',
        )

        # 3. FileActivity (needs exactly one of file or folder)
        f = _make_file(owner_id=owner, name='temp.txt', mime_type='text/plain', size=1)
        FileActivity.objects.create(
            user_id=owner, action=FileActivity.ACTION_OPEN,
            file=f,
        )
        # A collaborator-authored event targets the owner's resource. Its actor
        # does not match the owner, so resource targeting (captured before FK
        # SET_NULL) must still remove every owner-linked identifier/metadata.
        collaborator_activity = Activity.objects.create(
            user_id='collaborator', actor_id='collaborator',
            file=f, action='view', event_type='view',
            resource_name='temp.txt',
            details={'owner_filename': 'temp.txt'},
            metadata={'stable_file_id': str(f.id)},
        )

        # 4. Share (received — shared_with_id)
        Share.objects.create(
            shared_with_id=owner, shared_with_email='user@example.com',
            created_by_id='other', share_type='user',
            permission='view',
        )

        # 5. ShareAccessLog
        ShareAccessLog.objects.create(
            share_id_copy=uuid.uuid4(),
            viewer_id=owner, viewer_ip='1.2.3.4',
            user_agent='TestAgent/1.0',
            action=ShareAccessLog.ACTION_VIEW,
            result=ShareAccessLog.RESULT_OK,
        )

        # 6. ServiceStorageUsage
        ServiceStorageUsage.objects.create(
            owner_id=owner, service='wiki', used_bytes=5000,
        )

        # Run the purge
        res = process_expired_deletion_requests()
        self.assertEqual(res['processed'], 1)

        # Verify residual data is gone or anonymised
        dr.refresh_from_db()
        self.assertEqual(dr.status, DeletionRequest.STATUS_COMPLETED)
        self.assertEqual(dr.owner_id, '')
        # DeletionRequest email/reason cleared
        self.assertEqual(dr.email, '')
        self.assertEqual(dr.reason, '')

        # ResumableUpload: rows purged
        self.assertFalse(ResumableUpload.objects.filter(owner_id=owner).exists())

        # Activity: user_id/actor_id anonymised (set to '')
        for a in Activity.objects.filter(user_id=''):
            self.assertEqual(a.user_id, '')
            self.assertEqual(a.resource_name, '')
            self.assertEqual(a.details, {})
            self.assertEqual(a.metadata, {})
        # Activity where actor_id was owner but user_id was 'other'
        self.assertTrue(Activity.objects.filter(user_id='other', actor_id='').exists())
        collaborator_activity.refresh_from_db()
        self.assertEqual(collaborator_activity.user_id, 'collaborator')
        self.assertEqual(collaborator_activity.actor_id, 'collaborator')
        self.assertIsNone(collaborator_activity.file_id)
        self.assertIsNone(collaborator_activity.resource_id)
        self.assertIsNone(collaborator_activity.parent_id)
        self.assertEqual(collaborator_activity.resource_type, '')
        self.assertEqual(collaborator_activity.resource_name, '')
        self.assertEqual(collaborator_activity.details, {})
        self.assertEqual(collaborator_activity.metadata, {})

        # FileActivity: rows purged
        self.assertFalse(FileActivity.objects.filter(user_id=owner).exists())

        # Share (received): rows purged
        self.assertFalse(Share.objects.filter(shared_with_id=owner).exists())

        # ShareAccessLog: viewer_id/IP/user_agent anonymised
        for log in ShareAccessLog.objects.filter(viewer_id__isnull=True):
            self.assertIsNone(log.viewer_id)
            self.assertIsNone(log.viewer_ip)
            self.assertEqual(log.user_agent, '')

        # ServiceStorageUsage: rows purged
        self.assertFalse(ServiceStorageUsage.objects.filter(owner_id=owner).exists())


@override_settings(
    DRIVE_OFFBOARDING_CONNECT_TOKEN='test-connect-token',
    DRIVE_OFFBOARDING_CONNECT_API_URL='http://connect.test/api',
    DRIVE_OFFBOARDING_ALERT_OWNER_USERNAME='ryo',
    DRIVE_OFFBOARDING_STALE_HOURS=36,
)
class VEN153FailLoudTests(TestCase):
    def test_failed_run_pages_named_owner_and_records_liveness(self):
        """A fail-closed storage retry is observable and DMs Drive's named owner."""
        from drive.tasks import process_expired_deletion_requests

        owner = 'fail-loud-user'
        f = _make_file(
            owner_id=owner, name='blocked.txt', mime_type='text/plain',
            size=4, content=b'data',
        )
        DeletionRequest.objects.create(
            owner_id=owner,
            grace_expires_at=timezone.now() - timedelta(days=1),
        )
        with (
            patch.object(f.file.storage, 'delete', side_effect=OSError('storage unavailable')),
            patch('requests.post') as post,
        ):
            post.return_value.status_code = 200
            result = process_expired_deletion_requests()

        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['expired_pending'], 1)
        self.assertEqual(result['alert_status'], 'sent:ryo')
        self.assertEqual(post.call_args.kwargs['json']['recipient_username'], 'ryo')
        state = DeletionProcessorState.objects.get(pk=DeletionProcessorState.SINGLETON_PK)
        self.assertIsNotNone(state.last_started_at)
        self.assertIsNotNone(state.last_failure_at)
        self.assertIsNone(state.last_success_at)
        self.assertEqual(state.consecutive_failures, 1)
        self.assertEqual(state.expired_pending_count, 1)
        self.assertEqual(state.last_alert_status, 'sent:ryo')
        status_response = offboarding_statusz(APIRequestFactory().get('/api/offboarding-statusz/'))
        status_body = json.loads(status_response.content)
        self.assertEqual(status_body['mechanism'], 'drive-offboarding-deletion')
        self.assertEqual(status_body['expired_pending_count'], 1)
        self.assertEqual(status_body['consecutive_failures'], 1)
        metrics_response = offboarding_statusz(
            APIRequestFactory().get('/api/offboarding-statusz/?export=prometheus')
        )
        self.assertIn(
            b'drive_offboarding_processor_consecutive_failures 1',
            metrics_response.content,
        )
        self.assertIn(
            b'drive_offboarding_expired_pending_count 1',
            metrics_response.content,
        )

    def test_stalled_invariant_probe_pages_owner_and_telemetry_exposes_state(self):
        """An expired queue with no successful run is both probed and scrape-visible."""
        from drive.tasks import probe_expired_deletion_requests

        DeletionRequest.objects.create(
            owner_id='stalled-user',
            grace_expires_at=timezone.now() - timedelta(days=2),
        )
        with patch('requests.post') as post:
            post.return_value.status_code = 200
            result = probe_expired_deletion_requests()

        self.assertFalse(result['healthy'])
        self.assertEqual(result['expired_pending'], 1)
        self.assertEqual(post.call_args.kwargs['json']['recipient_username'], 'ryo')
        state = DeletionProcessorState.objects.get(pk=DeletionProcessorState.SINGLETON_PK)
        self.assertIsNotNone(state.last_probe_at)

        factory = APIRequestFactory()
        request = factory.get('/api/drive/internal/telemetry/')
        force_authenticate(request, user=_U(uid='staff', is_staff=True))
        response = DriveTelemetryView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['offboarding_expired_pending_count'], 1)
        self.assertEqual(response.data['offboarding_processor_last_success_timestamp'], 0)
        self.assertEqual(response.data['offboarding_processor_consecutive_failures'], 0)
