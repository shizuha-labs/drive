"""DRV-35: HTTP Range downloads for resume-on-reconnect."""
import hashlib
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from drive.models import File

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix='drive-drv35-'))
class RangeDownloadTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='drv35', email='drv35@example.com', password='x')
        self.client.force_authenticate(user=self.user)
        self.content = b'abcdefghijklmnopqrstuvwxyz'
        self.file_obj = File.objects.create(
            name='alphabet.txt',
            owner_id=str(self.user.id),
            file=ContentFile(self.content, name='alphabet.txt'),
            file_type='document',
            mime_type='text/plain',
            size=len(self.content),
            extension='txt',
            checksum_sha256=hashlib.sha256(self.content).hexdigest(),
        )
        self.url = f'/api/files/{self.file_obj.id}/download/'

    def _body(self, response):
        if hasattr(response, 'streaming_content'):
            return b''.join(response.streaming_content)
        return response.content

    def test_range_download_returns_206_with_content_range_and_slice(self):
        response = self.client.get(self.url, HTTP_RANGE='bytes=5-9')

        self.assertEqual(response.status_code, 206)
        self.assertEqual(self._body(response), b'fghij')
        self.assertEqual(response['Content-Range'], f'bytes 5-9/{len(self.content)}')
        self.assertEqual(response['Accept-Ranges'], 'bytes')
        self.assertEqual(response['X-Drive-Integrity'], 'partial-range')
        self.assertEqual(response['X-Drive-Checksum-SHA256'], hashlib.sha256(self.content).hexdigest())

    def test_two_consecutive_ranges_reconstruct_full_file(self):
        first = self.client.get(self.url, HTTP_RANGE='bytes=0-9')
        second = self.client.get(self.url, HTTP_RANGE=f'bytes=10-{len(self.content) - 1}')

        self.assertEqual(first.status_code, 206)
        self.assertEqual(second.status_code, 206)
        self.assertEqual(self._body(first) + self._body(second), self.content)

    def test_out_of_bounds_range_returns_416(self):
        response = self.client.get(self.url, HTTP_RANGE=f'bytes={len(self.content)}-{len(self.content) + 10}')

        self.assertEqual(response.status_code, 416)
        self.assertEqual(response['Accept-Ranges'], 'bytes')
        self.assertEqual(response['Content-Range'], f'bytes */{len(self.content)}')
        self.assertEqual(self._body(response), b'')

    def test_full_download_still_streams_with_accept_ranges(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._body(response), self.content)
        self.assertEqual(response['Accept-Ranges'], 'bytes')
        self.assertEqual(response['X-Drive-Integrity'], 'verified')
