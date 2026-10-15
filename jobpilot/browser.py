"""Visible, supervised form filling. No autonomous navigation or guessing."""
import re
import time
from pathlib import Path
from .core import origin,write_json

ALIASES={
 'first_name':['first name','given name','firstname','imie','imię'],
 'last_name':['last name','surname','family name','lastname','nazwisko'],
 'full_name':['full name','name','your name'],
 'email':['email','email address','e-mail','your email'],
 'phone':['phone','phone number','telephone','mobile','mobile phone'],
 'city':['city','current city'], 'country':['country','country of residence'],
 'state':['state','state province','province'], 'postal_code':['postal code','zip code','zip'],
 'address':['street address','address line 1'],
 'linkedin':['linkedin','linkedin profile','linkedin url','linkedin profile url'],
 'github':['github','github url','github profile'],
 'website':['website','personal website','portfolio','portfolio url'],
}

def normalized(label): return re.sub(r'[^\w]+',' ',label.casefold()).strip()

def answer_for(label,profile):
    label=normalized(label)
    if not label: return None
    personal=profile.get('personal',{})
    for key,aliases in ALIASES.items():
        if label in aliases:
            if key=='full_name': return ' '.join(filter(None,[personal.get('first_name'),personal.get('last_name')])) or None
            return personal.get(key) or None
    # Exact, user-authored answers only. Never infer work authorization or demographics.
    for question,answer in profile.get('answers',{}).items():
        if normalized(question)==label: return answer
    return None

FIELD_INFO=r'''el => ({
 label: ((el.labels && Array.from(el.labels).map(x=>x.innerText).join(' ')) || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || el.id || '').trim(),
 type: (el.type || '').toLowerCase(), tag: el.tagName.toLowerCase(),
 required: !!el.required, name: el.name || '', id: el.id || ''
})'''

def profile_value(profile,path):
    value=profile
    for key in path.split('.'):
        value=value[int(key)] if isinstance(value,list) else value[key]
    return value

def allowed_frame(frame_url,page_url,extra_origins):
    return frame_url.startswith(('https://','http://')) and origin(frame_url) in {origin(page_url),*extra_origins}

def fill_common(page,profile,resume,extra_origins=()):
    report={'filled':[],'unanswered':[],'errors':[]}
    for frame in page.frames:
        if not allowed_frame(frame.url,page.url,extra_origins): continue
        fields=frame.locator('input, textarea, select')
        for i in range(fields.count()):
            field=fields.nth(i)
            try:
                meta=field.evaluate(FIELD_INFO); label=meta['label']; typ=meta['type']
                if not field.is_enabled() or (typ!='file' and not field.is_visible()): continue
                if typ in ('submit','button','hidden','reset','password'): continue
                if typ=='file':
                    # Don't confuse resume with cover letter, photo, ID or certificates.
                    if re.search(r'\b(resume|cv|résumé)\b',label,re.I) and not re.search(r'cover|photo|passport|certificate',label,re.I):
                        field.set_input_files(str(resume)); report['filled'].append(label+' (file)')
                    else: report['unanswered'].append(label or 'Unlabelled upload')
                    continue
                if typ in ('checkbox','radio'):
                    report['unanswered'].append(label); continue
                answer=answer_for(label,profile)
                if answer is None or str(answer)=='': report['unanswered'].append(label); continue
                if meta['tag']=='select':
                    options=field.locator('option').evaluate_all('(els)=>els.map(e=>({label:e.label,value:e.value}))')
                    match=next((o for o in options if normalized(o['label'])==normalized(str(answer)) or o['value']==str(answer)),None)
                    if not match: report['unanswered'].append(label); continue
                    field.select_option(value=match['value'])
                else:
                    # Do not overwrite a human answer entered earlier in the session.
                    if field.input_value().strip(): continue
                    field.fill(str(answer))
                report['filled'].append(label)
            except Exception as exc: report['errors'].append({'field':locals().get('label','unknown'),'error':type(exc).__name__})
    return report

def adapter_scope(page,adapter):
    if not adapter: return page
    if origin(page.url)!=adapter.get('origin'):
        raise ValueError('Adapter origin does not match the current page. Navigate to its configured page first.')
    frame_url=adapter.get('frame_url_contains')
    if frame_url:
        frames=[f for f in page.frames if frame_url in f.url]
        if len(frames)!=1: raise ValueError('Adapter iframe must match exactly one frame.')
        # iframe origin is separately explicit; a substring alone never grants access.
        if origin(frames[0].url)!=adapter.get('frame_origin'): raise ValueError('Adapter iframe origin mismatch.')
        return frames[0]
    return page

