"""Source-backed discovery metadata, explicit filters and factual match explanations."""
import hashlib
import json
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

from .core import canonical_url, now
from . import sources

MAX_COMPANIES = 20


def normalize_sources(rows):
    if not isinstance(rows, list) or len(rows) > MAX_COMPANIES:
        raise ValueError('Configure at most 20 company boards per discovery run.')
    clean, seen = [], set()
    for row in rows:
        if not isinstance(row, dict): raise ValueError('Each source must be an object.')
        kind, board = row.get('type', ''), row.get('board', '').strip()
        if kind not in ('greenhouse', 'lever', 'ashby') or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', board):
            raise ValueError('Choose Greenhouse, Lever or Ashby and a public board slug (letters, numbers, _ or -).')
        region = row.get('region', 'global')
        if region not in ('global', 'eu'): raise ValueError('Lever region must be global or eu.')
        company = row.get('company', '').strip()
        if len(company) > 300: raise ValueError('Company name is too long.')
        identity = (kind, board, region)
        if identity not in seen:
            clean.append({'type':kind, 'board':board, 'region':region, 'company':company or board})
            seen.add(identity)
    return clean


def ensure_catalog(store):
    with store.connect() as connection:
        connection.execute('''CREATE TABLE IF NOT EXISTS discovery_metadata (
            job_id TEXT PRIMARY KEY, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            published_at TEXT, source_updated_at TEXT, content_hash TEXT NOT NULL,
            workplace TEXT DEFAULT '', employment_type TEXT DEFAULT '', saved INTEGER DEFAULT 0)''')


def valid_stamp(value):
    if not isinstance(value, str) or not value: return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z','+00:00'))
        if stamp.tzinfo is None: return None
        return stamp.astimezone(timezone.utc).isoformat()
    except ValueError: return None


def ingest_jobs(store, jobs):
    from .web_jobs import validate_job
    ensure_catalog(store)
    keys = []
    for job in jobs[:500]:
        clean = validate_job(job)
        key = store.add(clean)
        digest = hashlib.sha256(json.dumps(clean, sort_keys=True).encode()).hexdigest()
        with store.connect() as connection:
            connection.execute('''INSERT INTO discovery_metadata
                (job_id,first_seen,last_seen,published_at,source_updated_at,content_hash,workplace,employment_type)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET
                last_seen=excluded.last_seen,published_at=excluded.published_at,
                source_updated_at=excluded.source_updated_at,content_hash=excluded.content_hash,
                workplace=excluded.workplace,employment_type=excluded.employment_type''',
                (key, now(), now(), valid_stamp(job.get('published_at')), valid_stamp(job.get('source_updated_at')),
                 digest, str(job.get('workplace',''))[:50], str(job.get('employment_type',''))[:80]))
        keys.append(key)
    return keys


def catalog_rows(store):
    ensure_catalog(store)
    with store.connect() as connection:
        return [dict(row) for row in connection.execute('''SELECT j.*,m.first_seen,m.last_seen,m.published_at,
            m.source_updated_at,m.content_hash,m.workplace,m.employment_type,coalesce(m.saved,0) AS saved
            FROM jobs j LEFT JOIN discovery_metadata m ON m.job_id=j.id ORDER BY j.created DESC,j.id''')]


def set_preference(store, key, action):
    job = store.get(key)
    ensure_catalog(store)
    if action in ('save','unsave'):
        with store.connect() as connection:
            connection.execute('''INSERT INTO discovery_metadata(job_id,first_seen,last_seen,content_hash,saved)
                VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET saved=excluded.saved''',
                (key, now(), now(), '', int(action == 'save')))
    elif action in ('skip', 'restore'):
        expected, target, note = (('DISCOVERED', 'SKIPPED', 'Passed from Browse jobs') if action == 'skip'
                                  else ('SKIPPED', 'DISCOVERED', 'Restored from Browse jobs'))
        with store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            changed = connection.execute('UPDATE jobs SET status=?,updated=?,note=? WHERE id=? AND status=?',
                                         (target, now(), note, key, expected))
            if changed.rowcount != 1:
                raise ValueError('This opportunity has active progress. Reload and manage it in the tracker.')
            connection.execute('INSERT INTO events(job_id,created,status,note) VALUES(?,?,?,?)',
                               (key, now(), target, note))
    else: raise ValueError('Choose save, unsave, skip or restore.')


def terms(value):
    return [part.strip().casefold() for part in str(value or '').split(',') if part.strip()][:30]


def search_groups(value):
    text = re.sub(r'front[\s-]+end', 'frontend', str(value or '').casefold())
    aliases = {'frontend':('frontend','front-end','front end','react','ui engineer'),
               'developer':('developer','engineer'), 'engineer':('engineer','developer')}
    return [aliases.get(word, (word,)) for word in re.findall(r'[\w+#.]+', text)[:30]]


