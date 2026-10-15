import csv
import json
import re
import time
from contextvars import ContextVar
from html import unescape
from urllib.request import Request,urlopen
from urllib.parse import quote
from .core import read_json

_budget = ContextVar('jobpilot_discovery_budget', default=None)


class DiscoveryBudget:
    def __init__(self, seconds=30, max_pages=5, max_jobs=500, max_bytes=2 * 1024 * 1024):
        self.deadline = time.monotonic() + seconds
        self.max_pages = max_pages
        self.max_jobs = max_jobs
        self.max_bytes = max_bytes
        self.warnings = []

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Discovery time limit reached; partial results are shown.')
        return remaining

def plain(text): return unescape(re.sub('<[^>]+>',' ',text or ''))
def fetch_json(url):
    request=Request(url,headers={'User-Agent':'JobPilot/1.0 personal-job-search','Accept':'application/json'})
    budget = _budget.get()
    if budget is None:
        with urlopen(request,timeout=30) as response: return json.load(response)
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
             'description':plain(j.get('content','')),'source':'greenhouse'} for j in data['jobs']]

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
                'location':j.get('categories',{}).get('location',''),'description':desc,'source':'lever'})
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
    for source in config.get('sources',[]):
        try:
            budget = _budget.get()
            if budget is not None:
                budget.remaining()
                if len(jobs) >= budget.max_jobs:
                    budget.warnings.append('Discovery job limit reached; partial results shown.')
                    break
            if source['type']=='greenhouse': found=greenhouse(source['board'])
            elif source['type']=='lever': found=lever(source['board'],source.get('region','global'))
            else: raise ValueError('Supported source types: greenhouse, lever')
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


def discover(config, *, budget=None):
    token = _budget.set(budget)
    try:
        jobs, errors = _discover(config)
        return jobs, errors + (budget.warnings if budget is not None else [])
    finally:
        _budget.reset(token)
