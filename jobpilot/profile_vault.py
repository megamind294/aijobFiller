"""Account-local versioned source profiles and immutable baseline documents."""
import hashlib
import io
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from xml.sax.saxutils import escape
from flask import abort, flash, redirect, render_template, request, send_file
from .core import now, read_json, write_json
from .profile_forms import document_preferences

class ProfileVault:
    def __init__(self, home):
        self.home = Path(home)
        self.root = self.home/'profile-vault'
        self.root.mkdir(parents=True,exist_ok=True)
        self.path = self.root/'vault.sqlite3'
        with self.connect() as c:
            c.executescript('''CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY,name TEXT NOT NULL,version INTEGER NOT NULL,is_default INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS revisions(profile_id TEXT,version INTEGER,created TEXT,hash TEXT,body TEXT,PRIMARY KEY(profile_id,version));''')
    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path,timeout=30)
        c.row_factory = sqlite3.Row
        try:
            with c: yield c
        finally: c.close()
    def list(self):
        with self.connect() as c: return [dict(x) for x in c.execute('SELECT * FROM profiles WHERE deleted=0 ORDER BY is_default DESC,name')]
    def get(self, key):
        with self.connect() as c:
            row = c.execute('SELECT * FROM profiles WHERE id=? AND deleted=0',(key,)).fetchone()
        if not row: raise ValueError('Profile not found.')
        result = dict(row)
        result.update(self.revision(key,result['version']))
        return result
    def revision(self,key,version):
        with self.connect() as c: row = c.execute('SELECT * FROM revisions WHERE profile_id=? AND version=?',(key,version)).fetchone()
        if not row: raise ValueError('Profile version not found.')
        r = dict(row); r['profile'] = json.loads(r.pop('body')); return r
    def versions(self,key):
        with self.connect() as c: return [dict(x) for x in c.execute('SELECT version,created,hash FROM revisions WHERE profile_id=? ORDER BY version DESC',(key,))]
    def default(self):
        rows = self.list()
        return self.get(rows[0]['id']) if rows else None
    def save(self,profile,profile_id=None,expected_version=None,name='My profile'):
        document_preferences(profile.get('document_preferences'))
        body = json.dumps(profile,sort_keys=True,ensure_ascii=False)
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if profile_id:
                old = c.execute('SELECT * FROM profiles WHERE id=? AND deleted=0',(profile_id,)).fetchone()
                if not old: raise ValueError('Profile not found.')
                if expected_version is not None and old['version']!=int(expected_version): raise ValueError('This profile changed in another tab. Reload before saving.')
                version = old['version']+1
                c.execute('UPDATE profiles SET version=? WHERE id=?',(version,profile_id))
            else:
                profile_id = uuid.uuid4().hex
                version = 1
                default = not c.execute('SELECT 1 FROM profiles WHERE is_default=1 AND deleted=0').fetchone()
                c.execute('INSERT INTO profiles VALUES(?,?,?,?,0)',(profile_id,self.valid_name(name),version,int(default)))
            c.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(profile_id,version,now(),digest,body))
        result = self.get(profile_id)
        if result['is_default']: write_json(self.home/'profile.json',profile)
        return result
    @staticmethod
    def valid_name(name):
        if not isinstance(name,str) or not name.strip() or len(name)>100: raise ValueError('Use a profile name of 1–100 characters.')
        return name.strip()
    def rename(self,key,name):
        self.get(key)
        with self.connect() as c: c.execute('UPDATE profiles SET name=? WHERE id=?',(self.valid_name(name),key))
    def copy(self,key,name): return self.save(self.get(key)['profile'],name=name)
    def select(self,key):
        item = self.get(key)
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('UPDATE profiles SET is_default=0')
            c.execute('UPDATE profiles SET is_default=1 WHERE id=?',(key,))
        write_json(self.home/'profile.json',item['profile'])
    def delete(self,key):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            item = c.execute('SELECT * FROM profiles WHERE id=? AND deleted=0',(key,)).fetchone()
            if not item: raise ValueError('Profile not found.')
            if item['is_default']: raise ValueError('Choose another default before deleting this profile.')
            c.execute('UPDATE profiles SET deleted=1 WHERE id=?',(key,))
    def ensure_legacy(self):
        if not (self.home/'profile.json').exists(): return
        profile = read_json(self.home/'profile.json')
        selected = self.default()
        if not selected: self.save(profile,name='Original profile')
        elif selected['profile'] != profile:
            self.save(profile,selected['id'],expected_version=selected['version'])
    def pdf(self,key,version):
        revision = self.revision(key,version)
        path = self.root/f'{key}-{version}-{revision["hash"][:16]}.pdf'
        if not path.exists():
            render_baseline(revision['profile'],path,revision['profile'].get('document_preferences'))
        return path