def fill_adapter(page,adapter,profile,resume):
    scope=adapter_scope(page,adapter); filled=[]
    for mapping in adapter.get('fields',[]):
        loc=scope.locator(mapping['selector'])
        if loc.count()!=1: raise ValueError('Custom selector must identify one control: '+mapping['selector'])
        if mapping.get('kind')=='resume': loc.set_input_files(str(resume))
        else:
            value=profile_value(profile,mapping['profile_key'])
            if value is None or value=='': continue
            kind=mapping.get('kind','text')
            if kind=='select': loc.select_option(label=str(value))
            elif kind=='checkbox':
                if type(value) is not bool: raise ValueError('Checkbox answer must be a verified boolean.')
                loc.set_checked(value)
            elif kind=='text': loc.fill(str(value))
            else: raise ValueError('Unknown adapter field kind')
        filled.append(mapping['selector'])
    return filled

def confirmed(page,adapter):
    scope=adapter_scope(page,adapter)
    selector=adapter.get('success_selector'); expected=adapter.get('success_text')
    if not selector or not expected: return False
    loc=scope.locator(selector)
    return loc.count()==1 and loc.is_visible() and expected.casefold() in loc.inner_text().casefold()

def submit_once(page,adapter,store,key):
    """Caller already received explicit interactive SUBMIT for this application."""
    if store.get(key)['status']!='FILLING':
        raise ValueError('Submission requires the active browser claim. Check application history.')
    scope=adapter_scope(page,adapter)
    if not adapter.get('submit_selector') or not adapter.get('success_selector') or not adapter.get('success_text'):
        raise ValueError('Configure submit_selector, success_selector and success_text or submit manually.')
    if confirmed(page,adapter): raise ValueError('Receipt already visible; do not submit again.')
    loc=scope.locator(adapter['submit_selector'])
    if loc.count()!=1 or not loc.is_visible() or not loc.is_enabled(): raise ValueError('Submit button is ambiguous or unavailable.')
    store.update(key,'SUBMITTING',note='User explicitly requested submission; automatic retry disabled')
    try:
        loc.click(timeout=15000)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            try:
                if confirmed(page,adapter):
                    store.update(key,'SUBMITTED',note='Visible receipt confirmed',evidence=page.url+' | '+adapter['success_text'])
                    return True
            except Exception: pass
            page.wait_for_timeout(500)
    except Exception:
        # Network errors after a click can occur AFTER a successful submission.
        store.update(key,'SUBMISSION_UNCONFIRMED',note='Submission attempted; receipt not verified. Check portal before any retry.')
        return False
    store.update(key,'SUBMISSION_UNCONFIRMED',note='No configured receipt detected. Check portal before any retry.')
    return False

def fill_otp(page,adapter,mail,since):
    adapter_scope(page,adapter)
    rule=adapter.get('otp',{})
    if not rule.get('selector'): raise ValueError('Configure this site OTP selector first.')
    code=mail.otp(rule,since)
    # Gmail may wait: never reuse a page scope validated before that wait.
    scope=adapter_scope(page,adapter)
    target=scope.locator(rule['selector'])
    if target.count()!=1: raise ValueError('OTP selector must identify exactly one control.')
    target.fill(code)

def captcha_required(page):
    # Observe only. Tokens are produced by the provider/user, never by this tool.
    token_selectors='textarea[name="g-recaptcha-response"], textarea[name="h-captcha-response"], input[name="cf-turnstile-response"]'
    for frame in page.frames:
        try:
            tokens=frame.locator(token_selectors)
            if any(tokens.nth(i).input_value().strip() for i in range(tokens.count())): continue
            widgets=frame.locator('iframe[title*="recaptcha" i], iframe[title*="captcha" i], iframe[src*="challenges.cloudflare.com"], .g-recaptcha, .h-captcha, .cf-turnstile')
            if any(widgets.nth(i).is_visible() for i in range(widgets.count())): return True
            challenge=frame.get_by_text(re.compile(r'^(verify you are human|checking your browser|complete the security check)[.!]?$',re.I))
            if any(challenge.nth(i).is_visible() for i in range(challenge.count())): return True
        except Exception: continue
    return False

