"""DRV-73: valid generated ZIP report packs must pass MIME validation."""

import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from drive.serializers import FileUploadSerializer


class ZipUploadDetectionTests(SimpleTestCase):
    def test_python_generated_zip_is_accepted_when_libmagic_says_octet_stream(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('financial_summary.json', '{}')
            archive.writestr('ledger_report.csv', 'ledger,balance\nCash,1\n')

        serializer = FileUploadSerializer(data={
            'file': SimpleUploadedFile(
                'computax-report.zip',
                buffer.getvalue(),
                content_type='application/zip',
            ),
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_arbitrary_octet_stream_remains_rejected(self):
        serializer = FileUploadSerializer(data={
            'file': SimpleUploadedFile(
                'opaque.bin',
                bytes(range(256)) * 8,
                content_type='application/octet-stream',
            ),
        })

        self.assertFalse(serializer.is_valid())
        self.assertIn('not allowed', str(serializer.errors))
