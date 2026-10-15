"""Private resume intake; extracted suggestions are never verified facts."""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from .core import now, read_json, write_json


def extract_document(path):
    if path.suffix == '.pdf':
        from pypdf import PdfReader
        if not path.read_bytes().startswith(b'%PDF-'):
            raise ValueError('This PDF could not be read. Upload a valid PDF or DOCX resume.')
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError('Remove password protection from the PDF before uploading.')
        if len(reader.pages) > 25:
            raise ValueError('Upload a resume with at most 25 pages.')
        text = '\n'.join((page.extract_text() or '') for page in reader.pages)
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
    try:
        result = subprocess.run([sys.executable, '-m', 'jobpilot.resume_uploads', '--extract', str(path)],
                                capture_output=True, text=True, encoding='utf-8', timeout=15,
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


def suggested_profile(text):
    """Only identify explicit contact values and clearly labeled sections."""
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
               'experience': 'stop', 'work experience': 'stop', 'employment': 'stop', 'education': 'stop',
               'employment history': 'stop', 'professional experience': 'stop', 'career history': 'stop',
               'work history': 'stop', 'academic background': 'stop', 'educational background': 'stop',
               'doświadczenie zawodowe': 'stop', 'wykształcenie': 'stop',
               'projects': 'stop', 'languages': 'stop', 'certifications': 'stop', 'references': 'stop'}
    sections = {'summary': [], 'skills': []}
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

    home = Path(app.config['JOBPILOT_HOME'])

    @app.post('/profile/resume')
    def upload_resume():
        try:
            with ProcessLock(home / 'web-action.lock'):
                upload = request.files.get('file')
                suffix = Path(upload.filename or '').suffix.lower() if upload else ''
                if suffix not in ('.pdf', '.docx'):
                    raise ValueError('Choose a PDF or DOCX resume under 5 MiB.')
                with tempfile.TemporaryDirectory(dir=home) as temporary:
                    source = Path(temporary) / ('original' + suffix)
                    upload.save(source)
                    if not 0 < source.stat().st_size <= 5 * 1024 * 1024:
                        raise ValueError('Choose a nonempty resume under 5 MiB.')
                    parsed = extract_in_worker(source)
                    text = parsed['text']
                    identifier = secrets.token_hex(16)
                    filename = Path(secure_filename(upload.filename)).stem[:140] or 'resume'
                    metadata = {'id': identifier, 'suffix': suffix, 'filename': filename + suffix,
                                'size': source.stat().st_size, 'uploaded_at': now(), 'draft': parsed['draft']}
                    directory = home / 'uploaded-resumes' / identifier
                    directory.mkdir(parents=True, mode=0o700)
                    target = private_path(home, metadata, 'original')
                    shutil.copyfile(source, target)
                    target.chmod(0o600)
                    private_path(home, metadata, 'text').write_text(text, encoding='utf-8')
                    write_json(home / 'current-resume.json', metadata)
                flash('Resume uploaded. Review the extracted suggestions before importing them.', 'success')
                if not text:
                    flash('No selectable text was found. Your original file is saved; use a text-based resume to prefill your profile.', 'error')
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
                profile.setdefault('personal', {}).update(draft.get('personal', {}))
                for field in ('summary', 'skills'):
                    if draft.get(field):
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
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    try:
        extracted_text = extract_document(Path(sys.argv[2]))
        print(json.dumps({'text': extracted_text, 'draft': suggested_profile(extracted_text)}))
    except ValueError as error:
        print(json.dumps({'error': str(error)}))
        raise SystemExit(2)
    except Exception:
        print(json.dumps({'error': 'This document could not be read. Upload a valid, unencrypted PDF or DOCX.'}))
        raise SystemExit(2)
