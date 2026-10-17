"""DRV-38: mutating Drive operations emit canonical activity/audit events."""
import hashlib
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from drive.models import Activity, File, Folder, SharePermission, ShareType, StorageQuota

User = get_user_model()
DRV38_MEDIA_ROOT = tempfile.mkdtemp(prefix='drive-drv38-media-')


@override_settings(MEDIA_ROOT=DRV38_MEDIA_ROOT)
class DriveActivityInstrumentationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='nagi-drv38', email='drv38@example.com', password='x')
        self.client.force_authenticate(user=self.user)
        StorageQuota.objects.get_or_create(owner_id=self.user.id, defaults={'quota_bytes': 10**9})
        self.process_patch = patch('drive.views.process_file_upload.delay')
        self.process_patch.start()
        self.addCleanup(self.process_patch.stop)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(DRV38_MEDIA_ROOT, ignore_errors=True)

    def _upload(self, *, name='audit.txt', body=b'audit bytes'):
        response = self.client.post(
            '/api/files/',
            {'file': SimpleUploadedFile(name, body, content_type='text/plain')},
            format='multipart',
        )
        self.assertEqual(response.status_code, 201, response.content)
        return File.objects.get(id=response.data['id'])

    def test_upload_move_download_share_delete_restore_write_canonical_events(self):
        body = b'audit bytes'
        file_obj = self._upload(body=body)

        create_event = Activity.objects.get(file=file_obj, event_type=Activity.EVENT_CREATE)
        self.assertEqual(create_event.actor_id, str(self.user.id))
        self.assertEqual(create_event.resource_id, file_obj.id)
        self.assertEqual(create_event.metadata['file_id'], str(file_obj.id))
        self.assertEqual(create_event.metadata['name'], file_obj.name)
        self.assertEqual(create_event.metadata['size'], len(body))
        self.assertEqual(create_event.metadata['checksum'], hashlib.sha256(body).hexdigest())
        self.assertIsNotNone(create_event.timestamp)

        folder = Folder.objects.create(name='Destination', owner_id=str(self.user.id))
        response = self.client.post(f'/api/files/{file_obj.id}/move/', {'folder': str(folder.id)}, format='multipart')
        self.assertEqual(response.status_code, 200, response.content)
        move_event = Activity.objects.filter(file=file_obj, event_type=Activity.EVENT_MOVE).latest('timestamp')
        self.assertEqual(move_event.actor_id, str(self.user.id))
        self.assertIsNone(move_event.metadata['from_parent_id'])
        self.assertEqual(move_event.metadata['to_parent_id'], str(folder.id))

        response = self.client.get(f'/api/files/{file_obj.id}/download/', HTTP_RANGE='bytes=0-4')
        self.assertEqual(response.status_code, 206, getattr(response, 'content', b''))
        download_event = Activity.objects.filter(file=file_obj, event_type=Activity.EVENT_DOWNLOAD).latest('timestamp')
        self.assertEqual(download_event.metadata['byte_range'], [0, 4])
        self.assertEqual(download_event.metadata['integrity_status'], 'partial_range')

        response = self.client.post(
            '/api/shares/',
            {
                'file': str(file_obj.id),
                'share_type': ShareType.LINK,
                'permission': SharePermission.EDIT,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        share_event = Activity.objects.filter(file=file_obj, event_type=Activity.EVENT_SHARE).latest('timestamp')
        self.assertEqual(share_event.metadata['role'], SharePermission.EDIT)
        self.assertIsNotNone(share_event.metadata['link_token'])
        self.assertIsNone(share_event.metadata['shared_with_id'])

        response = self.client.delete(f'/api/files/{file_obj.id}/')
        self.assertEqual(response.status_code, 204, response.content)
        soft_delete_event = Activity.objects.filter(file=file_obj, event_type=Activity.EVENT_DELETE).latest('timestamp')
        self.assertTrue(soft_delete_event.metadata['soft'])
        self.assertFalse(soft_delete_event.metadata['hard'])

        response = self.client.post(f'/api/files/{file_obj.id}/restore/')
        self.assertEqual(response.status_code, 200, response.content)
        restore_event = Activity.objects.filter(file=file_obj, event_type=Activity.EVENT_RESTORE).latest('timestamp')
        self.assertEqual(restore_event.actor_id, str(self.user.id))
        self.assertEqual(restore_event.resource_id, file_obj.id)

    def test_machine_move_accepts_json(self):
        """The MCP transport sends structured move writes as JSON."""
        file_obj = self._upload(name='machine-write.txt')
        folder = Folder.objects.create(
            name='Machine destination',
            owner_id=str(self.user.id),
        )

        moved = self.client.post(
            f'/api/files/{file_obj.id}/move/',
            {'folder': str(folder.id)},
            format='json',
        )
        self.assertEqual(moved.status_code, 200, moved.content)
        file_obj.refresh_from_db()
        self.assertEqual(file_obj.folder_id, folder.id)

    def test_hard_purge_snapshots_delete_audit_before_file_row_is_removed(self):
        staff = User.objects.create_user(username='drv38-staff', email='drv38-staff@example.com', password='x', is_staff=True)
        self.client.force_authenticate(user=staff)
        file_obj = File(
            owner_id=str(staff.id),
            name='purge.txt',
            mime_type='text/plain',
            size=5,
        )
        file_obj.file.save('purge.txt', ContentFile(b'purge'), save=True)
        file_obj.move_to_trash(deleted_by=staff.id)

        response = self.client.post(f'/api/files/{file_obj.id}/purge/')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(File.objects.filter(id=file_obj.id).exists())
        hard_delete_event = Activity.objects.get(resource_id=file_obj.id, event_type=Activity.EVENT_DELETE)
        self.assertEqual(hard_delete_event.actor_id, str(staff.id))
        self.assertEqual(hard_delete_event.resource_name, 'purge.txt')
        self.assertFalse(hard_delete_event.metadata['soft'])
        self.assertTrue(hard_delete_event.metadata['hard'])

    def test_unauthenticated_mutation_fails_without_null_actor_activity(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(
            '/api/files/',
            {'file': SimpleUploadedFile('anon.txt', b'anon bytes', content_type='text/plain')},
            format='multipart',
        )

        self.assertGreaterEqual(response.status_code, 400)
        self.assertFalse(Activity.objects.filter(actor_id='').exists())
        self.assertEqual(Activity.objects.count(), 0)

    def test_mutation_rolls_back_when_audit_write_fails(self):
        """DRV-38 AC1 (PR #33 review P1): the mutation and its audit row are one
        transaction — if Activity.record_event raises, the file/share/folder
        mutation must roll back rather than commit unaudited state."""
        boom = patch(
            'drive.views.Activity.record_event',
            side_effect=RuntimeError('audit store down'),
        )

        # Upload (file create): no file row may survive the failed audit.
        # FileViewSet.create converts write failures into a structured error
        # response instead of raising, so assert on the status code.
        with boom:
            response = self.client.post(
                '/api/files/',
                {'file': SimpleUploadedFile('rollback.txt', b'rollback audit bytes', content_type='text/plain')},
                format='multipart',
            )
        self.assertGreaterEqual(response.status_code, 500, getattr(response, 'content', b''))
        self.assertFalse(File.objects.filter(name='rollback.txt').exists())

        # Soft delete: the file must NOT be trashed if the audit write fails.
        file_obj = self._upload(name='keep.txt')
        with boom:
            with self.assertRaises(RuntimeError):
                self.client.delete(f'/api/files/{file_obj.id}/')
        file_obj.refresh_from_db()
        self.assertFalse(file_obj.is_trashed)

        # Move: the file must stay in its original folder.
        folder = Folder.objects.create(name='RollbackDest', owner_id=str(self.user.id))
        with boom:
            with self.assertRaises(RuntimeError):
                self.client.post(
                    f'/api/files/{file_obj.id}/move/',
                    {'folder': str(folder.id)}, format='multipart',
                )
        file_obj.refresh_from_db()
        self.assertIsNone(file_obj.folder_id)

        # Share create: no share row may survive the failed audit.
        from drive.models import Share
        with boom:
            with self.assertRaises(RuntimeError):
                self.client.post(
                    '/api/shares/',
                    {'file': str(file_obj.id), 'share_type': ShareType.LINK,
                     'permission': SharePermission.VIEW},
                    format='json',
                )
        self.assertFalse(Share.objects.filter(file=file_obj).exists())

        # Folder create: no folder row may survive the failed audit.
        with boom:
            with self.assertRaises(RuntimeError):
                self.client.post('/api/folders/', {'name': 'RollbackFolder'}, format='json')
        self.assertFalse(Folder.objects.filter(name='RollbackFolder').exists())

        # Restore: a trashed file must STAY trashed if the audit write fails.
        file_obj.move_to_trash(deleted_by=self.user.id)
        with boom:
            with self.assertRaises(RuntimeError):
                self.client.post(f'/api/files/{file_obj.id}/restore/')
        file_obj.refresh_from_db()
        self.assertTrue(file_obj.is_trashed)