def challenge_gate(page,home,key):
    if not captcha_required(page): return True
    from .service import notify
    notify(home,'CAPTCHA','CAPTCHA/security check detected. Solve it yourself in the open browser.',key)
    # User confirms provider completion; no solver service or bypass is used.
    return input('After solving it, type SOLVED to continue, or press Enter to pause this action: ').strip()=='SOLVED'

def run_application(store,key,profile,adapter=None):
    from .service import ProcessLock
    with ProcessLock(store.home/'browser.lock'):
        return _run_application(store,key,profile,adapter)

def _run_application(store,key,profile,adapter=None):
    from playwright.sync_api import sync_playwright
    from .gmail import Gmail
    job=store.get(key); resume=Path(job['resume'])
    if not job['resume'] or not resume.is_file(): raise ValueError('Prepare this job resume first.')
    adapter=adapter or {}; store.claim(key); out=store.home/'reviews'/key
    # Timestamp is set by the user immediately before requesting a new OTP.
    otp_since=None
    try:
        with sync_playwright() as p:
            context=p.chromium.launch_persistent_context(str(store.home/'browser'),headless=False,accept_downloads=False)
            page=context.new_page(); page.goto(job['url'],wait_until='domcontentloaded',timeout=45000)
            print('Browser opened. Navigate to the application form and sign in if needed.')
            print('Commands: fill, otp-start, otp, receipt, submitted, submit, pause, quit')
            while True:
                action=input('jobpilot> ').strip().lower()
                if action in ('fill','otp','submit') and not challenge_gate(page,store.home,key): continue
                if action=='fill':
                    # Validate the adapter before any custom origin can receive profile data.
                    if adapter: adapter_scope(page,adapter)
                    extra=[adapter['frame_origin']] if adapter.get('frame_origin') else []
                    report=fill_common(page,profile,resume,extra)
                    if adapter: report['custom']=fill_adapter(page,adapter,profile,resume)
                    write_json(out/'fields.json',report)
                    store.update(key,'FILLING',note=f"Filled {len(report['filled'])} controls. Review unanswered fields in browser.")
                    print(report)
                elif action=='otp-start':
                    otp_since=time.time(); print('Now request a NEW code on the website; then enter otp here.')
                elif action=='otp':
                    if otp_since is None: print('Use otp-start immediately before requesting a fresh code.'); continue
                    adapter_scope(page,adapter); rule=adapter.get('otp',{})
                    if not rule.get('selector'): print('Configure this site\'s exact OTP rule and selector first.'); continue
                    fill_otp(page,adapter,Gmail(store.home),otp_since)
                    otp_since=None
                    print('Matching fresh code filled. Review and continue in browser.')
                elif action=='receipt':
                    if adapter and confirmed(page,adapter):
                        store.update(key,'SUBMITTED',note='Visible receipt confirmed after manual submission',evidence=page.url+' | '+adapter['success_text']); break
                    print('No configured receipt visible. Use submitted to record a manually verified receipt.')
                elif action=='submitted':
                    evidence=input('Paste the actual receipt/reference (do not enter an OTP): ').strip()
                    if evidence and input('Did the site confirm receipt? Type CONFIRMED: ')=='CONFIRMED':
                        store.update(key,'SUBMITTED',note='User verified receipt',evidence=evidence); break
                elif action=='submit':
                    print(f"Submit {job['title']} at {job['company']} using {resume.name}?")
                    if input('Review the filled page first. Type SUBMIT to send: ')=='SUBMIT':
                        submit_once(page,adapter,store,key); break
                elif action in ('pause','quit'):
                    # Manual submission may have happened outside the command loop.
                    state=input('If you clicked submit, type SENT; otherwise press Enter: ').strip()
                    if state=='SENT': store.update(key,'SUBMISSION_UNCONFIRMED',note='Manual submit reported without receipt; check portal')
                    else: store.update(key,'NEEDS_REVIEW',note='Paused by user. Form values may need refilling on restart.')
                    break
                else: print('Commands: fill, otp-start, otp, receipt, submitted, submit, pause, quit')
            context.close()
    except (Exception,KeyboardInterrupt):
        if store.get(key)['status'] not in ('SUBMITTED','SUBMITTING','SUBMISSION_UNCONFIRMED'):
            store.update(key,'SUBMISSION_UNCONFIRMED',note='Browser interrupted; manual submission may have occurred. Check portal before restarting.')
        raise
    finally: store.export()
