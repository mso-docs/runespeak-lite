"""Local document extraction. All conversion and OCR stays on this machine."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import unquote, urlparse
from urllib.request import Request, url2pathname, urlopen
import xml.etree.ElementTree as ET
import zipfile

from .webpage import extract_article, read_html, MAX_PAGE_BYTES
from .platforms import is_macos, is_windows, kill_tree, start_isolated

MAX_TEXT = 4 * 1024 * 1024
SUPPORTED = '.txt .md .markdown .pdf .docx .doc .odt .rtf .png .jpg .jpeg .tif .tiff .bmp .webp'.split()


# Where the optional converters usually live when they are not on PATH (Finder and the
# Windows Start menu launch apps with a minimal PATH).
EXTRA_LOCATIONS = {
    'libreoffice': ['/Applications/LibreOffice.app/Contents/MacOS/soffice',
                    r'C:\Program Files\LibreOffice\program\soffice.exe',
                    r'C:\Program Files (x86)\LibreOffice\program\soffice.exe'],
    'tesseract': ['/opt/homebrew/bin/tesseract', '/usr/local/bin/tesseract', '/opt/local/bin/tesseract',
                  r'C:\Program Files\Tesseract-OCR\tesseract.exe'],
    'pdftotext': ['/opt/homebrew/bin/pdftotext', '/usr/local/bin/pdftotext', '/opt/local/bin/pdftotext'],
    'pdfinfo': ['/opt/homebrew/bin/pdfinfo', '/usr/local/bin/pdfinfo', '/opt/local/bin/pdfinfo'],
    'pdftoppm': ['/opt/homebrew/bin/pdftoppm', '/usr/local/bin/pdftoppm', '/opt/local/bin/pdftoppm'],
}


def find_tool(name):
    found = shutil.which(name) or (shutil.which('soffice') if name == 'libreoffice' else None)
    if found:
        return found
    return next((path for path in EXTRA_LOCATIONS.get(name, ()) if Path(path).is_file()), None)


def install_hint(name):
    if is_macos():
        return {'tesseract': 'Install Tesseract (for example: brew install tesseract).',
                'libreoffice': 'Install LibreOffice from libreoffice.org.'}.get(name, 'Install Poppler (for example: brew install poppler).')
    if is_windows():
        return {'tesseract': 'Install Tesseract OCR (UB Mannheim build) and add it to PATH.',
                'libreoffice': 'Install LibreOffice from libreoffice.org.'}.get(name, 'Install Poppler for Windows and add its bin folder to PATH.')
    package = 'tesseract-ocr' if name == 'tesseract' else 'libreoffice-writer' if name == 'libreoffice' else 'poppler-utils'
    return f'Install {package}.'


def command(args, cancel, timeout=120):
    executable = find_tool(args[0])
    if not executable:
        raise ValueError(f'{args[0]} is needed for this document. {install_hint(args[0])}')
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = start_isolated([executable, *args[1:]], stdout=output, stderr=errors)
        start = time.monotonic()
        try:
            while process.poll() is None:
                if cancel.wait(.1):
                    raise InterruptedError('Document loading cancelled')
                if time.monotonic() - start > timeout:
                    raise ValueError('Document conversion timed out. Try a smaller document.')
                if os.fstat(output.fileno()).st_size > MAX_TEXT:
                    raise ValueError('Extracted text exceeds the 4 MB limit.')
            if cancel.is_set():
                raise InterruptedError('Document loading cancelled')
            if process.returncode:
                errors.seek(0)
                detail = errors.read(2000).decode('utf-8', 'replace').strip()
                raise ValueError(f'{args[0]} could not read this document. {detail}')
            output.seek(0)
            data = output.read(MAX_TEXT + 1)
            if len(data) > MAX_TEXT:
                raise ValueError('Extracted text exceeds the 4 MB limit.')
            return data.decode('utf-8', 'replace')
        finally:
            kill_tree(process)
            process.wait()


def decode_text(data):
    if b'\x00' in data[:1000] and not data.startswith((b'\xff\xfe', b'\xfe\xff')):
        raise ValueError('This does not look like a plain text document.')
    try:
        return data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig')
    except UnicodeDecodeError:
        return data.decode('cp1252')


def markdown_text(text):
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'(?m)^\s{0,3}(?:>\s?|[-*+]\s+)', '', text)
    text = re.sub(r'(?m)^\s*(?:```|~~~).*$', '', text)
    text = re.sub(r'(?m)^\s*(?:---+|===+)\s*$', '', text)
    return text.replace('**', '').replace('__', '').replace('`', '')


def zip_text(path, odt=False):
    with zipfile.ZipFile(path) as archive:
        names = ['content.xml'] if odt else ['word/document.xml', 'word/footnotes.xml', 'word/endnotes.xml']
        paragraphs = []
        for name in names:
            if name not in archive.namelist():
                continue
            if archive.getinfo(name).file_size > MAX_TEXT:
                raise ValueError('Document XML exceeds the 4 MB limit.')
            root = ET.fromstring(archive.read(name))
            if odt:
                tags = {'{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p', '{urn:oasis:names:tc:opendocument:xmlns:text:1.0}h'}
                paragraphs.extend(''.join(element.itertext()) for element in root.iter() if element.tag in tags)
            else:
                namespace = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
                paragraphs.extend(''.join(element.itertext()) for element in root.iter(namespace + 'p'))
        return '\n\n'.join(paragraphs)


def image_text(path, cancel):
    from PIL import Image, ImageOps
    with tempfile.TemporaryDirectory(prefix='runespeak-ocr-') as folder:
        image_path = Path(folder) / 'image.png'
        with Image.open(path) as image:
            if image.width * image.height > 40_000_000:
                raise ValueError('Image exceeds the 40 megapixel OCR limit.')
            ImageOps.exif_transpose(image).convert('RGB').save(image_path)
        return command(['tesseract', str(image_path), 'stdout', '-l', 'eng'], cancel)


def pdfium_text(path, cancel, progress):
    """PDF text without Poppler (the default on macOS and Windows): bundled PDFium.

    Text is rebuilt from positioned runs (see pdflayout) so lines, paragraphs and headings
    read naturally; scanned pages fall back to Tesseract OCR when it is installed.
    """
    import pypdfium2 as pdfium
    from .pdflayout import page_runs, pdf_layout_text
    try:
        document = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        raise ValueError(f'Could not open this PDF ({exc}). Password-protected PDFs are not supported.') from exc
    try:
        count = len(document)
        if count > 200:
            raise ValueError('PDF exceeds 200 pages. Split it into smaller documents first.')
        pages = []
        scanned = []
        for index in range(count):
            if cancel.is_set():
                raise InterruptedError('Document loading cancelled')
            progress(f'Reading PDF page {index+1} / {count}…')
            page = document[index]
            textpage = page.get_textpage()
            try:
                width, height = page.get_size()
                runs = page_runs(textpage)
                if not runs:
                    scanned.append(index)
                    if find_tool('tesseract'):
                        progress(f'OCR on scanned page {index+1} / {count}…')
                        with tempfile.TemporaryDirectory(prefix='runespeak-page-') as folder:
                            image = Path(folder) / 'page.png'
                            page.render(scale=2400 / max(width, height, 1)).to_pil().save(image)
                            pages.append(('ocr', image_text(image, cancel)))
                        continue
                pages.append(('layout', (width, height, runs)))
            finally:
                textpage.close()
                page.close()
    finally:
        document.close()
    if scanned and len(scanned) == count and not find_tool('tesseract'):
        raise ValueError(f'This PDF has no text layer (scanned pages). {install_hint("tesseract")}')
    parts = []
    group = []
    for kind, value in pages + [('end', None)]:
        if kind == 'layout':
            group.append(value)
            continue
        if group:
            parts.append(pdf_layout_text(group, cancel, progress))
            group = []
        if kind == 'ocr' and value.strip():
            parts.append(value.strip())
    text = '\n\n'.join(part for part in parts if part)
    if len(text) > MAX_TEXT:
        raise ValueError('Extracted text exceeds the 4 MB limit.')
    return text


def pdf_text(path, cancel, progress):
    if not (find_tool('pdfinfo') and find_tool('pdftotext')):
        return pdfium_text(path, cancel, progress)
    info = command(['pdfinfo', str(path)], cancel)
    match = re.search(r'^Pages:\s+(\d+)', info, re.M)
    if not match:
        raise ValueError('Unable to determine PDF page count.')
    pages = int(match.group(1))
    if pages > 200:
        raise ValueError('PDF exceeds 200 pages. Split it into smaller documents first.')
    extracted = command(['pdftotext', '-enc', 'UTF-8', str(path), '-'], cancel).split('\f')
    result = []
    for index in range(pages):
        if cancel.is_set():
            raise InterruptedError('Document loading cancelled')
        text = extracted[index] if index < len(extracted) else ''
        progress(f'Reading PDF page {index+1} / {pages}…')
        if not text.strip():
            progress(f'OCR on scanned page {index+1} / {pages}…')
            with tempfile.TemporaryDirectory(prefix='runespeak-page-') as folder:
                prefix = str(Path(folder) / 'page')
                command(['pdftoppm', '-f', str(index+1), '-l', str(index+1), '-scale-to', '2400', '-singlefile', '-png', str(path), prefix], cancel)
                text = image_text(prefix + '.png', cancel)
        result.append(text.strip())
    return '\n\n'.join(result)


def extract_file(path, cancel, progress=lambda message: None):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError('Choose a document file, not a folder.')
    if path.stat().st_size > MAX_PAGE_BYTES:
        raise ValueError('Document exceeds the 64 MiB limit.')
    if cancel.is_set():
        raise InterruptedError('Document loading cancelled')
    suffix = path.suffix.lower()
    with path.open('rb') as source:
        magic = source.read(8)
    progress(f'Reading {path.name}…')
    if magic.startswith(b'%PDF-') or suffix == '.pdf':
        text = pdf_text(path, cancel, progress)
    elif suffix in ('.txt', '.md', '.markdown'):
        text = decode_text(path.read_bytes())
        if suffix != '.txt':
            text = markdown_text(text)
    elif suffix in ('.docx', '.odt'):
        text = zip_text(path, suffix == '.odt')
    elif suffix in ('.doc', '.rtf'):
        with tempfile.TemporaryDirectory(prefix='runespeak-office-') as folder:
            profile = (Path(folder) / 'profile').as_uri()
            command(['libreoffice', f'-env:UserInstallation={profile}', '--headless', '--convert-to', 'txt:Text (encoded):UTF8', '--outdir', folder, str(path)], cancel)
            output = Path(folder) / (path.stem + '.txt')
            if not output.exists():
                raise ValueError('LibreOffice could not convert this document.')
            if output.stat().st_size > MAX_TEXT:
                raise ValueError('Extracted text exceeds the 4 MB limit.')
            text = decode_text(output.read_bytes())
    elif suffix in ('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp'):
        text = image_text(path, cancel)
    else:
        raise ValueError('Supported files: TXT, MD, PDF, DOCX, DOC, ODT, RTF, PNG, JPEG, TIFF, BMP, WebP.')
    text = text.strip()
    if len(text.encode('utf-8')) > MAX_TEXT:
        raise ValueError('Extracted text exceeds the 4 MB limit.')
    if not text:
        raise ValueError('No readable text found. Check scan quality or try a text-based document.')
    return text


def load_source(source, cancel, progress=lambda message: None):
    parsed = urlparse(source)
    if parsed.scheme == 'file':
        if parsed.netloc not in ('', 'localhost'):
            raise ValueError('Only local file URLs are supported.')
        # url2pathname turns /C:/Users/you into a C: drive path on Windows; it only unquotes elsewhere.
        return extract_file(url2pathname(parsed.path), cancel, progress)
    if parsed.scheme not in ('http', 'https'):
        return extract_file(source, cancel, progress)
    with urlopen(Request(source, headers={'User-Agent': 'Runespeak-Lite/0.2'}), timeout=20) as response:
        if urlparse(response.url).scheme not in ('http', 'https'):
            raise ValueError('Unsupported redirect')
        content_type = response.headers.get('Content-Type', '').split(';')[0].lower()
        suffix = Path(unquote(urlparse(response.url).path)).suffix.lower()
        data = read_html(response, cancel)
    if cancel.is_set():
        raise InterruptedError('Document loading cancelled')
    if data.lstrip().startswith(b'%PDF-') or content_type == 'application/pdf':
        suffix = '.pdf'
    elif content_type in ('text/html', 'application/xhtml+xml') or data.lstrip().lower().startswith((b'<!doctype html', b'<html')):
        return extract_article(data)
    elif suffix not in SUPPORTED:
        suffix = {'text/plain': '.txt', 'text/markdown': '.md', 'image/png': '.png', 'image/jpeg': '.jpg', 'image/tiff': '.tiff', 'image/webp': '.webp', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx', 'application/msword': '.doc', 'application/vnd.oasis.opendocument.text': '.odt'}.get(content_type, '')
    if suffix not in SUPPORTED:
        raise ValueError('The link returned an unsupported document type.')
    with tempfile.TemporaryDirectory(prefix='runespeak-download-') as folder:
        path = Path(folder) / ('document' + suffix)
        path.write_bytes(data)
        return extract_file(path, cancel, progress)
