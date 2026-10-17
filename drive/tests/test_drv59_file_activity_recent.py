"""DRV-59: FileActivity-backed Recent and throttled access hooks."""
import os
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from drive.models import File, FileActivity, FileType, Share, SharePermission, ShareType, StorageQuota
from drive.visibility import visible_file_activity

User = get_user_model()
MEDIA_ROOT = tempfile.mkdtemp(prefix='drive-drv59-media-')


@override_settings(MEDIA_ROOT=MEDIA_ROOT, DRIVE_FILE_ACTIVITY_INLINE=True)
class FileActivityRecentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='drv59-owner', email='owner59@example.com', password='x')
        self.recipient = User.objects.create_user(username='drv59-recipient', email='recipient59@example.com', password='x')
        self.client.force_authenticate(user=self.owner)
        StorageQuota.objects.get_or_create(owner_id=self.owner.id, defaults={'quota_bytes': 10**9})
        StorageQuota.objects.get_or_create(owner_id=self.recipient.id, defaults={'quota_bytes': 10**9})

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def _file(self, owner, name, body=b'body'):
        return File.objects.create(
            name=name,
            owner_id=str(owner.id),
            file=SimpleUploadedFile(name, body, content_type='text/plain'),
            file_type=FileType.DOCUMENT,
            mime_type='text/plain',
            size=len(body),
            extension='txt',
        )

    def _recent_ids(self, user=None):
        if user is not None:
            self.client.force_authenticate(user=user)
        response = self.client.get('/api/files/recent/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['X-Drive-Recent-Activity-Retention-Days'], '90')
        self.assertEqual(response['X-Drive-Recent-Fallback-Order'], 'updated_at')
        return [item['id'] for item in response.data]

    def _shared_search_ids(self, user, q):
        """File ids visible to `user` on the explicit shared search scope
        (the surface where PLAT-8918 moved shared-item discoverability)."""
        self.client.force_authenticate(user=user)
        response = self.client.get('/api/files/search/', {'q': q, 'scope': 'shared'})
        self.assertEqual(response.status_code, 200, response.content)
        return [row['id'] for row in response.data['results']]

    def test_fileactivity_model_collapses_repeated_open_within_ten_minutes(self):
        file_obj = self._file(self.owner, 'collapse.txt')
        first, created = FileActivity.record(
            user_id=self.owner.id,
            action=FileActivity.ACTION_OPEN,
            file=file_obj,
            at=timezone.now(),
        )
        self.assertTrue(created)
        second, created = FileActivity.record(
            user_id=self.owner.id,
            action=FileActivity.ACTION_OPEN,
            file=file_obj,
            at=timezone.now() + timedelta(minutes=5),
        )
        self.assertFalse(created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(FileActivity.objects.count(), 1)

    def test_recent_orders_by_latest_distinct_visible_activity(self):
        older = self._file(self.owner, 'older.txt')
        newest = self._file(self.owner, 'newest.txt')
        now = timezone.now()
        FileActivity.objects.create(user_id=str(self.owner.id), file=newest, action=FileActivity.ACTION_OPEN, created_at=now - timedelta(hours=3))
        FileActivity.objects.create(user_id=str(self.owner.id), file=older, action=FileActivity.ACTION_PREVIEW, created_at=now - timedelta(hours=2))
        FileActivity.objects.create(user_id=str(self.owner.id), file=newest, action=FileActivity.ACTION_DOWNLOAD, created_at=now - timedelta(minutes=1))

        self.assertEqual(self._recent_ids()[:2], [str(newest.id), str(older.id)])


    def test_recent_rejects_malformed_and_non_positive_limit(self):
        malformed = self.client.get('/api/files/recent/?limit=abc')
        zero = self.client.get('/api/files/recent/?limit=0')
        negative = self.client.get('/api/files/recent/?limit=-5')

        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(zero.status_code, 400)
        self.assertEqual(negative.status_code, 400)
        self.assertEqual(malformed.data['detail'], 'limit must be a positive integer')

    def test_recent_deduplicates_activity_before_limit_and_fallback(self):
        hot = self._file(self.owner, 'hot.txt')
        older_activity = self._file(self.owner, 'older-activity.txt')
        no_activity = self._file(self.owner, 'no-activity.txt')
        now = timezone.now()
        File.objects.filter(pk=no_activity.pk).update(updated_at=now + timedelta(minutes=5))

        FileActivity.objects.bulk_create([
            FileActivity(
                user_id=str(self.owner.id),
                file=hot,
                action=FileActivity.ACTION_OPEN,
                created_at=now - timedelta(seconds=i),
            )
            for i in range(100)
        ])
        FileActivity.objects.create(
            user_id=str(self.owner.id),
            file=older_activity,
            action=FileActivity.ACTION_PREVIEW,
            created_at=now - timedelta(hours=2),
        )

        response = self.client.get('/api/files/recent/?limit=2')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([item['id'] for item in response.data], [str(hot.id), str(older_activity.id)])
        self.assertNotIn(str(no_activity.id), [item['id'] for item in response.data])

    def test_recent_filters_revoked_share_at_query_time_without_deleting_activity(self):
        shared = self._file(self.owner, 'shared.txt')
        share = Share.objects.create(
            file=shared,
            share_type=ShareType.USER,
            permission=SharePermission.VIEW,
            shared_with_id=str(self.recipient.id),
            created_by_id=str(self.owner.id),
        )
        activity = FileActivity.objects.create(
            user_id=str(self.recipient.id),
            file=shared,
            action=FileActivity.ACTION_OPEN,
        )

        self.assertIn(activity, visible_file_activity(self.recipient))
        # PLAT-8918 (DRV-181): the recent leg is OWNERSHIP-scoped, so shared
        # items surface on the explicit shared scope instead — assert grantee
        # visibility there, pre-revocation.
        self.assertIn(str(shared.id), self._shared_search_ids(self.recipient, 'shared'))

        share.is_active = False
        share.revoked_at = timezone.now()
        share.save(update_fields=['is_active', 'revoked_at'])

        self.assertNotIn(activity, visible_file_activity(self.recipient))
        # Revocation filters at query time on the shared surface too — the
        # activity row itself is retained (asserted below), only visibility
        # flips.
        self.assertNotIn(str(shared.id), self._shared_search_ids(self.recipient, 'shared'))
        self.assertTrue(FileActivity.objects.filter(pk=activity.pk).exists())

    def test_recent_retention_boundary_falls_back_to_updated_at_order(self):
        old_activity_file = self._file(self.owner, 'old-activity.txt')
        no_activity_file = self._file(self.owner, 'no-activity.txt')
        FileActivity.objects.create(
            user_id=str(self.owner.id),
            file=old_activity_file,
            action=FileActivity.ACTION_OPEN,
            created_at=timezone.now() - timedelta(days=91),
        )
        # Both files remain visible even though retained FileActivity no longer
        # drives ordering; the endpoint appends visible files by updated_at.
        ids = self._recent_ids()
        self.assertIn(str(old_activity_file.id), ids)
        self.assertIn(str(no_activity_file.id), ids)

    def test_open_preview_download_and_edit_hooks_write_throttled_recent_rows(self):
        file_obj = self._file(self.owner, 'hooks.txt', body=b'hook bytes')

        response = self.client.get(f'/api/files/{file_obj.id}/')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data['recent_activity']['recorded_action'], FileActivity.ACTION_OPEN)
        self.assertLessEqual(response.data['recent_activity']['enqueue_ms'], 5)
        self.assertEqual(FileActivity.objects.filter(file=file_obj, action=FileActivity.ACTION_OPEN).count(), 1)

        # The second open is admitted by the endpoint but collapsed by the cache/DB.
        self.client.get(f'/api/files/{file_obj.id}/')
        self.assertEqual(FileActivity.objects.filter(file=file_obj, action=FileActivity.ACTION_OPEN).count(), 1)

        response = self.client.get(f'/api/files/{file_obj.id}/preview/')
        self.assertEqual(response.status_code, 200, response.content)
        response = self.client.get(f'/api/files/{file_obj.id}/download/')
        self.assertEqual(response.status_code, 200, getattr(response, 'content', b''))
        b''.join(response.streaming_content)
        response = self.client.patch(
            f'/api/files/{file_obj.id}/',
            {'is_starred': True},
            format='multipart',
        )
        self.assertEqual(response.status_code, 200, response.content)

        actions = set(FileActivity.objects.filter(file=file_obj).values_list('action', flat=True))
        self.assertTrue({
            FileActivity.ACTION_OPEN,
            FileActivity.ACTION_PREVIEW,
            FileActivity.ACTION_DOWNLOAD,
            FileActivity.ACTION_EDIT,
        }.issubset(actions))
