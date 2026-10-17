"""DRV-47: content_text extraction and search_vector trigger slice."""
import zipfile
from io import BytesIO

from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase, skipUnlessDBFeature

from drive.models import File, FileType
from drive.tasks import (
    CONTENT_TEXT_MAX_BYTES,
    DOCUMENT_SOURCE_MAX_BYTES,
    process_file_upload,
)


def _minimal_pdf(text: str) -> bytes:
    """Build a valid single-page PDF with a text content stream and correct xref."""
    stream = f'BT /F1 12 Tf 72 720 Td ({text}) Tj ET'.encode('latin-1')
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        (b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] '
         b'/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>'),
        b'<< /Length %d >>\nstream\n%s\nendstream' % (len(stream), stream),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    out = BytesIO()
    out.write(b'%PDF-1.4\n')
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b'%d 0 obj\n%s\nendobj\n' % (index, body))
    xref_offset = out.tell()
    out.write(b'xref\n0 %d\n' % (len(objects) + 1))
    out.write(b'0000000000 65535 f \n')
    for offset in offsets:
        out.write(b'%010d 00000 n \n' % offset)
    out.write(b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n'
              % (len(objects) + 1, xref_offset))
    return out.getvalue()


def _minimal_docx(text: str) -> bytes:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'
    )
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', document_xml)
    return buf.getvalue()


