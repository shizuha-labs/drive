"""DRV-42: soft-delete trash, restore window, and privileged purge."""
from datetime import timedelta
import shutil
import tempfile
from types import SimpleNamespace

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from drive.models import File, FileVersion
from drive.tasks import purge_expired_trash
from drive.views import FileViewSet


class _User(SimpleNamespace):
    @property
    def is_authenticated(self):
        return True


def _auth(request, *, user_id=1, is_staff=False):
    force_authenticate(
        request,
        user=_User(id=user_id, pk=user_id, email=f'u{user_id}@example.com', is_staff=is_staff),
    )
    return request


def _rows(response):
    if isinstance(response.data, dict) and 'results' in response.data:
        return response.data['results']
    return response.data


class DriveTrashRestoreTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.media_root = tempfile.mkdtemp(prefix='drive-drv42-media-')
        self._media_override = override_settings(MEDIA_ROOT=self.media_root)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.addCleanup(lambda: shutil.rmtree(self.media_root, ignore_errors=True))

    def _file(self, name='doc.txt', *, owner_id='1', body=b'hello'):
        file_obj = File(
            owner_id=owner_id,
            name=name,
            mime_type='text/plain',
            size=len(body),
        )
        file_obj.file.save(name, ContentFile(body), save=True)
        return file_obj

    @override_settings(DRIVE_TRASH_RESTORE_WINDOW_DAYS=30)
    def test_delete_lists_in_trash_then_restore_lifecycle(self):
        file_obj = self._file()

        destroy = FileViewSet.as_view({'delete': 'destroy'})
        response = destroy(_auth(self.factory.delete(f'/files/{file_obj.id}/')), pk=str(file_obj.id))
        self.assertEqual(response.status_code, 204)
        file_obj.refresh_from_db()
        self.assertTrue(file_obj.is_trashed)
        self.assertIsNotNone(file_obj.deleted_at)
        self.assertEqual(file_obj.deleted_by, '1')
        self.assertGreater(file_obj.restore_until, timezone.now())

        list_view = FileViewSet.as_view({'get': 'list'})
        active = list_view(_auth(self.factory.get('/files/')))
        trash = list_view(_auth(self.factory.get('/files/?trash=true', {'trash': 'true'})))

        self.assertEqual(active.status_code, 200)
        self.assertEqual([row['id'] for row in _rows(active)], [])
        self.assertEqual(trash.status_code, 200)
        trash_rows = _rows(trash)
        self.assertEqual([row['id'] for row in trash_rows], [str(file_obj.id)])
        self.assertIn('restore_until', trash_rows[0])

        restore = FileViewSet.as_view({'post': 'restore'})
        response = restore(_auth(self.factory.post(f'/files/{file_obj.id}/restore/')), pk=str(file_obj.id))
        self.assertEqual(response.status_code, 200)
        file_obj.refresh_from_db()
        self.assertFalse(file_obj.is_trashed)
        self.assertIsNone(file_obj.deleted_at)
        self.assertEqual(file_obj.deleted_by, '')
        self.assertIsNone(file_obj.restore_until)

    def test_restore_after_expiry_returns_clear_error(self):
        file_obj = self._file()
        file_obj.is_trashed = True
        file_obj.trashed_at = timezone.now() - timedelta(days=31)
        file_obj.deleted_at = file_obj.trashed_at
        file_obj.deleted_by = '1'
        file_obj.restore_until = timezone.now() - timedelta(seconds=1)
        file_obj.save(update_fields=['is_trashed', 'trashed_at', 'deleted_at', 'deleted_by', 'restore_until'])

        restore = FileViewSet.as_view({'post': 'restore'})
        response = restore(_auth(self.factory.post(f'/files/{file_obj.id}/restore/')), pk=str(file_obj.id))

        self.assertEqual(response.status_code, 400)
        self.assertIn('expired', str(response.data).lower())
        file_obj.refresh_from_db()
        self.assertTrue(file_obj.is_trashed)

    @override_settings(DRIVE_TRASH_RESTORE_WINDOW_DAYS=30)
    def test_purge_expired_trash_deletes_rows_versions_and_storage(self):
        file_obj = self._file(body=b'current')
        FileVersion.objects.create(
            file=file_obj,
            version_number=1,
            file_data=ContentFile(b'old-version', name='versions/old.txt'),
            size=11,
            checksum_md5='',
            created_by_id='1',
        )
        file_path = file_obj.file.name
        version_path = file_obj.versions.get().file_data.name
        storage = file_obj.file.storage
        self.assertTrue(storage.exists(file_path))
        self.assertTrue(storage.exists(version_path))

        file_obj.is_trashed = True
        file_obj.trashed_at = timezone.now() - timedelta(days=31)
        file_obj.deleted_at = file_obj.trashed_at
        file_obj.deleted_by = '1'
        file_obj.restore_until = timezone.now() - timedelta(seconds=1)
        file_obj.save(update_fields=['is_trashed', 'trashed_at', 'deleted_at', 'deleted_by', 'restore_until'])

        result = purge_expired_trash()

        self.assertEqual(result['purged_files'], 1)
        self.assertFalse(File.objects.filter(pk=file_obj.pk).exists())
        self.assertFalse(FileVersion.objects.filter(file_id=file_obj.pk).exists())
        self.assertFalse(storage.exists(file_path))
        self.assertFalse(storage.exists(version_path))

    def test_privileged_purge_is_staff_only(self):
        file_obj = self._file()
        file_obj.move_to_trash(deleted_by='1')

        purge = FileViewSet.as_view({'post': 'purge'})
        denied = purge(_auth(self.factory.post(f'/files/{file_obj.id}/purge/')), pk=str(file_obj.id))
        allowed = purge(_auth(self.factory.post(f'/files/{file_obj.id}/purge/'), is_staff=True), pk=str(file_obj.id))

        self.assertEqual(denied.status_code, 403)
        self.assertEqual(allowed.status_code, 200)
        self.assertFalse(File.objects.filter(pk=file_obj.pk).exists())
