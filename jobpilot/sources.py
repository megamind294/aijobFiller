import csv
import json
import re
import time
import socket
import ssl
import ipaddress
import http.client
import threading
import queue
from contextvars import ContextVar
from html import unescape
from urllib.request import Request,urlopen
from urllib.parse import quote, urlsplit
from .core import read_json

_budget = ContextVar('jobpilot_discovery_budget', default=None)
_safe_transport = ContextVar('jobpilot_safe_transport', default=False)


class DiscoveryBudget:
    def __init__(self, seconds=30, max_pages=5, max_jobs=500, max_bytes=2 * 1024 * 1024, max_companies=20):
        self.deadline = time.monotonic() + seconds
        self.max_pages = max_pages
        self.max_jobs = max_jobs
        self.max_bytes = max_bytes
        self.max_companies = max_companies
        self.warnings = []

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Discovery time limit reached; partial results are shown.')
        return remaining

def plain(text): return unescape(re.sub('<[^>]+>',' ',text or ''))
def fetch_json(url):
    if _safe_transport.get():
        return safe_fetch_json(url)
    request=Request(url,headers={'User-Agent':'JobPilot/1.0 personal-job-search','Accept':'application/json'})
    budget = _budget.get()
    if budget is None:
        budget = DiscoveryBudget()
    with urlopen(request, timeout=min(5, budget.remaining())) as response:
        chunks = []
        size = 0
        reader = getattr(response, 'read1', response.read)
        while True:
            budget.remaining()
            chunk = reader(min(65536, budget.max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > budget.max_bytes:
                raise ValueError('Discovery response limit exceeded (maximum 2 MiB per response).')
        return json.loads(b''.join(chunks))

def greenhouse(board):
    data=fetch_json('https://boards-api.greenhouse.io/v1/boards/'+quote(board,safe='')+'/jobs?content=true')
    return [{'url':j['absolute_url'],'title':j['title'],'company':board,'location':j.get('location',{}).get('name',''),
             'description':plain(unescape(j.get('content',''))),'source':'greenhouse',
             'source_updated_at':j.get('updated_at'), 'source_id':str(j.get('id',''))} for j in data['jobs']]

def ashby(board):
    data = fetch_json('https://api.ashbyhq.com/posting-api/job-board/' + quote(board, safe=''))
    return [{'url':j.get('jobUrl') or j['applyUrl'], 'title':j['title'], 'company':board,
             'location':j.get('location') or '', 'description':j.get('descriptionPlain') or plain(j.get('descriptionHtml','')),
             'source':'ashby', 'published_at':j.get('publishedAt'),
             'workplace':(j.get('workplaceType') or ('remote' if j.get('isRemote') else '')).lower(),
             'employment_type':(j.get('employmentType') or '').lower()}
            for j in data['jobs'] if j.get('isListed', True)]

def lever(board,region='global'):
    host='api.eu.lever.co' if region=='eu' else 'api.lever.co'; results=[]; skip=0
    while True:
        budget = _budget.get()
        if budget is not None:
            budget.remaining()
            if skip // 100 >= budget.max_pages:
                budget.warnings.append(f'Lever board {board} reached its page limit; partial results shown.')
                break
        data=fetch_json(f'https://{host}/v0/postings/{quote(board,safe="")}?mode=json&limit=100&skip={skip}')
        for j in data:
            desc=j.get('descriptionPlain') or plain(j.get('description',''))
            desc+='\n'+'\n'.join(plain(x.get('text',''))+' '+plain(x.get('content','')) for x in j.get('lists',[]))
            desc+='\n'+plain(j.get('additional',''))
            results.append({'url':j.get('applyUrl') or j['hostedUrl'],'title':j['text'],'company':board,
                'location':j.get('categories',{}).get('location',''),'description':desc,'source':'lever',
                'employment_type':j.get('categories',{}).get('commitment','').lower(),
                'workplace':j.get('workplaceType','').lower(), 'source_id':str(j.get('id',''))})
            if budget is not None and len(results) >= budget.max_jobs:
                budget.warnings.append(f'Lever board {board} reached its job limit; partial results shown.')
                return results
        if len(data)<100: break
        skip+=100; time.sleep(.3)
    return results

def read_jobs(path):
    if str(path).lower().endswith('.csv'):
        with open(path,encoding='utf-8-sig',newline='') as f: data=list(csv.DictReader(f))
    else:
        data=read_json(path)
        if isinstance(data,dict): data=data.get('jobs')
    if not isinstance(data,list): raise ValueError('Input must contain a JSON jobs array or CSV rows.')
    for i,j in enumerate(data):
        if not isinstance(j,dict) or not all(isinstance(j.get(k),str) and j[k].strip() for k in ('url','title','company')):
            raise ValueError(f'Job row {i+1} requires url, title, company strings.')
    return data

def _discover(config):
    jobs=[]; errors=[]
    for index, source in enumerate(config.get('sources',[])):
        try:
            budget = _budget.get()
            if budget is not None:
                if index >= budget.max_companies:
                    budget.warnings.append('Company limit reached; partial results shown.')
                    break
                budget.remaining()
                if len(jobs) >= budget.max_jobs:
                    budget.warnings.append('Discovery job limit reached; partial results shown.')
                    break
            if source['type']=='greenhouse': found=greenhouse(source['board'])
            elif source['type']=='lever': found=lever(source['board'],source.get('region','global'))
            elif source['type']=='ashby': found=ashby(source['board'])
            else: raise ValueError('Supported source types: greenhouse, lever, ashby')
            for job in found:
                job['company'] = source.get('company') or job['company']
            if budget is not None and len(jobs) + len(found) > budget.max_jobs:
                found = found[:budget.max_jobs - len(jobs)]
                budget.warnings.append('Discovery job limit reached; partial results shown.')
            jobs.extend(found)
        except Exception as exc: errors.append(f"{source.get('board','?')}: {type(exc).__name__}: {exc}")
        time.sleep(.3)
    include=[s.casefold() for s in config.get('title_keywords',['react','frontend','front-end','front end'])]
    locations=[s.casefold() for s in config.get('location_keywords',['poland','krak','warsaw','warszawa','wroclaw','wrocław','gdansk','gdańsk'])]
    matches=[]
    for j in jobs:
        if include and not any(s in j['title'].casefold() for s in include): continue
        # A bare "remote" is not proof of eligibility from Poland.
        if locations and not any(s in j['location'].casefold() for s in locations): continue
        matches.append(j)
    return matches, errors


def discover(config, *, budget=None, safe_transport=False):
    budget = budget or DiscoveryBudget()
    token = _budget.set(budget)
    transport = _safe_transport.set(safe_transport)
    try:
        jobs, errors = _discover(config)
        return jobs, errors + (budget.warnings if budget is not None else [])
    finally:
        _budget.reset(token)
        _safe_transport.reset(transport)


PUBLIC_API_HOSTS = {'api.ashbyhq.com', 'boards-api.greenhouse.io', 'api.lever.co', 'api.eu.lever.co'}


def _resolve_public(host, budget):
    # Platform getaddrinfo has no timeout. A daemon worker keeps the request
    # bounded even when the system resolver is stalled.
    results = queue.Queue(maxsize=1)
    def resolve():
        try:
            results.put((socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM), None))
        except OSError as exc:
            results.put((None, exc))
    threading.Thread(target=resolve, daemon=True).start()
    try:
        addresses, error = results.get(timeout=min(5, budget.remaining()))
    except queue.Empty:
        raise TimeoutError('Public job host DNS lookup exceeded the time limit.') from None
    if error: raise error
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('Public job host resolved to a non-public address.')
    return addresses


def safe_fetch_json(url):
    """No redirects/proxies; TLS hostname is verified against a pinned public IP.

    URL imports only request generated API endpoints, never the supplied webpage.
    DNS is checked once and the selected address is used for the socket itself.
    """
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in PUBLIC_API_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Unsupported public API endpoint.')
    budget = _budget.get() or DiscoveryBudget(seconds=15)
    addresses = _resolve_public(parsed.hostname, budget)
    budget.remaining()
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=min(5, budget.remaining()))
    raw = socket.create_connection((addresses[0][4][0], 443), timeout=min(5, budget.remaining()))
    tls_socket = None
    response = None
    try:
        raw.settimeout(min(5, budget.remaining()))
        tls_socket = ssl.create_default_context().wrap_socket(raw, server_hostname=parsed.hostname)
        connection.sock = tls_socket
        tls_socket.settimeout(min(5, budget.remaining()))
        connection.request('GET', parsed.path + ('?' + parsed.query if parsed.query else ''), headers={'Accept':'application/json','User-Agent':'JobPilot/1.0'})
        tls_socket.settimeout(min(5, budget.remaining()))
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError('Public job feed returned HTTP ' + str(response.status) + '; redirects are not followed.')
        chunks, size = [], 0
        while True:
            tls_socket.settimeout(min(5, budget.remaining()))
            chunk = response.read1(min(65536, budget.max_bytes + 1 - size))
            if not chunk: break
            size += len(chunk)
            if size > budget.max_bytes: raise ValueError('Discovery response limit exceeded.')
            chunks.append(chunk)
        return json.loads(b''.join(chunks))
    finally:
        if response is not None: response.close()
        connection.close()
        if tls_socket is not None: tls_socket.close()
        raw.close()