def render_baseline(profile,path,preferences=None):
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    pref = document_preferences(preferences)
    if pref['font']=='Vera':
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        if 'JobPilotUnicode' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('JobPilotUnicode',str(Path(__file__).parent/'fonts'/'DejaVuSans.ttf')))
        pref = {**pref, 'font': 'JobPilotUnicode'}
    size = int(pref['size']); leading = size*{'compact':1.15,'normal':1.4,'relaxed':1.7}[pref['spacing']]
    body = ParagraphStyle('body',fontName=pref['font'],fontSize=size,leading=leading,spaceAfter=5,
                          alignment=4 if pref['alignment']=='justify' else 0)
    heading = ParagraphStyle('heading',parent=body,fontSize=size+2,spaceBefore=10,alignment=0,textColor=colors.HexColor('#173d31') if pref['template']=='classic' else colors.black)
    name = ParagraphStyle('name',parent=heading,fontSize=21,leading=25)
    story=[]
    def add(text,style=body):
        if text: story.append(Paragraph(escape(str(text)).replace('\n','<br/>'),style))
    personal = profile.get('personal',{})
    add(' '.join(personal.get(k,'') for k in ('first_name','last_name')),name)
    add(' | '.join(personal.get(k,'') for k in pref['header_fields'] if personal.get(k)))
    for section in pref['sections']:
        value = profile.get(section)
        if not value: continue
        add({'summary':'Profile','experience':'Experience','education':'Education','projects':'Projects','skills':'Skills','languages':'Languages'}[section],heading)
        if section=='summary': add(value)
        elif section in ('skills','languages'): add(', '.join(value))
        else:
            for entry in value:
                keys = {'experience':('role','company','start','end'),'projects':('name','url'),'education':('degree','institution','dates')}[section]
                add(' | '.join(entry.get(k,'') for k in keys if entry.get(k)))
                for bullet in entry.get('bullets',[]): add('• '+bullet)
                story.append(Spacer(1,5))
    if not story: add('Profile draft')
    path = Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    SimpleDocTemplate(str(path),pagesize=A4,leftMargin=40,rightMargin=40,topMargin=35,bottomMargin=35).build(story)
    return path

def register_profile_vault(app):
    from .workspace import current_home
    def vault():
        v = ProfileVault(current_home()); v.ensure_legacy(); return v
    @app.get('/profiles')
    def profiles():
        v = vault()
        return render_template('profile_vault.html',profiles=v.list(),selected=v.default())
    @app.post('/profiles/new')
    def new_profile():
        try:
            item = vault().save({'personal':{},'skills':[],'facts_verified':False},name=request.form.get('name','My profile'))
            return redirect('/profile?profile_id='+item['id'])
        except ValueError as exc: flash(str(exc),'error'); return redirect('/profiles')
    @app.post('/profiles/<key>/<action>')
    def profile_action(key,action):
        try:
            v = vault()
            if action=='default': v.select(key)
            elif action=='rename': v.rename(key,request.form.get('name',''))
            elif action=='copy': v.copy(key,request.form.get('name','Copy'))
            elif action=='delete': v.delete(key)
            else: abort(404)
            flash('Profile updated.','success')
        except ValueError as exc: flash(str(exc),'error')
        return redirect('/profiles')
    @app.get('/profiles/<key>/versions')
    def profile_versions(key):
        v = vault()
        try: item = v.get(key)
        except ValueError: abort(404)
        return render_template('profile_versions.html',item=item,versions=v.versions(key))
    @app.get('/profiles/<key>/versions/<int:version>/resume.pdf')
    def profile_pdf(key,version):
        try: path = vault().pdf(key,version)
        except ValueError: abort(404)
        return send_file(path,mimetype='application/pdf',as_attachment=request.args.get('download')=='1',download_name=f'resume-v{version}.pdf')
    @app.get('/profiles/<key>/versions/<int:version>/preview')
    def profile_preview(key,version):
        try: revision = vault().revision(key,version)
        except ValueError: abort(404)
        return render_template('profile_preview.html',revision=revision,key=key,version=version,preferences=document_preferences(revision['profile'].get('document_preferences')))
