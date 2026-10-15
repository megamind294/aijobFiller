"""Read-only Gmail; OTP matching never reads unrelated messages into the tracker."""
import base64
import os
import re
import time
from email.utils import getaddresses,parseaddr
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit
from .core import write_json

SCOPES=['https://www.googleapis.com/auth/gmail.readonly']

def matching_code(message, rule, since):
    if float(message.get('timestamp',0))<since: return None
    if message.get('sender','').casefold()!=rule['sender'].casefold(): return None
    if rule['recipient'].casefold() not in [x.casefold() for x in message.get('recipients',[])]: return None
    if rule.get('subject_contains','').casefold() not in message.get('subject','').casefold(): return None
    # Default avoids treating years/phone numbers as codes. Configure pattern only for known sites.
    codes=re.findall(rule.get('pattern',r'(?<!\d)(\d{6})(?!\d)'),message.get('text',''))
    codes=list(dict.fromkeys(c for c in codes if isinstance(c,str)))
    return codes[0] if len(codes)==1 else None

def message_body(part):
    pieces=[]
    if part.get('mimeType') in ('text/plain','text/html'):
        data=part.get('body',{}).get('data','')
        if data:
            text=base64.urlsafe_b64decode(data+'='*((-len(data))%4)).decode('utf-8',errors='replace')
            if part['mimeType']=='text/html': text=unescape(re.sub('<[^>]+>',' ',text))
            pieces.append(text)
    for child in part.get('parts',[]): pieces.append(message_body(child))
    return '\n'.join(pieces)

def normalize_message(msg):
    headers={h['name'].casefold():h['value'] for h in msg.get('payload',{}).get('headers',[])}
    return {'id':msg['id'],'sender':parseaddr(headers.get('from',''))[1],
        'recipients':[a for _,a in getaddresses([headers.get('to',''),headers.get('delivered-to','')])],
        'subject':headers.get('subject',''),'text':message_body(msg.get('payload',{})),
        'timestamp':int(msg.get('internalDate',0))/1000}

def verification_links(message, hosts):
    allowed={h.casefold() for h in hosts}
    urls=re.findall(r'https://[^\s<>"\']+',message.get('text',''))
    return list(dict.fromkeys(u for u in urls if urlsplit(u).hostname and urlsplit(u).hostname.casefold() in allowed))

class Gmail:
    def __init__(self,home,interactive=False):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        home=Path(home); home.mkdir(parents=True,exist_ok=True)
        token=home/'gmail-token.json'; credentials=home/'credentials.json'
        creds=Credentials.from_authorized_user_file(str(token),SCOPES) if token.exists() else None
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
            elif interactive:
                if not credentials.exists(): raise ValueError(f'Put your Google Desktop OAuth credentials at {credentials}')
                creds=InstalledAppFlow.from_client_secrets_file(str(credentials),SCOPES).run_local_server(port=0)
            else: raise ValueError('Run gmail-login first. A ChatGPT Gmail connection cannot authorize this script.')
            fd=os.open(token,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'w',encoding='utf-8') as f: f.write(creds.to_json())
            try: os.chmod(token,0o600)
            except OSError: pass
        self.service=build('gmail','v1',credentials=creds,cache_discovery=False)
    def search(self,query,limit=20):
        ids=self.service.users().messages().list(userId='me',q=query,maxResults=limit).execute().get('messages',[])
        return [normalize_message(self.service.users().messages().get(userId='me',id=m['id'],format='full').execute()) for m in ids]
    def otp(self,rule,since,timeout=90):
        for key in ('sender','recipient','subject_contains'):
            if not rule.get(key): raise ValueError('OTP rule requires sender, recipient and subject_contains.')
        if not re.fullmatch(r'[^\s"(){}]+@[^\s"(){}]+',rule['sender']): raise ValueError('OTP sender must be an exact email address.')
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            messages=self.search(f'after:{int(since)} from:({rule["sender"]})',10)
            matches=[(m,matching_code(m,rule,since)) for m in messages]
            matches=[(m,c) for m,c in matches if c]
            if matches:
                # Multiple fresh matching messages may belong to separate login sessions.
                unique={c for _,c in matches}
                if len(unique)!=1: raise ValueError('Multiple different fresh OTPs found. Enter the correct code manually.')
                return matches[0][1]
            time.sleep(3)
        raise TimeoutError('No unambiguous fresh OTP matched this site, recipient and subject.')
    def action_candidates(self,days=14):
        # Only suggestions; application state is never inferred from a bulk job email.
        query=f'newer_than:{days}d -in:sent -in:trash -in:spam -category:promotions {{interview assessment recruiter "documents required" "schedule a call" "rekrutacja" "rozmowa"}}'
        return [{'id':m['id'],'sender':m['sender'],'subject':m['subject'],
                 'url':'https://mail.google.com/mail/u/0/#all/'+m['id']} for m in self.search(query,50)]
