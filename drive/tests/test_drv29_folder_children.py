"""DRV-29 Slice 8: lazy folder children endpoint."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from drive.models import Folder

User = get_user_model()


class FolderChildrenApiTests(TestCase):
    """Tests for GET /folders/children/ lazy-load endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(username='owner', email='owner@example.com', password='x')
        self.other = User.objects.create_user(username='other', email='other@example.com', password='x')
        self.client.force_authenticate(self.owner)

        # Root folders
        self.root_a = Folder.objects.create(owner_id=str(self.owner.id), name='Projects')
        self.root_b = Folder.objects.create(owner_id=str(self.owner.id), name='Archive')

        # Nested folders
        self.child = Folder.objects.create(owner_id=str(self.owner.id), name='Invoices', parent=self.root_a)
        self.grandchild = Folder.objects.create(owner_id=str(self.owner.id), name='2024', parent=self.child)

        # Trashed folder (should be excluded)
        self.trashed = Folder.objects.create(
            owner_id=str(self.owner.id), name='Old', is_trashed=True, trashed_at='2026-01-01T00:00:00Z'
        )

        # Other user's folder (should not be visible)
        self.other_folder = Folder.objects.create(owner_id=str(self.other.id), name='Secret')

    def _get(self, **params):
        return self.client.get('/api/folders/children/', params)

    def test_unauthenticated_returns_401(self):
        self.client.force_authenticate(None)
        resp = self._get()
        self.assertEqual(resp.status_code, 401)

    def test_root_folders_no_parent(self):
        """GET /folders/children/ without parent returns root folders."""
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        names = {f['name'] for f in resp.data}
        self.assertEqual(names, {'Projects', 'Archive'})
        self.assertNotIn('Old', names)  # trashed excluded
        self.assertNotIn('Secret', names)  # other user excluded

    def test_children_with_parent_id(self):
        """GET /folders/children/?parent=<id> returns direct children."""
        resp = self._get(parent=self.root_a.id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['name'], 'Invoices')

    def test_empty_children(self):
        """Folder with no children returns empty list."""
        resp = self._get(parent=self.root_b.id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    def test_nested_children(self):
        """Grandchild is returned when querying the child folder."""
        resp = self._get(parent=self.child.id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['name'], '2024')

    def test_other_user_folder_not_visible(self):
        """Other user's folder children are not visible (empty list, not 404)."""
        resp = self._get(parent=self.other_folder.id)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    def test_trashed_folder_not_in_root(self):
        """Trashed folders are excluded from root listing."""
        resp = self._get()
        names = {f['name'] for f in resp.data}
        self.assertNotIn('Old', names)

    def test_ordering_by_name(self):
        """Children are ordered by name."""
        Folder.objects.create(owner_id=str(self.owner.id), name='Alpha', parent=self.root_a)
        Folder.objects.create(owner_id=str(self.owner.id), name='Beta', parent=self.root_a)
        resp = self._get(parent=self.root_a.id)
        names = [f['name'] for f in resp.data]
        self.assertEqual(names, sorted(names))
