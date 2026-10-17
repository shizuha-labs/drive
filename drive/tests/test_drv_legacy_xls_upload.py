from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from drive.serializers import FileUploadSerializer, _detect_upload_mime


OLE2_PAYLOAD = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1' + (b'\x00' * 1024)


class LegacyXlsUploadTests(SimpleTestCase):
    @patch('drive.serializers.magic.from_buffer', return_value='application/x-ole-storage')
    def test_valid_legacy_xls_is_normalized_across_libmagic_variants(self, _magic):
        upload = SimpleUploadedFile(
            'legacy-statement.xls',
            OLE2_PAYLOAD,
            content_type='application/vnd.ms-excel',
        )

        self.assertEqual(_detect_upload_mime(upload), 'application/vnd.ms-excel')

    @override_settings(ALLOWED_FILE_TYPES=['application/vnd.ms-excel'])
    @patch('drive.serializers.magic.from_buffer', return_value='application/x-ole-storage')
    def test_upload_serializer_accepts_signature_matched_legacy_xls(self, _magic):
        upload = SimpleUploadedFile(
            'legacy-statement.xls',
            OLE2_PAYLOAD,
            content_type='application/vnd.ms-excel',
        )

        serializer = FileUploadSerializer(data={'file': upload})

        self.assertTrue(serializer.is_valid(), serializer.errors)

    @override_settings(ALLOWED_FILE_TYPES=['application/vnd.ms-excel'])
    @patch('drive.serializers.magic.from_buffer', return_value='application/octet-stream')
    def test_xls_suffix_without_ole2_signature_remains_rejected(self, _magic):
        upload = SimpleUploadedFile(
            'renamed-binary.xls',
            b'not an OLE2 workbook',
            content_type='application/vnd.ms-excel',
        )

        serializer = FileUploadSerializer(data={'file': upload})

        self.assertFalse(serializer.is_valid())
        self.assertIn("application/octet-stream", str(serializer.errors))

    @override_settings(
        ALLOWED_FILE_TYPES=[
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        ]
    )
    @patch('drive.serializers.magic.from_buffer', return_value='application/x-ole-storage')
    def test_encrypted_xlsx_ole2_container_is_accepted(self, _magic):
        upload = SimpleUploadedFile(
            'password-protected-statement.xlsx',
            OLE2_PAYLOAD,
            content_type='application/octet-stream',
        )

        serializer = FileUploadSerializer(data={'file': upload})

        self.assertTrue(serializer.is_valid(), serializer.errors)

    @override_settings(ALLOWED_FILE_TYPES=['text/plain'])
    @patch(
        'drive.serializers.magic.from_buffer',
        return_value='application/x-wine-extension-ini',
    )
    def test_utf8_desktop_ini_is_normalized_to_plain_text(self, _magic):
        upload = SimpleUploadedFile(
            'desktop.ini',
            b'[.ShellClassInfo]\\nLocalizedResourceName=Evidence\\n',
        )

        serializer = FileUploadSerializer(data={'file': upload})

        self.assertTrue(serializer.is_valid(), serializer.errors)

    @override_settings(ALLOWED_FILE_TYPES=['text/plain'])
    @patch('drive.serializers.magic.from_buffer', return_value='application/x-empty')
    def test_empty_txt_marker_is_normalized_to_plain_text(self, _magic):
        upload = SimpleUploadedFile('No Trade in Feb.txt', b'')

        serializer = FileUploadSerializer(data={'file': upload})

        self.assertTrue(serializer.is_valid(), serializer.errors)

    @override_settings(ALLOWED_FILE_TYPES=['text/plain'])
    @patch(
        'drive.serializers.magic.from_buffer',
        return_value='application/x-wine-extension-ini',
    )
    def test_binary_ini_remains_rejected(self, _magic):
        upload = SimpleUploadedFile(
            'renamed-binary.ini',
            b'\x00\x01\x02not text',
        )

        serializer = FileUploadSerializer(data={'file': upload})

        self.assertFalse(serializer.is_valid())
        self.assertIn('not allowed', str(serializer.errors))
