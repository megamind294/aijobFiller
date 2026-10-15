import csv
import json
import re
import time
from html import unescape
from urllib.request import Request,urlopen
from urllib.parse import quote
from .core import read_json

def plain(text): return unescape(re.sub('<[^>]+>',' ',text or ''))
def fetch_json(url):
    request=Request(url,headers={'User-Agent':'JobPilot/1.0 personal-job-search','Accept':'application/json'})
    with urlopen(request,timeout=30) as response: return json.load(response)

def greenhouse(board):
    data=fetch_json('https://boards-api.greenhouse.io/v1/boards/'+quote(board,safe='')+'/jobs?content=true')
    return [{'url':j['absolute_url'],'title':j['title'],'company':board,'location':j.get('location',{}).get('name',''),
             'description':plain(j.get('content','')),'source':'greenhouse'} for j in data['jobs']]

def lever(board,region='global'):
    host='api.eu.lever.co' if region=='eu' else 'api.lever.co'; results=[]; skip=0
    while True:
        data=fetch_json(f'https://{host}/v0/postings/{quote(board,safe="")}?mode=json&limit=100&skip={skip}')
        for j in data:
            desc=j.get('descriptionPlain') or plain(j.get('description',''))
            desc+='\n'+'\n'.join(plain(x.get('text',''))+' '+plain(x.get('content','')) for x in j.get('lists',[]))
            desc+='\n'+plain(j.get('additional',''))
            results.append({'url':j.get('applyUrl') or j['hostedUrl'],'title':j['text'],'company':board,
                'location':j.get('categories',{}).get('location',''),'description':desc,'source':'lever'})
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

def discover(config):
    jobs=[]; errors=[]
    for source in config.get('sources',[]):
        try:
            if source['type']=='greenhouse': found=greenhouse(source['board'])
            elif source['type']=='lever': found=lever(source['board'],source.get('region','global'))
            else: raise ValueError('Supported source types: greenhouse, lever')
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
