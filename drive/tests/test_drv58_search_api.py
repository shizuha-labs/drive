"""DRV-58: unified Drive search API."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from drive.models import File, FileActivity, FileType, Folder, Share, SharePermission, ShareType

User = get_user_model()


class DriveSearchApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username='owner', email='owner@example.com', password='x')
        self.other = User.objects.create_user(username='other', email='other@example.com', password='x')
        self.org_user = User.objects.create_user(username='org', email='org@example.com', password='x')
        self.org_user.organization_id = 'org-1'
        self.client.force_authenticate(self.owner)
        self.root = Folder.objects.create(owner_id=str(self.owner.id), name='Projects', description='alpha folder')
        self.child = Folder.objects.create(owner_id=str(self.owner.id), name='Invoices', parent=self.root)
        self.alpha = self._file(
            self.owner,
            'alpha-report.txt',
            folder=self.root,
            content_text='quarterly alpha revenue plan',
            mime_type='text/plain',
            file_type=FileType.DOCUMENT,
            size=120,
        )
        self.image = self._file(
            self.owner,
            'budget-screenshot.png',
            folder=self.root,
            content_text=None,
            mime_type='image/png',
            file_type=FileType.IMAGE,
            size=2048,
        )
        self.hidden = self._file(
            self.other,
            'alpha-secret.txt',
            content_text='alpha secret from other user',
        )

    def _file(self, user, name, **kwargs):
        return File.objects.create(
            owner_id=str(user.id),
            name=name,
            mime_type=kwargs.pop('mime_type', 'text/plain'),
            size=kwargs.pop('size', 10),
            file_type=kwargs.pop('file_type', FileType.DOCUMENT),
            **kwargs,
        )

    def search(self, **params):
        return self.client.get('/api/files/search/', params)

    def test_q_filters_entity_and_snippets_are_visible_only(self):
        resp = self.search(q='alpha', scope='all', entity='both', page_size=10)
        self.assertEqual(resp.status_code, 200, resp.content)
        names = {row['name'] for row in resp.data['results']}
        self.assertIn('alpha-report.txt', names)
        self.assertIn('Projects', names)
        self.assertNotIn('alpha-secret.txt', names)
        alpha_row = next(row for row in resp.data['results'] if row['name'] == 'alpha-report.txt')
        self.assertEqual(alpha_row['kind'], 'file')
        self.assertIn('<mark>alpha</mark>', alpha_row['snippet'].lower())
        self.assertIn(alpha_row['match_reason'], {'name', 'content'})

        files_only = self.search(q='alpha', entity='file')
        self.assertEqual({row['kind'] for row in files_only.data['results']}, {'file'})

    def test_scope_shared_and_revoked_share_do_not_leak(self):
        shared = self._file(self.owner, 'shared-plan.txt', content_text='shared alpha plan')
        Share.objects.create(
            file=shared,
            share_type=ShareType.USER,
            permission=SharePermission.VIEW,
            shared_with_id=str(self.other.id),
            created_by_id=str(self.owner.id),
        )
        self.client.force_authenticate(self.other)
        resp = self.search(q='shared', scope='shared')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual([row['name'] for row in resp.data['results']], ['shared-plan.txt'])
        self.assertTrue(resp.data['results'][0]['is_shared'])

        share = Share.objects.get(file=shared)
        share.is_active = False
        share.revoked_at = timezone.now()
        share.save(update_fields=['is_active', 'revoked_at'])
        resp = self.search(q='shared', scope='shared')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data['results'], [])

    def test_org_share_scope_uses_visibility_service(self):
        shared = self._file(self.owner, 'org-alpha.txt', content_text='org alpha visible')
        Share.objects.create(
            file=shared,
            share_type=ShareType.ORGANIZATION,
            permission=SharePermission.VIEW,
            organization_id='org-1',
            created_by_id=str(self.owner.id),
        )
        self.client.force_authenticate(self.org_user)
        resp = self.search(q='org alpha', scope='shared')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual([row['name'] for row in resp.data['results']], ['org-alpha.txt'])

    def test_filters_sort_cursor_and_concurrent_insert_are_stable(self):
        self._file(self.owner, 'bravo.txt', content_text='sortable', size=200)
        self._file(self.owner, 'charlie.txt', content_text='sortable', size=300)
        first = self.search(q='sortable', sort='name', page_size=1)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.data['results'][0]['name'], 'bravo.txt')
        cursor = first.data['next_cursor']
        self.assertTrue(cursor)

        # A concurrent insert before the first page's cursor must not duplicate
        # already-returned rows on the next keyset page.
        self._file(self.owner, 'aardvark.txt', content_text='sortable', size=50)
        second = self.search(q='sortable', sort='name', page_size=2, cursor=cursor)
        self.assertEqual(second.status_code, 200, second.content)
        names = [row['name'] for row in second.data['results']]
        self.assertIn('charlie.txt', names)
        self.assertNotIn('bravo.txt', names)

    def test_sort_name_is_not_limited_by_updated_at_candidate_window(self):
        old_first = self._file(self.owner, 'aardvark.txt', content_text='needle')
        File.objects.filter(pk=old_first.pk).update(updated_at=timezone.now() - timedelta(days=30))
        for index in range(120):
            self._file(self.owner, f'z{index:03d}.txt', content_text='needle')

        resp = self.search(q='needle', sort='name', entity='file', page_size=50)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data['results'][0]['name'], 'aardvark.txt')


    def test_cursor_pages_apply_database_limit_after_keyset_predicate(self):
        for index in range(30):
            self._file(self.owner, f'z{index:03d}.txt', content_text='needle')

        first = self.search(q='needle', sort='name', entity='file', page_size=5)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual([row['name'] for row in first.data['results']], [f'z{index:03d}.txt' for index in range(5)])

        with CaptureQueriesContext(connection) as queries:
            second = self.search(q='needle', sort='name', entity='file', page_size=5, cursor=first.data['next_cursor'])
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual([row['name'] for row in second.data['results']], [f'z{index:03d}.txt' for index in range(5, 10)])
        file_selects = [
            query['sql']
            for query in queries
            if 'FROM "drive_file"' in query['sql'] and 'COUNT(' not in query['sql']
        ]
        self.assertTrue(file_selects, [query['sql'] for query in queries])
        self.assertTrue(any('LIMIT 6' in query for query in file_selects), file_selects)
        self.assertFalse([query for query in file_selects if 'LIMIT' not in query], file_selects)

    def test_sort_recent_uses_caller_file_activity(self):
        old_active = self._file(self.owner, 'old-active.txt', content_text='recent')
        new_idle = self._file(self.owner, 'new-idle.txt', content_text='recent')
        File.objects.filter(pk=old_active.pk).update(updated_at=timezone.now() - timedelta(days=10))
        FileActivity.objects.create(
            user_id=str(self.owner.id),
            file=old_active,
            action=FileActivity.ACTION_OPEN,
            created_at=timezone.now(),
        )

        resp = self.search(q='recent', sort='recent', entity='file', page_size=10)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data['results'][0]['name'], 'old-active.txt')
        self.assertEqual(resp.data['results'][1]['name'], 'new-idle.txt')

    def test_filters_and_unindexed_signal(self):
        resp = self.search(entity='file', file_type=FileType.IMAGE, mime='image/png', size_min=1000)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual([row['name'] for row in resp.data['results']], ['budget-screenshot.png'])
        self.assertTrue(resp.data['has_unindexed_files'])
        self.assertEqual(resp.data['unindexed_count'], 1)

        csv_or = self.search(entity='file', file_type=f'{FileType.IMAGE},{FileType.DOCUMENT}', mime='image/png,text/plain')
        self.assertEqual(csv_or.status_code, 200, csv_or.content)
        names = {row['name'] for row in csv_or.data['results']}
        self.assertIn('budget-screenshot.png', names)
        self.assertIn('alpha-report.txt', names)

    def test_unauthorized_owner_and_folder_filters_return_empty(self):
        other_folder = Folder.objects.create(owner_id=str(self.other.id), name='Private')
        resp = self.search(q='alpha', owner_id=str(self.other.id))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data['results'], [])
        resp = self.search(q='alpha', folder_id=str(other_folder.id))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data['results'], [])

    def test_trash_excluded_by_default(self):
        trashed = self._file(self.owner, 'trashed-alpha.txt', content_text='alpha')
        trashed.is_trashed = True
        trashed.trashed_at = timezone.now() - timedelta(days=1)
        trashed.save(update_fields=['is_trashed', 'trashed_at'])
        resp = self.search(q='trashed')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.data['results'], [])
        resp = self.search(q='trashed', trashed='true')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual([row['name'] for row in resp.data['results']], ['trashed-alpha.txt'])

    def test_invalid_sort_and_cursor_sort_mismatch_are_rejected(self):
        resp = self.search(sort='random')
        self.assertEqual(resp.status_code, 400, resp.content)
        first = self.search(q='alpha', sort='name', page_size=1)
        self.assertEqual(first.status_code, 200, first.content)
        resp = self.search(q='alpha', sort='updated_desc', cursor=first.data['next_cursor'])
        self.assertEqual(resp.status_code, 400, resp.content)
