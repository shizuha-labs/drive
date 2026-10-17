"""Income-tax portal Java-wrapped PDFs must pass MIME validation and unwrap."""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from drive.serializers import (
    FileUploadSerializer,
    _detect_upload_mime,
    _extract_embedded_pdf,
)


def _java_wrapped_pdf(pdf_body: bytes) -> bytes:
    """Build a minimal Java ObjectOutputStream-like prefix + PDF + trailer.

    The real portal stream is a full Java serialization graph; Drive only needs
    the ``aced 0005`` magic and an embedded ``%PDF…%%EOF`` payload to accept
    and unwrap the upload.
    """
    prefix = (
        b'\xac\xed\x00\x05'  # STREAM_MAGIC / STREAM_VERSION
        b'ur\x00\x13[Ljava.lang.Object;'
        b'\x00' * 32
    )
    return prefix + pdf_body


MINIMAL_PDF = (
    b'%PDF-1.4\n'
    b'1 0 obj<<>>endobj\n'
    b'trailer<<>>\n'
    b'startxref\n0\n'
    b'%%EOF\n'
)


class JavaWrappedPdfDetectionTests(SimpleTestCase):
    def test_extract_embedded_pdf_from_java_wrapper(self):
        wrapped = _java_wrapped_pdf(MINIMAL_PDF)
        extracted = _extract_embedded_pdf(wrapped)
        self.assertIsNotNone(extracted)
        self.assertTrue(extracted.startswith(b'%PDF'))
        self.assertIn(b'%%EOF', extracted[-16:])

    def test_plain_pdf_is_not_unwrapped(self):
        self.assertIsNone(_extract_embedded_pdf(MINIMAL_PDF))

    def test_java_wrapped_pdf_detected_as_application_pdf(self):
        wrapped = _java_wrapped_pdf(MINIMAL_PDF)
        upload = SimpleUploadedFile(
            'ITR_Form.pdf',
            wrapped,
            content_type='application/octet-stream',
        )
        self.assertEqual(_detect_upload_mime(upload), 'application/pdf')

    @override_settings(ALLOWED_FILE_TYPES=['application/pdf', 'application/zip'])
    def test_java_wrapped_itr_form_is_accepted_by_serializer(self):
        wrapped = _java_wrapped_pdf(MINIMAL_PDF)
        serializer = FileUploadSerializer(
            data={
                'file': SimpleUploadedFile(
                    'ITR_Form.pdf',
                    wrapped,
                    content_type='application/octet-stream',
                ),
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)

    @override_settings(ALLOWED_FILE_TYPES=['application/pdf', 'application/zip'])
    def test_arbitrary_java_blob_without_pdf_remains_rejected(self):
        blob = b'\xac\xed\x00\x05' + bytes(range(256)) * 4
        serializer = FileUploadSerializer(
            data={
                'file': SimpleUploadedFile(
                    'opaque.bin',
                    blob,
                    content_type='application/octet-stream',
                ),
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn('not allowed', str(serializer.errors))
