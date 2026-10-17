"""DRV-87: My Drive root listings must never flatten nested descendants."""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from rest_framework.test import APIClient

from drive.models import File, Folder


User = get_user_model()


class ExplicitRootListingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.owner = User.objects.create_user(
            username='drv87-owner',
            email='drv87-owner@example.com',
            password='x',
        )
        self.client.force_authenticate(self.owner)
        self.root_folder = Folder.objects.create(owner_id=str(self.owner.id), name='Accounting')
        self.nested_folder = Folder.objects.create(
            owner_id=str(self.owner.id),
            name='FY 2025-26',
            parent=self.root_folder,
        )
        self.root_file = self._file('root.txt')
        self.nested_file = self._file('vda_schedule.csv', folder=self.nested_folder)

    def _file(self, name, *, folder=None):
        file_obj = File(
            owner_id=str(self.owner.id),
            name=name,
            folder=folder,
            mime_type='text/plain',
            size=1,
        )
        file_obj.file.save(name, ContentFile(b'x'), save=True)
        return file_obj

    @staticmethod
    def _rows(response):
        if isinstance(response.data, dict):
            return response.data.get('results', [])
        return response.data

    def test_folder_root_action_excludes_nested_folders(self):
        response = self.client.get('/api/folders/root/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['name'] for row in self._rows(response)], ['Accounting'])

    def test_file_root_action_excludes_nested_files(self):
        response = self.client.get('/api/files/root/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['name'] for row in self._rows(response)], ['root.txt'])
        self.assertNotIn('vda_schedule.csv', str(response.data))

    def test_nested_listing_still_returns_direct_children(self):
        folders = self.client.get('/api/folders/', {'parent': self.root_folder.id})
        files = self.client.get('/api/files/', {'folder': self.nested_folder.id})
        self.assertEqual([row['name'] for row in self._rows(folders)], ['FY 2025-26'])
        self.assertEqual([row['name'] for row in self._rows(files)], ['vda_schedule.csv'])
