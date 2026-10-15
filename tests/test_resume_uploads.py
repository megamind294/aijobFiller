import io
import json
import zipfile
from pathlib import Path

from reportlab.pdfgen import canvas
from web_support import WebCase
from jobpilot.core import read_json, write_json
from test_workflow import PROFILE


def sample_pdf():
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream)
    text = pdf.beginText(50, 800)
    for line in ['Jane Example', 'jane@example.com', '+48 123 456 789',
                 'https://linkedin.com/in/jane-example', 'Professional Summary',
                 'Frontend developer building accessible interfaces.', 'Skills',
                 'React, TypeScript, CSS', 'Experience', 'Example Studio']:
        text.textLine(line)
    pdf.drawText(text)
    pdf.save()
    return stream.getvalue()


def sample_docx(text='Jane Example\njane@example.com\nSkills\nReact, TypeScript'):
    from xml.sax.saxutils import escape
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        paragraphs = ''.join('<w:p><w:r><w:t>' + escape(line) + '</w:t></w:r></w:p>' for line in text.splitlines())
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + paragraphs + '</w:body></w:document>')
    return stream.getvalue()


class ResumeUploadTests(WebCase):
    def setUp(self):
        super().setUp()
        self.login()

    def upload(self, data, filename='resume.pdf'):
        return self.post('/profile/resume', {'file': (io.BytesIO(data), filename)}, page='/profile')

    def test_profile_has_visible_resume_upload(self):
        self.assertIn(b'Upload your resume', self.client.get('/profile').data)

    def test_pdf_upload_download_and_suggestions_do_not_overwrite_profile(self):
        write_json(self.home / 'profile.json', PROFILE)
        original = sample_pdf()
        response = self.upload(original)
        self.assertIn(b'Resume uploaded', response.data)
        self.assertIn(b'jane@example.com', response.data)
        self.assertEqual(read_json(self.home / 'profile.json'), PROFILE)
        download = self.client.get('/profile/resume/download')
        self.addCleanup(download.close)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, original)
        self.assertIn('attachment', download.headers['Content-Disposition'])

    def test_docx_upload_extracts_text_and_escapes_markup(self):
        response = self.upload(sample_docx('Jane Example\njane@example.com\n<script>alert(1)</script>'), 'resume.docx')
        self.assertIn(b'Resume uploaded', response.data)
        self.assertIn(b'&lt;script&gt;', response.data)
        self.assertNotIn(b'<script>alert', response.data)

    def test_invalid_replacement_preserves_previous_resume(self):
        original = sample_pdf()
        self.upload(original)
        response = self.upload(b'not a PDF')
        self.assertIn(b'could not', response.data.lower())
        download = self.client.get('/profile/resume/download')
        self.addCleanup(download.close)
        self.assertEqual(download.data, original)
        response = self.upload(b'hello', 'resume.exe')
        self.assertIn(b'PDF or DOCX', response.data)

    def test_import_requires_confirmation_and_creates_unverified_draft(self):
        write_json(self.home / 'profile.json', PROFILE)
        self.upload(sample_pdf())
        metadata = read_json(self.home / 'current-resume.json')
        self.post('/profile/resume/import', {'resume_id': metadata['id']}, page='/profile')
        self.assertEqual(read_json(self.home / 'profile.json'), PROFILE)
        response = self.post('/profile/resume/import', {'resume_id': metadata['id'], 'confirmed': 'yes'}, page='/profile')
        self.assertIn(b'Unverified profile draft', response.data)
        profile = read_json(self.home / 'profile.json')
        self.assertFalse(profile['facts_verified'])
        self.assertEqual(profile['personal']['email'], 'jane@example.com')
        self.assertEqual(profile['personal']['first_name'], 'Jane')
        self.assertEqual(profile['skills'], ['React', 'TypeScript', 'CSS'])
        self.assertEqual(profile['experience'], PROFILE['experience'])

    def test_resume_routes_require_login_and_csrf(self):
        self.assertEqual(self.client.post('/profile/resume', data={}).status_code, 400)
        other = self.app.test_client()
        self.assertEqual(other.get('/profile/resume/download').status_code, 302)
        self.assertEqual(self.client.get('/profile/resume/download').status_code, 404)

    def test_extraction_timeout_preserves_previous_resume(self):
        import subprocess
        from unittest.mock import patch
        original = sample_pdf()
        self.upload(original)
        with patch('jobpilot.resume_uploads.subprocess.run', side_effect=subprocess.TimeoutExpired('extract', 15)):
            response = self.upload(original)
        self.assertIn(b'time limit', response.data)
        download = self.client.get('/profile/resume/download')
        self.addCleanup(download.close)
        self.assertEqual(download.data, original)

    def test_encrypted_and_overlong_pdfs_are_rejected(self):
        from pypdf import PdfWriter
        for encrypted in (False, True):
            with self.subTest(encrypted=encrypted):
                writer = PdfWriter()
                for _ in range(1 if encrypted else 26):
                    writer.add_blank_page(width=100, height=100)
                if encrypted:
                    writer.encrypt('private-test-password')
                output = io.BytesIO()
                writer.write(output)
                response = self.upload(output.getvalue())
                self.assertIn(b'password protection' if encrypted else b'25 pages', response.data)
                self.assertFalse((self.home / 'current-resume.json').exists())

    def test_docx_decompression_limit(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('word/document.xml', b'x' * (6 * 1024 * 1024))
        response = self.upload(stream.getvalue(), 'resume.docx')
        self.assertIn(b'content limit', response.data)
        self.assertFalse((self.home / 'current-resume.json').exists())

    def test_old_tab_cannot_import_a_replaced_resume(self):
        write_json(self.home / 'profile.json', PROFILE)
        self.upload(sample_pdf())
        old = read_json(self.home / 'current-resume.json')['id']
        self.upload(sample_docx(), 'new-resume.docx')
        response = self.post('/profile/resume/import', {'resume_id': old, 'confirmed': 'yes'}, page='/profile')
        self.assertIn(b'changed', response.data)
        self.assertEqual(read_json(self.home / 'profile.json'), PROFILE)

    def test_uploaded_original_symlink_is_not_downloaded(self):
        from unittest.mock import patch
        self.upload(sample_pdf())
        metadata = read_json(self.home / 'current-resume.json')
        original = self.home / 'uploaded-resumes' / metadata['id'] / 'original.pdf'
        with patch('pathlib.Path.is_symlink', autospec=True, side_effect=lambda path: path == original):
            response = self.client.get('/profile/resume/download')
        self.assertEqual(response.status_code, 404)

    def test_suggestions_are_generated_in_the_limited_worker(self):
        from unittest.mock import patch
        with patch('jobpilot.resume_uploads.suggested_profile', side_effect=RuntimeError('Parser ran in web process')):
            response = self.upload(sample_pdf())
        self.assertIn(b'Resume uploaded', response.data)
        self.assertIn(b'jane@example.com', response.data)

    def test_long_email_like_text_does_not_stall_upload(self):
        import time
        started = time.monotonic()
        response = self.upload(sample_docx('a' * 90000), 'long.docx')
        self.assertLess(time.monotonic() - started, 15)
        self.assertIn(b'Resume uploaded', response.data)

    def test_common_section_boundary_is_not_imported_as_skills(self):
        self.upload(sample_docx('Jane Example\nSkills\nReact\nEmployment History\nExample Corp'), 'resume.docx')
        self.assertEqual(read_json(self.home / 'current-resume.json')['draft']['skills'], ['React'])

    def test_long_filename_keeps_download_extension(self):
        self.upload(sample_pdf(), 'a' * 180 + '.pdf')
        response = self.client.get('/profile/resume/download')
        self.addCleanup(response.close)
        self.assertTrue(response.headers['Content-Disposition'].endswith('.pdf'))
