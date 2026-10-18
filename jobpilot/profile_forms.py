"""Bounded normal-form profile parsing; no inferred facts."""
import copy
import json
import re

PERSONAL = ('first_name','last_name','email','phone','city','country','state','address','postal_code','linkedin','github','website')
SECTIONS = {'experience':('company','role','start','end','bullets'), 'projects':('name','url','bullets'), 'education':('degree','institution','dates','bullets')}
CUSTOM = ('notice_period','salary_expectation','salary_currency','contract_preference','work_authorization_answer','sponsorship_answer','relocation_answer')
DOCUMENT_SECTIONS = ('summary','skills','experience','projects','education','languages')
DOCUMENT_HEADER_FIELDS = ('email','phone','city','state','country','linkedin','github','website')

def document_preferences(value=None):
    if value is None: value = {}
    if not isinstance(value,dict): raise ValueError('Document preferences must be an object.')
    supported = {'template','font','size','spacing','sections','alignment','header_fields'}
    if set(value) - supported: raise ValueError('Unsupported document preference.')
    result = {'template':'classic','font':'Vera','size':'10','spacing':'normal','sections':list(DOCUMENT_SECTIONS),
              'alignment':'left','header_fields':[field for field in DOCUMENT_HEADER_FIELDS if field != 'state']}
    result.update(value)
    choices = {'template':('classic','minimal'), 'font':('Vera','Helvetica','Times-Roman','Courier'), 'size':('9','10','11','12'), 'spacing':('compact','normal','relaxed'), 'alignment':('left','justify')}
    for key, allowed in choices.items():
        if result[key] not in allowed: raise ValueError(f'Choose a supported document {key}.')
    sections = result['sections']
    if not isinstance(sections,list) or any(not isinstance(x,str) or x not in DOCUMENT_SECTIONS for x in sections) or len(sections)!=len(set(sections)):
        raise ValueError('Document sections must be unique supported section names.')
    fields = result['header_fields']
    if not isinstance(fields,list) or any(not isinstance(x,str) or x not in DOCUMENT_HEADER_FIELDS for x in fields) or len(fields)!=len(set(fields)):
        raise ValueError('Header fields must be unique supported contact fields.')
    return result

def parse_profile_form(form, previous):
    if 'profile_json' in form:
        p = json.loads(form['profile_json'])
    else:
        p = copy.deepcopy(previous)
        p['personal'] = {k:form.get('personal_'+k,'').strip() for k in PERSONAL}
        p['summary'] = form.get('summary','').strip()
        for key in ('skills','languages','summary_variants'):
            p[key] = [x.strip() for x in form.get(key,'').splitlines() if x.strip()]
        for section, fields in SECTIONS.items():
            if section+'_json' in form:
                p[section] = json.loads(form[section+'_json'])
                continue
            indices = sorted({int(m.group(1)) for k in form if (m:=re.fullmatch(section+r'_(\d+)_[a-z_]+',k))})
            if len(indices)>50: raise ValueError('Use at most 50 entries per section.')
            p[section] = []
            for i in indices:
                item = {k:form.get(f'{section}_{i}_{k}','').strip() for k in fields}
                item['bullets'] = [x.strip() for x in item.get('bullets','').splitlines() if x.strip()]
                if any(item.values()): p[section].append(item)
        if 'summary_variants_json' in form: p['summary_variants'] = json.loads(form['summary_variants_json'])
        p['answers'] = {}
        indices = sorted({int(m.group(1)) for k in form if (m:=re.fullmatch(r'answers_(\d+)_question',k))})
        if len(indices)>100: raise ValueError('Use at most 100 saved answers.')
        for i in indices:
            q = form.get(f'answers_{i}_question','').strip()
            a = form.get(f'answers_{i}_answer','').strip()
            if a and not q: raise ValueError('Each saved answer needs a question label.')
            if q in p['answers']: raise ValueError('Saved answer question labels must be unique.')
            if q: p['answers'][q] = a
        if 'answers_json' in form: p['answers'] = json.loads(form['answers_json'])
        p['custom'] = dict(previous.get('custom',{}))
        for key in CUSTOM:
            if 'custom_'+key in form: p['custom'][key] = form['custom_'+key].strip()
        if 'custom_json' in form: p['custom'] = json.loads(form['custom_json'])
        prefs = dict(previous.get('document_preferences',{}))
        for key in ('template','font','size','spacing','alignment'):
            if 'preference_'+key in form: prefs[key] = form['preference_'+key]
        if 'preference_sections' in form:
            prefs['sections'] = [x.strip() for x in form['preference_sections'].split(',') if x.strip()]
        if 'preference_header_fields' in form:
            prefs['header_fields'] = [x.strip() for x in form['preference_header_fields'].split(',') if x.strip()]
        p['document_preferences'] = document_preferences(prefs)
    if not isinstance(p,dict): raise ValueError('Profile must be an object.')
    p['facts_verified'] = form.get('facts_verified') == 'yes' and form.get('save_action') != 'draft'
    return p

def profile_errors(p):
    errors = {}
    if not p.get('facts_verified'): return errors
    for field in ('first_name','last_name','email','phone','city','country'):
        if not p.get('personal',{}).get(field,'').strip(): errors['personal_'+field] = 'Required to verify your profile.'
    if p.get('personal',{}).get('email') and '@' not in p['personal']['email']: errors['personal_email'] = 'Enter a valid email address.'
    if not p.get('skills'): errors['skills'] = 'Add at least one real skill.'
    for section, fields in SECTIONS.items():
        required = fields[:-1] if section!='projects' else ('name',)
        for i,item in enumerate(p.get(section,[])):
            for field in required:
                if not item.get(field): errors[f'{section}_{i}_{field}'] = 'Required for a verified entry.'
            if section!='education' and not item.get('bullets'): errors[f'{section}_{i}_bullets'] = 'Add at least one factual statement.'
    if not p.get('experience') and not p.get('projects'): errors['experience'] = 'Add work experience or a project before verifying.'
    return errors
