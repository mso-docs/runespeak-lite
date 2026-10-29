import io
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from runespeak.documents import command, extract_file, load_source


def small_pdf(text):
    stream = f'BT /F1 18 Tf 50 700 Td ({text}) Tj ET'.encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>', b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    data = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref = len(data)
    data.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        data.extend(f'{offset:010d} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode())
    return bytes(data)


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.cancel = threading.Event()

    def tearDown(self):
        self.temp.cleanup()

    def test_text_and_markdown_and_utf16(self):
        path = self.folder / 'notes.md'
        path.write_text('# Heading\n\n**Bold words** and [a link](https://example.com).')
        self.assertEqual(extract_file(path, self.cancel), '# Heading\n\nBold words and a link.')
        path = self.folder / 'notes.txt'
        path.write_bytes('Unicode café notes'.encode('utf-16'))
        self.assertIn('café', extract_file(path, self.cancel))

    def test_word_and_odt(self):
        path = self.folder / 'paper.docx'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>A Word document.</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(extract_file(path, self.cancel), 'A Word document.')
        path = self.folder / 'paper.odt'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('content.xml', '<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><text:p>Open document text.</text:p></office:document>')
        self.assertEqual(extract_file(path, self.cancel), 'Open document text.')

    @unittest.skipUnless(shutil.which('pdftotext'), 'requires Poppler')
    def test_real_pdf_and_extensionless_pdf_link(self):
        data = small_pdf('A research paper about local speech.')
        path = self.folder / 'paper.pdf'
        path.write_bytes(data)
        self.assertIn('research paper', extract_file(path, self.cancel))
        response = io.BytesIO(data)
        response.url = 'https://example.org/pdf/12345'
        response.headers = {'Content-Type': 'application/octet-stream'}
        with patch('runespeak.documents.urlopen', return_value=response):
            self.assertIn('research paper', load_source(response.url, self.cancel))

    @unittest.skipUnless(shutil.which('tesseract') and shutil.which('pdftoppm'), 'requires OCR and Poppler')
    def test_real_image_and_scanned_pdf_ocr(self):
        from PIL import Image, ImageDraw, ImageFont
        image = Image.new('RGB', (1200, 400), 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 40)
        draw.text((60, 80), 'A speaking tome for adventurers.', font=font, fill='black')
        path = self.folder / 'scan.png'
        image.save(path)
        self.assertIn('speaking tome', extract_file(path, self.cancel).lower())
        path = self.folder / 'scan.pdf'
        image.save(path, 'PDF')
        self.assertIn('speaking tome', extract_file(path, self.cancel).lower())

    @unittest.skipUnless(shutil.which('libreoffice'), 'requires LibreOffice')
    def test_legacy_rtf_conversion(self):
        path = self.folder / 'old.rtf'
        path.write_text(r'{\rtf1\ansi A legacy document for narration.}')
        self.assertIn('legacy document', extract_file(path, self.cancel))

    def test_local_file_uri_with_spaces(self):
        path = self.folder / 'my research.txt'
        path.write_text('Local document with a space in its name.')
        self.assertIn('Local document', load_source(path.as_uri(), self.cancel))

    def test_cancel_and_unsupported_type(self):
        path = self.folder / 'unknown.bin'
        path.write_bytes(b'not a readable document')
        with self.assertRaisesRegex(ValueError, 'Supported files'):
            extract_file(path, self.cancel)
        self.cancel.set()
        with self.assertRaises(InterruptedError):
            extract_file(path, self.cancel)

    def test_running_converter_can_be_cancelled(self):
        timer = threading.Timer(.2, self.cancel.set)
        timer.start()
        try:
            with self.assertRaises(InterruptedError):
                command([sys.executable, '-c', 'import time; time.sleep(20)'], self.cancel)
        finally:
            timer.join()