def filter_jobs(rows, filters):
    selected = []
    scope = filters.get('scope', 'title')
    age = filters.get('age', '')
    if age and age not in ('6','24','168','336','720'): raise ValueError('Choose a supported posting age.')
    for row in rows:
        title, description = row.get('title','').casefold(), row.get('description','').casefold()
        text = description if scope == 'description' else title + ' ' + description if scope == 'all' else title
        if terms(filters.get('include')) and not any(term in text for term in terms(filters.get('include'))): continue
        if any(term in text for term in terms(filters.get('exclude'))): continue
        searchable = ' '.join(str(row.get(k) or '') for k in ('title','description','company','location')).casefold()
        if not all(any(term in searchable for term in group) for group in search_groups(filters.get('q'))): continue
        if terms(filters.get('location')) and not any(term in row.get('location','').casefold() for term in terms(filters.get('location'))): continue
        if terms(filters.get('company')) and not any(term in row.get('company','').casefold() for term in terms(filters.get('company'))): continue
        if any(term in row.get('company','').casefold() for term in terms(filters.get('exclude_company'))): continue
        if filters.get('workplace') and (row.get('workplace') or '').casefold() != filters['workplace']: continue
        normalize = lambda value: re.sub(r'[\s_-]', '', str(value or '').lower())
        if filters.get('employment') and normalize(row.get('employment_type')) != normalize(filters['employment']): continue
        if filters.get('saved') == '1' and not row.get('saved'): continue
        if filters.get('status') and row['status'] != filters['status']: continue
        if age:
            stamp = valid_stamp(row.get('published_at'))
            if not stamp: continue
            published = datetime.fromisoformat(stamp)
            current = datetime.now(timezone.utc)
            if not current - timedelta(hours=int(age)) <= published <= current: continue
        selected.append(row)
    groups = search_groups(filters.get('q'))
    if groups:
        def relevance(row):
            title = str(row.get('title') or '').casefold()
            score = sum(10 for group in groups if any(term in title for term in group))
            if any(word in title for word in ('manager', 'director', 'head of')) and not any(
                    word in str(filters.get('q', '')).casefold() for word in ('manager', 'director', 'head')):
                score -= 20
            return score
        selected.sort(key=relevance, reverse=True)
    return selected


def explain_match(job, profile):
    text = ' '.join(str(job.get(k) or '') for k in ('title','description')).casefold()
    skills = [skill for skill in profile.get('skills', []) if isinstance(skill, str) and skill.strip()][:100]
    matched = [skill for skill in skills if re.search(r'(?<!\w)' + re.escape(skill.casefold()) + r'(?!\w)', text)]
    missing = [skill for skill in skills if skill not in matched]
    return {'matched_skills':matched, 'unmentioned_skills':missing,
            'label':f'{len(matched)} of {len(skills)} profile skills mentioned' if skills else 'Add profile skills to compare',
            'unknowns':['Work authorization and sponsorship are not verified.',
                        'Remote work does not establish eligibility in your country.',
                        'Seniority, degree and required years need review in the source description.'],
            'method':'Literal mentions in the title and description; unmentioned skills are not proof of a gap. This is not an ATS or hiring probability.'}


def posting_reference(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.query or parsed.fragment:
        raise ValueError('Use a clean HTTPS public Ashby, Greenhouse or Lever posting URL, or enter the job manually.')
    parts = parsed.path.strip('/').split('/')
    kind = {'jobs.ashbyhq.com':'ashby','boards.greenhouse.io':'greenhouse','job-boards.greenhouse.io':'greenhouse','jobs.lever.co':'lever','jobs.eu.lever.co':'lever'}.get(parsed.hostname)
    if not kind or len(parts) < 2: raise ValueError('Unsupported posting URL. Enter URL, title, company and description manually below.')
    board = parts[0]
    source = normalize_sources([{'type':kind,'board':board,'region':'eu' if parsed.hostname == 'jobs.eu.lever.co' else 'global'}])[0]
    if kind == 'greenhouse':
        if len(parts) != 3 or parts[1] != 'jobs' or not parts[2].isdigit(): raise ValueError('Use the public Greenhouse /company/jobs/id posting URL.')
        identity = parts[2]
    else:
        if len(parts) not in (2,3) or (len(parts) == 3 and parts[2] != 'apply') or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', parts[1]): raise ValueError('Use the public company/posting URL.')
        identity = parts[1]
    return source, identity


def import_posting(url):
    source, identity = posting_reference(url.strip())
    budget = sources.DiscoveryBudget(seconds=15, max_pages=5, max_jobs=500)
    budget_token = sources._budget.set(budget)
    transport_token = sources._safe_transport.set(True)
    try:
        fetch = {'ashby':sources.ashby, 'greenhouse':sources.greenhouse, 'lever':lambda board: sources.lever(board, source['region'])}[source['type']]
        for job in fetch(source['board']):
            path = urlsplit(job['url']).path.strip('/').split('/')
            if job.get('source_id') == identity or identity in path[1:]:
                return job
        raise ValueError('Posting was not found in the bounded public feed. It may be unavailable or outside this sample; enter it manually. No closed status was inferred.')
    finally:
        sources._budget.reset(budget_token)
        sources._safe_transport.reset(transport_token)