def _minimal_xlsx(*strings: str) -> bytes:
    items = ''.join(f'<si><t>{s}</t></si>' for s in strings)
    shared_strings = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(strings)}" uniqueCount="{len(strings)}">{items}</sst>'
    )
    buf = BytesIO()
    with zipfile.ZipFile(buf, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('xl/sharedStrings.xml', shared_strings)
    return buf.getvalue()


class ContentTextExtractionTests(TestCase):
    def _file(self, *, name='notes.txt', content=b'hello drive search', mime_type='text/plain', file_type=FileType.DOCUMENT):
        file_obj = File(
            name=name,
            owner_id='user-1',
            file_type=file_type,
            mime_type=mime_type,
            size=len(content),
            extension=name.rsplit('.', 1)[-1].lower() if '.' in name else '',
        )
        file_obj.file.save(name, ContentFile(content), save=False)
        file_obj.save()
        return file_obj

    def test_processing_populates_content_text_for_text_file(self):
        file_obj = self._file(content=b'alpha beta searchable text')

        process_file_upload(str(file_obj.id))

        file_obj.refresh_from_db()
        self.assertTrue(file_obj.is_processed)
        self.assertEqual(file_obj.content_text, 'alpha beta searchable text')
        self.assertEqual(file_obj.processing_error, '')

    def test_processing_caps_content_text_with_marker(self):
        content = b'a' * (CONTENT_TEXT_MAX_BYTES + 100)
        file_obj = self._file(content=content)

        process_file_upload(str(file_obj.id))

        file_obj.refresh_from_db()
        encoded = file_obj.content_text.encode('utf-8')
        self.assertLessEqual(len(encoded), CONTENT_TEXT_MAX_BYTES)
        self.assertTrue(file_obj.content_text.endswith('[TRUNCATED]'))

    def test_unsupported_binary_stays_safe_and_processed(self):
        file_obj = self._file(
            name='bundle.zip',
            content=b'PK\x03\x04\x00binary',
            mime_type='application/zip',
            file_type=FileType.ARCHIVE,
        )

        process_file_upload(str(file_obj.id))

        file_obj.refresh_from_db()
        self.assertTrue(file_obj.is_processed)
        self.assertIsNone(file_obj.content_text)
        self.assertEqual(file_obj.processing_error, '')

    def test_processing_extracts_pdf_content(self):
        file_obj = self._file(
            name='report.pdf',
            content=_minimal_pdf('quarterly synergy report'),
            mime_type='application/pdf',
        )

        process_file_upload(str(file_obj.id))

        file_obj.refresh_from_db()
        self.assertTrue(file_obj.is_processed)
        self.assertIn('quarterly synergy report', file_obj.content_text or '')
        self.assertEqual(file_obj.processing_error, '')

    def test_processing_extracts_docx_content(self):
        file_obj = self._file(
            name='meeting-notes.docx',
            content=_minimal_docx('agenda action items decisions'),
            mime_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )

        process_file_upload(str(file_obj.id))

        file_obj.refresh_from_db()
        self.assertTrue(file_obj.is_processed)
        self.assertIn('agenda action items decisions', file_obj.content_text or '')
        self.assertEqual(file_obj.processing_error, '')

    def test_processing_extracts_xlsx_shared_strings(self):
        file_obj = self._file(
            name='budget.xlsx',
            content=_minimal_xlsx('revenue', 'forecast', 'q3-actuals'),
            mime_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        process_file_upload(str(file_obj.id))

        file_obj.refresh_from_db()
        self.assertTrue(file_obj.is_processed)
        for token in ('revenue', 'forecast', 'q3-actuals'):
            self.assertIn(token, file_obj.content_text or '')
        self.assertEqual(file_obj.processing_error, '')

    def test_corrupt_pdf_records_error_without_failing_processing(self):
        file_obj = self._file(
            name='broken.pdf',
            content=b'%PDF-1.4 this is not a real pdf body',
            mime_type='application/pdf',
        )

        process_file_upload(str(file_obj.id))

        file_obj.refresh_from_db()
        self.assertTrue(file_obj.is_processed)
        self.assertIsNone(file_obj.content_text)

    def test_zip_bomb_ooxml_member_skipped_without_decompression(self):
        """Review P1: a small .docx whose word/document.xml DECLARES a huge
        uncompressed size must be refused before any read/parse of that
        member — never decompressed into worker memory."""
        from unittest.mock import patch

        from drive.tasks import OOXML_MEMBER_MAX_BYTES, _extract_ooxml_text

        # ~9 MiB of repetitive XML compresses to a few KiB — a decompression
        # amplification payload well past the per-member cap.
        bomb_xml = (
            '<?xml version="1.0"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>' + '<w:p><w:r><w:t>x</w:t></w:r></w:p>' * 350_000 + '</w:body></w:document>'
        )
        self.assertGreater(len(bomb_xml), OOXML_MEMBER_MAX_BYTES)
        buf = BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', '<Types/>')
            archive.writestr('word/document.xml', bomb_xml)
        payload = buf.getvalue()
        self.assertLess(len(payload), 1024 * 1024)  # small on the wire

        with patch.object(
            zipfile.ZipFile, 'open',
            side_effect=AssertionError('oversized member must not be opened'),
        ):
            extracted = _extract_ooxml_text(payload, 'docx')

        self.assertEqual(extracted, '')

        # End-to-end: processing records the file safely with no content_text.
        file_obj = self._file(
            name='bomb.docx',
            content=payload,
            mime_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        process_file_upload(str(file_obj.id))
        file_obj.refresh_from_db()
        self.assertTrue(file_obj.is_processed)
        self.assertIsNone(file_obj.content_text)
        self.assertEqual(file_obj.processing_error, '')

    def test_ooxml_aggregate_budget_bounds_total_decompression(self):
        """Many under-cap pptx slides must stop at the aggregate budget."""
        from unittest.mock import patch

        from drive.tasks import _extract_ooxml_text

        slide = (
            '<?xml version="1.0"?>'
            '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:t>slide-text</a:t>' + '<a:x/>' * 100_000 + '</p:sld>'
        )
        buf = BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', '<Types/>')
            for i in range(100):
                archive.writestr(f'ppt/slides/slide{i}.xml', slide)

        opened = []
        real_open = zipfile.ZipFile.open

        def counting_open(self, member, *args, **kwargs):
            opened.append(getattr(member, 'filename', member))
            return real_open(self, member, *args, **kwargs)

        with patch.object(zipfile.ZipFile, 'open', counting_open):
            _extract_ooxml_text(buf.getvalue(), 'pptx')

        # Each slide declares ~600KB uncompressed; the 32 MiB aggregate budget
        # admits at most ~55 of the 100 members.
        self.assertLess(len(opened), 60)

    def test_oversized_document_skipped_safely(self):
        file_obj = self._file(
            name='huge.docx',
            content=_minimal_docx('tiny'),
            mime_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        )
        # Simulate an oversized upload via the size column — extraction must
        # refuse before loading the stream, leaving content_text None.
        File.objects.filter(id=file_obj.id).update(size=DOCUMENT_SOURCE_MAX_BYTES + 1)

        process_file_upload(str(file_obj.id))

        file_obj.refresh_from_db()
        self.assertTrue(file_obj.is_processed)
        self.assertIsNone(file_obj.content_text)
        self.assertEqual(file_obj.processing_error, '')


class SearchVectorTriggerTests(TestCase):
    @skipUnlessDBFeature('has_native_uuid_field')
    def test_search_vector_trigger_populates_on_postgresql(self):
        if connection.vendor != 'postgresql':
            self.skipTest('search_vector trigger is PostgreSQL-only')
        file_obj = File.objects.create(
            name='launch-plan.txt',
            owner_id='user-1',
            file_type=FileType.DOCUMENT,
            mime_type='text/plain',
            size=12,
            extension='txt',
            content_text='dojo launch searchable text',
        )

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT search_vector::text FROM drive_file WHERE id = %s",
                [str(file_obj.id)],
            )
            vector_text = cursor.fetchone()[0]

        self.assertIn('launch', vector_text)
        self.assertIn('dojo', vector_text)
