import io
import shutil
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from jobpilot.core import read_json, write_json
from jobpilot.resume_uploads import extract_document, extract_in_worker
from test_resume_uploads import sample_pdf
from test_workflow import PROFILE
from web_support import WebCase


def image_pdf(with_text_page=False):
    image = Image.new('RGB', (1400, 1800), 'white')
    font_path = next((p for p in ('C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf') if Path(p).exists()), None)
    font = ImageFont.truetype(font_path, 40) if font_path else ImageFont.load_default(size=40)
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(['Jane Example', 'jane@example.com', 'Professional Summary',
                              'Frontend developer building accessible interfaces.', 'Skills', 'React, TypeScript, CSS']):
        draw.text((60, 80 + i * 90), line, fill='black', font=font)
    stream = io.BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(560, 720))
    if with_text_page:
        pdf.drawString(40, 670, 'Existing selectable work experience')
        pdf.showPage()
    pdf.drawImage(ImageReader(image), 0, 0, width=560, height=720)
    pdf.save()
    return stream.getvalue()


class ResumeOcrTests(WebCase):
    def test_busy_global_worker_prevents_resume_subprocess(self):
        from jobpilot.service import ProcessLock
        source = self.home / 'resume.pdf'
        source.write_bytes(sample_pdf())
        with self.app.app_context(), ProcessLock(self.home / 'heavy-work.lock'):
            with patch('jobpilot.resume_uploads.subprocess.run') as run:
                with self.assertRaises(RuntimeError):
                    extract_in_worker(source)
                run.assert_not_called()

    def upload(self, data, filename='resume.pdf'):
        return self.post('/profile/resume', {'file': (io.BytesIO(data), filename)}, page='/profile')

    def setUp(self):
        super().setUp()
        self.login()

    def test_image_page_is_read_and_selectable_page_is_preserved(self):
        source = self.home / 'mixed.pdf'
        source.write_bytes(image_pdf(with_text_page=True))
        with patch('jobpilot.resume_uploads.ocr_pdf_page', return_value='Jane Example\njane@example.com', create=True):
            text = extract_document(source)
        self.assertIn('Existing selectable work experience', text)
        self.assertIn('jane@example.com', text)

    def test_missing_ocr_does_not_replace_previous_resume(self):
        original = sample_pdf()
        self.upload(original)
        source = self.home / 'image.pdf'
        source.write_bytes(image_pdf())
        def parse(path):
            with patch('jobpilot.resume_uploads.shutil.which', return_value=None):
                text = extract_document(path)
            return {'text': text, 'draft': {}}
        with patch('jobpilot.resume_uploads.extract_in_worker', side_effect=parse):
            response = self.upload(source.read_bytes())
        self.assertIn(b'OCR', response.data)
        download = self.client.get('/profile/resume/download')
        self.addCleanup(download.close)
        self.assertEqual(download.data, original)

    def test_read_again_refreshes_text_without_importing_profile(self):
        original = sample_pdf()
        self.upload(original)
        before = read_json(self.home / 'current-resume.json')
        write_json(self.home / 'profile.json', PROFILE)
        parsed = {'text': 'Recovered resume text', 'draft': {'personal': {'first_name': 'Recovered'}}, 'ocr_pages': [1]}
        with patch('jobpilot.resume_uploads.extract_in_worker', return_value=parsed):
            response = self.post('/profile/resume/reprocess', {'resume_id': before['id']}, page='/profile')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Recovered resume text', response.data)
        self.assertIn(b'OCR', response.data)
        self.assertEqual(read_json(self.home / 'profile.json'), PROFILE)
        after = read_json(self.home / 'current-resume.json')
        self.assertNotEqual(before['id'], after['id'])
        download = self.client.get('/profile/resume/download')
        self.addCleanup(download.close)
        self.assertEqual(download.data, original)

    def test_read_again_failure_preserves_original_and_text(self):
        self.upload(sample_pdf())
        before = read_json(self.home / 'current-resume.json')
        with patch('jobpilot.resume_uploads.extract_in_worker', side_effect=ValueError('OCR time limit')):
            response = self.post('/profile/resume/reprocess', {'resume_id': before['id']}, page='/profile')
        self.assertIn(b'OCR time limit', response.data)
        self.assertEqual(read_json(self.home / 'current-resume.json'), before)

    def test_empty_reread_preserves_previous_extracted_state(self):
        self.upload(sample_pdf())
        before = read_json(self.home / 'current-resume.json')
        with patch('jobpilot.resume_uploads.extract_in_worker', return_value={'text': '', 'draft': {}, 'ocr_pages': [1]}):
            response = self.post('/profile/resume/reprocess', {'resume_id': before['id']}, page='/profile')
        self.assertIn(b'No readable text', response.data)
        self.assertEqual(read_json(self.home / 'current-resume.json'), before)

    def test_decorative_blank_page_does_not_require_ocr(self):
        source = self.home / 'decorative.pdf'
        output = io.BytesIO()
        pdf = canvas.Canvas(output)
        pdf.drawString(40, 700, 'Jane Example resume')
        pdf.showPage()
        pdf.setFillColorRGB(1, 1, 1)
        pdf.rect(20, 20, 50, 50, fill=1)
        pdf.save()
        source.write_bytes(output.getvalue())
        with patch('jobpilot.resume_uploads.shutil.which', return_value=None):
            try:
                text = extract_document(source)
            except ValueError as error:
                self.fail(str(error))
        self.assertIn('Jane Example resume', text)

    @__import__('unittest').skipUnless(shutil.which('pdftoppm') and shutil.which('tesseract'), 'Linux OCR tools required')
    def test_real_scanned_resume_in_limited_worker(self):
        source = self.home / 'scan.pdf'
        source.write_bytes(image_pdf())
        parsed = extract_in_worker(source)
        self.assertIn('jane@example.com', parsed['text'])
        self.assertEqual(parsed['ocr_pages'], [1])
        self.assertEqual(parsed['draft']['personal']['email'], 'jane@example.com')
