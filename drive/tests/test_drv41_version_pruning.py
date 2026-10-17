"""DRV-41: version-history retention pruning."""
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from drive.models import Activity, File, FileVersion
from drive.tasks import prune_file_versions, prune_versions_for_file

User = get_user_model()


def _mk_file(owner, version=6, name='doc.txt'):
    return File.objects.create(
        name=name,
        owner_id=str(owner.id),
        file=SimpleUploadedFile(name, b'current', content_type='text/plain'),
        mime_type='text/plain',
        size=7,
        file_type='document',
        version=version,
    )


def _mk_version(file_obj, version_number, created_at=None, storage_name=None):
    version = FileVersion.objects.create(
        file=file_obj,
        version_number=version_number,
        file_data=storage_name or SimpleUploadedFile(f'v{version_number}.txt', f'v{version_number}'.encode(), content_type='text/plain'),
        size=version_number,
        checksum_md5='',
        created_by_id=file_obj.owner_id,
    )
    if created_at is not None:
        FileVersion.objects.filter(id=version.id).update(created_at=created_at)
        version.refresh_from_db()
    return version


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), DRIVE_VERSION_RETENTION_COUNT=2)
class VersionPruningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='drv41', email='drv41@shizuha.com', password='x')
        self.file = _mk_file(self.user, version=6)
        self.base_time = timezone.now() - timedelta(days=10)

    def _versions(self):
        return list(self.file.versions.order_by('version_number').values_list('version_number', flat=True))

    def test_at_limit_noop(self):
        _mk_version(self.file, 4, self.base_time)
        _mk_version(self.file, 5, self.base_time + timedelta(minutes=1))
        result = prune_versions_for_file(self.file, retention_count=2)
        self.assertEqual(result['pruned'], 0)
        self.assertEqual(self._versions(), [4, 5])
        self.assertFalse(Activity.objects.filter(details__event='version_pruned').exists())

    def test_below_limit_noop(self):
        _mk_version(self.file, 5, self.base_time)
        result = prune_versions_for_file(self.file, retention_count=2)
        self.assertEqual(result['pruned'], 0)
        self.assertEqual(self._versions(), [5])

    def test_prunes_oldest_versions_and_is_idempotent(self):
        for n in range(1, 6):
            _mk_version(self.file, n, self.base_time + timedelta(minutes=n))

        first = prune_versions_for_file(self.file, retention_count=2)
        second = prune_versions_for_file(self.file, retention_count=2)

        self.assertEqual(first['pruned'], 3)
        self.assertEqual(second['pruned'], 0)
        self.assertEqual(self._versions(), [4, 5])
        self.assertEqual(Activity.objects.filter(details__event='version_pruned').count(), 3)

    def test_current_version_row_is_never_pruned(self):
        for n in range(1, 6):
            _mk_version(self.file, n, self.base_time + timedelta(minutes=n))
        # Defensive guard: even if a FileVersion row exists for the live current
        # File.version, it must not be pruned as historical overflow.
        _mk_version(self.file, 6, self.base_time)

        result = prune_versions_for_file(self.file, retention_count=1)

        self.assertEqual(result['pruned'], 4)
        self.assertEqual(self._versions(), [5, 6])

    def test_storage_blob_not_deleted_when_another_version_references_it(self):
        shared = 'versions/shared-object.bin'
        _mk_version(self.file, 1, self.base_time, storage_name=shared)
        _mk_version(self.file, 2, self.base_time + timedelta(minutes=1), storage_name=shared)
        _mk_version(self.file, 3, self.base_time + timedelta(minutes=2))
        storage = FileVersion._meta.get_field('file_data').storage

        with mock.patch.object(storage, 'delete') as delete_mock:
            result = prune_versions_for_file(self.file, retention_count=2)

        self.assertEqual(result['pruned'], 1)
        delete_mock.assert_not_called()
        self.assertEqual(self._versions(), [2, 3])

    def test_task_prunes_all_files(self):
        other = _mk_file(self.user, version=4, name='other.txt')
        for n in range(1, 6):
            _mk_version(self.file, n, self.base_time + timedelta(minutes=n))
        for n in range(1, 4):
            _mk_version(other, n, self.base_time + timedelta(minutes=n))

        result = prune_file_versions.run(retention_count=2)

        self.assertEqual(result['files_seen'], 2)
        self.assertEqual(result['versions_pruned'], 4)
        self.assertEqual(self._versions(), [4, 5])
        self.assertEqual(list(other.versions.order_by('version_number').values_list('version_number', flat=True)), [2, 3])
