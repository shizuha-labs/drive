"""PLAT-8918 / DRV-181: the default file listing is OWNERSHIP-scoped.

The pre-fix default listing (`GET /api/files/` and the root/recent/starred
legs) conflated org membership with ownership — a caller owning 11 files saw
~10,355 items because `visible_files()` unions org-share grants into the
default surface. The remediation scopes every default listing leg to
`owner_id=user.id`; org/direct-shared items remain discoverable on the
explicit shared surfaces (search `?scope=shared`, `/api/shares/`), which
still route through the DRV-14 ACL module.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from drive.models import File, Folder, Share, SharePermission, ShareType


def _user(username, org=None):
    user = User.objects.create_user(username=username, email=f'{username}@example.com', password='x')
    if org:
        user.organization_id = org
    return user


class OwnershipScopedListingTests(TestCase):
    def setUp(self):
        self.owner = _user('owner', org='org-1')
        self.foreign = _user('foreign', org='org-1')  # same org — the leak class
        self.client_api = APIClient()
        self.client_api.force_authenticate(self.owner)

    def _file(self, owner, name, folder=None, **kwargs):
        return File.objects.create(
            name=name,
            owner_id=str(owner.id),
            folder=folder,
            file_type='document',
            mime_type='text/plain',
            size=1,
            is_starred=kwargs.get('is_starred', False),
            is_trashed=kwargs.get('is_trashed', False),
        )

    def _org_share(self, file_obj):
        return Share.objects.create(
            file=file_obj,
            share_type=ShareType.ORGANIZATION,
            permission=SharePermission.VIEW,
            organization_id='org-1',
            created_by_id=str(self.owner.id),
        )

    def test_default_listing_counts_only_owned_files(self):
        """The DRV-181 headline: owned count == listed count, org-shared
        foreign items absent from the default surface."""
        mine = [self._file(self.owner, f'mine-{i}.txt') for i in range(3)]
        for i in range(5):
            self._org_share(self._file(self.foreign, f'foreign-{i}.txt'))
        resp = self.client_api.get('/api/files/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], len(mine))
        names = {r['name'] for r in resp.data['results']}
        self.assertTrue(names.issuperset({f.name for f in mine}))
        self.assertFalse(any(n.startswith('foreign-') for n in names))

    def test_root_and_starred_legs_owned_only(self):
        self._file(self.owner, 'my-root.txt', is_starred=True)
        self._org_share(self._file(self.foreign, 'shared-star.txt', is_starred=True))
        root = self.client_api.get('/api/files/root/')
        # /api/files/root/ is paginated ({"count", "results"}) — same shape as
        # the list leg.
        self.assertEqual([r['name'] for r in root.data['results']], ['my-root.txt'])
        starred = self.client_api.get('/api/files/starred/')
        self.assertEqual([r['name'] for r in starred.data], ['my-root.txt'])

    def test_recent_leg_owned_only(self):
        self._file(self.owner, 'my-recent.txt')
        self._org_share(self._file(self.foreign, 'shared-recent.txt'))
        resp = self.client_api.get('/api/files/recent/')
        self.assertEqual(resp.status_code, 200)
        names = {r['name'] for r in resp.data}
        self.assertIn('my-recent.txt', names)
        self.assertNotIn('shared-recent.txt', names)

    def test_search_default_scope_excludes_foreign_even_with_match(self):
        """F1 'nonmatches absent' re-run against foreign IDs: a foreign file
        whose name matches the query is absent from the default (scope=mine)
        search, present on the explicit shared scope."""
        self._org_share(self._file(self.foreign, 'cross-org-secret.txt'))
        mine = self.client_api.get('/api/files/search/', {'q': 'cross-org-secret'})
        self.assertEqual(mine.status_code, 200)
        # The search surface answers in cursor shape: total_returned, not the
        # paginated list's "count".
        self.assertEqual(mine.data['total_returned'], 0)
        shared = self.client_api.get(
            '/api/files/search/', {'q': 'cross-org-secret', 'scope': 'shared'})
        self.assertEqual(shared.status_code, 200)
        self.assertEqual(shared.data['total_returned'], 1)

    def test_foreign_folder_param_lists_empty_not_children(self):
        """`?folder=<foreign uuid>` must not list a foreign folder's children —
        the owned-only base intersects the foreign folder_id to empty."""
        foreign_folder = Folder.objects.create(
            name='foreign-folder', owner_id=str(self.foreign.id))
        hidden = self._file(self.foreign, 'hidden-child.txt', folder=foreign_folder)
        self._org_share(hidden)  # even org-shared, the default surface stays owned
        resp = self.client_api.get('/api/files/', {'folder': str(foreign_folder.pk)})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 0)

    def test_foreign_unshared_download_not_discoverable(self):
        """A foreign unshared file never serves content: retrieve is
        share-gated (undiscoverable -> 404); download resolves through the
        same owner-scoped get_queryset, so it is likewise undiscoverable
        (404) rather than permission-denied (403). Both fail closed —
        never 200, and 404 does not even reveal the file's existence."""
        secret = self._file(self.foreign, 'secret.txt')
        detail = self.client_api.get(f'/api/files/{secret.id}/')
        self.assertEqual(detail.status_code, 404)
        download = self.client_api.get(f'/api/files/{secret.id}/download/')
        self.assertEqual(download.status_code, 404)

    def test_trash_leg_still_scoped_and_restorable(self):
        trashed = self._file(self.owner, 'gone.txt', is_trashed=True)
        self._org_share(self._file(self.foreign, 'foreign-gone.txt', is_trashed=True))
        resp = self.client_api.get('/api/files/', {'trash': '1'})
        self.assertEqual(resp.status_code, 200)
        names = {r['name'] for r in resp.data['results']}
        self.assertIn('gone.txt', names)
        self.assertNotIn('foreign-gone.txt', names)
