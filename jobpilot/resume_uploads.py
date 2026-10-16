"""Private resume intake; extracted suggestions are never verified facts."""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from .core import now, read_json, write_json


def ocr_pdf_page(path, page_number, deadline):
    """Read one bounded raster page locally; never send a resume to an API."""
    renderer, engine = shutil.which('pdftoppm'), shutil.which('tesseract')
    if not renderer or not engine:
        raise ValueError('This PDF needs OCR. OCR tools are unavailable; your previous resume is preserved.')
    environment = {**os.environ, 'OMP_THREAD_LIMIT': '1'}
    def run(command, limit):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('OCR reached its time limit. Try a shorter or clearer document.')
        try:
            result = subprocess.run(command, capture_output=True, timeout=min(limit, remaining), env=environment,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
        except subprocess.TimeoutExpired:
            raise ValueError('OCR reached its time limit. Try a shorter or clearer document.') from None
        if result.returncode:
            raise ValueError('OCR could not read this page. Try a clearer PDF or a DOCX resume.')
    with tempfile.TemporaryDirectory() as temporary:
        prefix = Path(temporary) / 'page'
        run([renderer, '-f', str(page_number), '-l', str(page_number), '-singlefile',
             '-r', '180', '-scale-to', '2400', '-png', str(path), str(prefix)], 15)
        image = prefix.with_suffix('.png')
        if not image.is_file() or image.stat().st_size > 25 * 1024 * 1024:
            raise ValueError('This page exceeds the OCR image limit.')
        output = Path(temporary) / 'recognized'
        run([engine, str(image), str(output), '-l', 'eng', '--psm', '3'], 20)
        result = output.with_suffix('.txt')
        if not result.is_file() or result.stat().st_size > 500000:
            raise ValueError('This page exceeds the OCR text limit.')
        return result.read_text(encoding='utf-8').strip()


def extract_document(path, details=None):
    details = details if details is not None else {}
    details['ocr_pages'] = []
    if path.suffix == '.pdf':
        from pypdf import PdfReader
        if not path.read_bytes().startswith(b'%PDF-'):
            raise ValueError('This PDF could not be read. Upload a valid PDF or DOCX resume.')
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError('Remove password protection from the PDF before uploading.')
        if len(reader.pages) > 25:
            raise ValueError('Upload a resume with at most 25 pages.')
        pages = []
        deadline = time.monotonic() + 60
        for number, page in enumerate(reader.pages, 1):
            page_text = page.extract_text() or ''
            if not page_text.strip() and len(page.images):
                if len(details['ocr_pages']) >= 10:
                    raise ValueError('Upload at most 10 scanned pages for OCR.')
                page_text = ocr_pdf_page(path, number, deadline)
                details['ocr_pages'].append(number)
            pages.append(page_text)
        text = '\n'.join(pages)
    else:
        from defusedxml.ElementTree import fromstring
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(entry.file_size for entry in entries) > 25 * 1024 * 1024:
                raise ValueError('This DOCX exceeds the document content limit.')
            if any(entry.flag_bits & 1 for entry in entries):
                raise ValueError('Remove password protection before uploading.')
            document = archive.getinfo('word/document.xml')
            if document.file_size > 5 * 1024 * 1024:
                raise ValueError('This DOCX exceeds the document content limit.')
            root = fromstring(archive.read(document))
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            text = '\n'.join(''.join(p.itertext()) for p in root.findall('.//w:p', ns))
    if len(text) > 100000:
        raise ValueError('The resume contains too much text. Upload a shorter document.')
    return text.strip()


def extract_in_worker(path):
    from flask import current_app, has_app_context
    from .service import ProcessLock
    from contextlib import nullcontext
    # One shared lock across all account workspaces and browser workers.
    lock = (ProcessLock(Path(current_app.config['JOBPILOT_HOME']) / 'heavy-work.lock')
            if has_app_context() else nullcontext())
    with lock:
        return _extract_in_worker(path)


def _extract_in_worker(path):
    try:
        result = subprocess.run([sys.executable, '-m', 'jobpilot.resume_uploads', '--extract', str(path)],
                                capture_output=True, text=True, encoding='utf-8', timeout=90,
                                cwd=str(Path(__file__).resolve().parent.parent),
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0)
    except subprocess.TimeoutExpired:
        raise ValueError('Resume extraction reached its time limit. Try a simpler PDF or DOCX.') from None
    try:
        payload = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ValueError('The resume could not be read within resource limits. Try a simpler document.') from None
    if not isinstance(payload, dict):
        raise ValueError('The extracted resume is invalid.')
    if result.returncode or 'error' in payload:
        raise ValueError(payload.get('error', 'The resume could not be read.'))
    text = payload.get('text')
    if not isinstance(text, str) or len(text) > 100000 or not isinstance(payload.get('draft'), dict):
        raise ValueError('The extracted resume text is invalid.')
    return payload


def structured_section(lines, section):
    """Parse explicit headings and dates; leave ambiguous identity/order in source text."""
    bullet = re.compile(r'^\s*[•●▪*\-–]\s+')
    date = r'(?:(?:[A-Za-z]{3,9}\.?\s+|\d{1,2}[/.-])?(?:19|20)\d{2}(?:[/.-]\d{1,2})?)'
    dates = re.compile(r'(?<!\w)(' + date + r')\s*[-–—]\s*(' + date + r'|Present|Current|Now|Obecnie)(?!\w)', re.I)
    identity = re.compile(
        r'\b(?:developer|engineer|manager|designer|analyst|specialist|consultant|intern|assistant|'
        r'architect|director|officer|lead|administrator|coordinator|researcher|technician)\b' if section == 'experience'
        else r'\b(?:b\.?sc|m\.?sc|b\.?tech|m\.?tech|ba|bs|ms|ma|mba|ph\.?d|bachelor|master|doctor|degree|diploma|licencjat|inżynier)\b', re.I)

    def clean(line):
        return bullet.sub('', line).strip()

    def headers(values):
        values = [value.strip(' |,;–—-') for value in values if value.strip(' |,;–—-')]
        # A separator or "at" explicitly relates role/degree to its organization.
        if len(values) == 1:
            values = re.split(r'\s*[|]\s*|\s+at\s+|\s+@\s+|\s+[-–—]\s+', values[0], maxsplit=1)
        if len(values) != 2 or any(len(value) > 300 for value in values):
            return None
        matches = [bool(identity.search(value)) for value in values]
        if matches.count(True) != 1:
            return None
        first, second = (values if matches[0] else values[::-1])
        return {'role': first, 'company': second} if section == 'experience' else {'degree': first, 'institution': second}

    if section == 'projects':
        entries = []
        item = None
        for line in lines:
            if bullet.match(line):
                if item:
                    item['bullets'].append(clean(line))
            elif re.fullmatch(r'https?://\S+', line):
                if item and not item['url']:
                    item['url'] = line
            else:
                # Unmarked prose cannot safely distinguish a second title.
                item = {'name': line, 'url': '', 'bullets': []}
                entries.append(item)
        return [item for item in entries if item['bullets'] or item['url']][:50]

    entries = []
    pending = []
    item = None
    for line in lines:
        match = dates.search(line) if not bullet.match(line) else None
        if match:
            inline = (line[:match.start()] + line[match.end():]).strip(' |,;–—-')
            candidates = pending + ([inline] if inline else [])
            # Only consume adjacent identity headers, never previous achievements.
            parsed = headers(candidates[-1:]) or headers(candidates[-2:])
            if parsed:
                used = 1 if headers(candidates[-1:]) else 2
                if item:
                    item['bullets'].extend(clean(value) for value in candidates[:-used])
                item = parsed
                if section == 'experience':
                    item.update(start=match[1], end=match[2])
                else:
                    item['dates'] = match[0]
                item['bullets'] = []
                entries.append(item)
            else:
                # Stop associating text when a dated record has unknown identity.
                if item:
                    item['bullets'].extend(clean(value) for value in pending)
                item = None
            pending = []
        elif bullet.match(line):
            if item:
                item['bullets'].extend(clean(value) for value in pending)
                pending = []
                item['bullets'].append(clean(line))
        else:
            pending.append(line)
    if item:
        item['bullets'].extend(clean(value) for value in pending)
    return entries[:50]


def suggested_profile(text):
    """Identify explicit contact values and conservative, reviewable section drafts."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    personal = {}
    if lines:
        words = lines[0].split()
        excluded = {'resume', 'cv', 'curriculum', 'vitae', 'developer', 'engineer', 'software', 'frontend', 'skills', 'profile'}
        if (2 <= len(words) <= 5 and len(lines[0]) <= 80 and
                all(word.replace('-', '').replace("'", '').isalpha() for word in words) and
                not excluded.intersection(word.casefold() for word in words)):
            personal.update(first_name=words[0], last_name=' '.join(words[1:]))
    # Tokenize first: an unanchored email regex can repeatedly rescan a long
    # run of email-compatible characters even when it contains no '@'.
    candidates = re.split(r"[\s<>()\[\]{},;:\"']+", text)
    emails = {candidate for candidate in candidates if len(candidate) <= 254 and '@' in candidate and
              re.fullmatch(r'[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,189}\.[A-Za-z]{2,63}', candidate)}
    if len(emails) == 1:
        personal['email'] = emails.pop()
    phones = set(match.strip() for match in re.findall(r'(?<!\w)\+\d[\d ()-]{7,24}\d', text))
    if len(phones) == 1:
        personal['phone'] = phones.pop()
    for field, pattern in [('linkedin', r'(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9_%.-]+'),
                           ('github', r'(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_-]+')]:
        matches = {value for value in re.findall(pattern, text) if len(value) <= 1000}
        if len(matches) == 1:
            value = matches.pop().rstrip('.')
            personal[field] = value if value.startswith(('http://', 'https://')) else 'https://' + value
    aliases = {'professional summary': 'summary', 'summary': 'summary', 'about me': 'summary',
               'profile': 'summary', 'podsumowanie': 'summary', 'skills': 'skills',
               'technical skills': 'skills', 'technologies': 'skills', 'umiejętności': 'skills',
               'experience': 'experience', 'work experience': 'experience', 'employment': 'experience', 'education': 'education',
               'employment history': 'experience', 'professional experience': 'experience', 'career history': 'experience',
               'work history': 'experience', 'academic background': 'education', 'educational background': 'education',
               'doświadczenie zawodowe': 'experience', 'wykształcenie': 'education',
               'projects': 'projects', 'personal projects': 'projects', 'selected projects': 'projects', 'projekty': 'projects',
               'languages': 'stop', 'certifications': 'stop', 'references': 'stop', 'awards': 'stop',
               'interests': 'stop', 'volunteering': 'stop', 'publications': 'stop'}
    sections = {section: [] for section in ('summary', 'skills', 'experience', 'education', 'projects')}
    current = None
    for line in lines:
        heading = line.casefold().strip(' :•-')
        if heading in aliases:
            current = aliases[heading]
        elif current in sections:
            sections[current].append(line)
    draft = {'personal': personal} if personal else {}
    if sections['summary']:
        draft['summary'] = ' '.join(sections['summary'])[:3000]
    if sections['skills']:
        skills = [part.strip(' •-\t') for part in re.split(r'[,;|\n]', '\n'.join(sections['skills']))]
        draft['skills'] = list(dict.fromkeys(skill for skill in skills if skill and len(skill) <= 100))[:50]
    for section in ('experience', 'education', 'projects'):
        entries = structured_section(sections[section], section)
        if entries:
            draft[section] = entries
    return draft


def private_path(home, metadata, name):
    identifier = metadata.get('id', '')
    suffix = metadata.get('suffix', '')
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier) or suffix not in ('.pdf', '.docx'):
        raise ValueError('Invalid stored resume.')
    path = home / 'uploaded-resumes' / identifier / (('original' + suffix) if name == 'original' else 'text.txt')
    if any(part.is_symlink() for part in (path, *path.parents)) or path.resolve() != path.absolute():
        raise ValueError('Invalid stored resume path.')
    return path


def load_resume(home):
    pointer = home / 'current-resume.json'
    if not pointer.exists():
        return None
    try:
        metadata = read_json(pointer)
        original = private_path(home, metadata, 'original')
        text_path = private_path(home, metadata, 'text')
        if not original.is_file() or not text_path.is_file() or text_path.stat().st_size > 500000:
            return None
        metadata['text'] = text_path.read_text(encoding='utf-8')
        return metadata
    except (ValueError, OSError, TypeError, AttributeError):
        return None


def register_resume_uploads(app):
    from flask import abort, flash, redirect, request, send_file
    from werkzeug.utils import secure_filename
    from .service import ProcessLock
    from .workspace import current_home
    from werkzeug.local import LocalProxy

    home = LocalProxy(current_home)

    def save_parsed(source, parsed, filename, uploaded_at, reuse_original=False):
        identifier = secrets.token_hex(16)
        metadata = {'id': identifier, 'suffix': source.suffix, 'filename': filename,
                    'size': source.stat().st_size, 'uploaded_at': uploaded_at, 'parsed_at': now(),
                    'draft': parsed['draft'], 'ocr_pages': parsed.get('ocr_pages', [])}
        directory = home / 'uploaded-resumes' / identifier
        directory.mkdir(parents=True, mode=0o700)
        target = private_path(home, metadata, 'original')
        if reuse_original:
            # Extraction revisions share the same immutable original inode.
            # Keep old versions for recovery without copying each 5 MiB file.
            os.link(source, target)
        else:
            shutil.copyfile(source, target)
        target.chmod(0o600)
        text_path = private_path(home, metadata, 'text')
        text_path.write_text(parsed['text'], encoding='utf-8')
        text_path.chmod(0o600)
        write_json(home / 'current-resume.json', metadata)

    @app.post('/profile/resume')
    def upload_resume():
        try:
            with ProcessLock(home / 'web-action.lock'):
                upload = request.files.get('file')
                suffix = Path(upload.filename or '').suffix.lower() if upload else ''
                if suffix not in ('.pdf', '.docx'):
                    raise ValueError('Choose a PDF or DOCX resume under 5 MiB.')
                with tempfile.TemporaryDirectory(dir=str(home)) as temporary:
                    source = Path(temporary) / ('original' + suffix)
                    upload.save(source)
                    if not 0 < source.stat().st_size <= 5 * 1024 * 1024:
                        raise ValueError('Choose a nonempty resume under 5 MiB.')
                    parsed = extract_in_worker(source)
                    text = parsed['text']
                    filename = Path(secure_filename(upload.filename)).stem[:140] or 'resume'
                    save_parsed(source, parsed, filename + suffix, now())
                flash('Resume uploaded. Review the extracted suggestions before importing them.', 'success')
                if not text:
                    flash('No readable text was found, including OCR. Your original is saved; try a clearer scan or DOCX.', 'error')
        except (ValueError, RuntimeError, OSError) as exc:
            flash('Another action is already running. Try again shortly.' if isinstance(exc, RuntimeError) else str(exc), 'error')
        return redirect('/profile#resume-upload')

    @app.post('/profile/resume/reprocess')
    def reprocess_uploaded_resume():
        try:
            with ProcessLock(home / 'web-action.lock'):
                metadata = load_resume(home)
                if metadata is None or request.form.get('resume_id') != metadata['id']:
                    raise ValueError('The uploaded resume changed. Reload this page before reading again.')
                source = private_path(home, metadata, 'original')
                parsed = extract_in_worker(source)
                if not parsed['text'].strip():
                    raise ValueError('No readable text was found, including OCR. Previous extracted text and suggestions are preserved.')
                save_parsed(source, parsed, metadata['filename'], metadata['uploaded_at'], reuse_original=True)
                flash('Resume read again. Review the recovered text and suggestions before importing.', 'success')
        except (ValueError, RuntimeError, OSError) as exc:
            flash('Another action is already running. Try again shortly.' if isinstance(exc, RuntimeError) else str(exc), 'error')
        return redirect('/profile#resume-upload')

    @app.get('/profile/resume/download')
    def download_uploaded_resume():
        metadata = load_resume(home)
        if metadata is None:
            abort(404)
        return send_file(private_path(home, metadata, 'original'), as_attachment=True,
                         download_name=metadata['filename'] or ('resume' + metadata['suffix']),
                         mimetype='application/pdf' if metadata['suffix'] == '.pdf' else
                         'application/vnd.openxmlformats-officedocument.wordprocessingml.document')

    @app.post('/profile/resume/import')
    def import_resume_draft():
        try:
            with ProcessLock(home / 'web-action.lock'):
                metadata = load_resume(home)
                if metadata is None or request.form.get('resume_id') != metadata['id']:
                    raise ValueError('The uploaded resume changed. Reload this page before importing.')
                if request.form.get('confirmed') != 'yes':
                    raise ValueError('Confirm that you want these suggestions imported as an unverified draft.')
                if not metadata.get('draft'):
                    raise ValueError('No profile fields could be reliably identified. Review the extracted text and enter your details below.')
                path = home / 'profile.json'
                profile = read_json(path) if path.exists() else {'personal': {}, 'skills': [], 'experience': [], 'projects': [], 'education': []}
                draft = metadata['draft']
                personal = profile.setdefault('personal', {})
                for field, value in draft.get('personal', {}).items():
                    if not personal.get(field):
                        personal[field] = value
                for field in ('summary', 'skills', 'experience', 'education', 'projects'):
                    if draft.get(field) and not profile.get(field):
                        profile[field] = draft[field]
                profile['facts_verified'] = False
                write_json(path, profile)
                flash('Unverified profile draft imported. Review all fields and check your work history before verifying.', 'success')
        except (ValueError, RuntimeError, OSError) as exc:
            flash('Another action is already running. Try again shortly.' if isinstance(exc, RuntimeError) else str(exc), 'error')
        return redirect('/profile')


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != '--extract':
        raise SystemExit(2)
    if os.name != 'nt':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    try:
        details = {}
        extracted_text = extract_document(Path(sys.argv[2]), details)
        print(json.dumps({'text': extracted_text, 'draft': suggested_profile(extracted_text), **details}))
    except ValueError as error:
        print(json.dumps({'error': str(error)}))
        raise SystemExit(2)
    except Exception:
        print(json.dumps({'error': 'This document could not be read. Upload a valid, unencrypted PDF or DOCX.'}))
        raise SystemExit(2)
