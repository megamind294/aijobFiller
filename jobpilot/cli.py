import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from .core import Store,read_json,write_json,validate_profile,STATUSES
from .sources import read_jobs,discover
from .resume import prepare

ROOT=Path(__file__).resolve().parent

def main(argv=None):
    parser=argparse.ArgumentParser(description='JobPilot: reviewed job applications, factual resumes and Excel tracking.')
    parser.add_argument('--home',default='data',help='Private data directory (default: ./data)')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init',help='Create editable profile and source configuration')
    sub.add_parser('check',help='Validate your profile')
    imp=sub.add_parser('import',help='Import ChatGPT job JSON or CSV'); imp.add_argument('file')
    dis=sub.add_parser('discover',help='Read configured public company job feeds'); dis.add_argument('--config',default=None)
    sub.add_parser('list',help='Show application queue')
    prep=sub.add_parser('prepare',help='Create per-job resume PDF'); prep.add_argument('id'); prep.add_argument('--ai',action='store_true',help='Use paid API for fact ranking (optional)')
    app=sub.add_parser('apply',help='Open and assist one reviewed application'); app.add_argument('id'); app.add_argument('--adapter')
    run=sub.add_parser('run',help='Prepare and review a batch one by one'); run.add_argument('--limit',type=int,default=5); run.add_argument('--adapter'); run.add_argument('--ai',action='store_true')
    mark=sub.add_parser('mark',help='Record an outcome or safely recover a paused application'); mark.add_argument('id'); mark.add_argument('status',choices=sorted(STATUSES)); mark.add_argument('--note',required=True)
    sub.add_parser('export',help='Refresh applications.xlsx')
    watch=sub.add_parser('watch',help='Continuously discover/import jobs; never submit in background')
    watch.add_argument('--interval',type=int,default=1200,help='Seconds between cycles (default 20 minutes)')
    watch.add_argument('--prepare',action='store_true',help='Also generate factual resumes for up to five new jobs per cycle')
    watch.add_argument('--once',action='store_true',help='Run one worker cycle for diagnosis')
    sub.add_parser('health',help='Show worker heartbeat and latest attention request')
    sub.add_parser('stop',help='Request a graceful worker stop after its current cycle')
    sub.add_parser('gmail-login',help='Connect Gmail using separate Desktop OAuth credentials')
    inbox=sub.add_parser('inbox',help='List potentially actionable job emails'); inbox.add_argument('--days',type=int,default=14)
    confirm=sub.add_parser('link-email',help='Link a reviewed confirmation to exactly one job'); confirm.add_argument('id'); confirm.add_argument('message_id')
    demo=sub.add_parser('demo',help='Serve the localhost test application'); demo.add_argument('--port',type=int,default=8765)
    args=parser.parse_args(argv)
    if args.command=='demo':
        from .demo import serve
        serve(args.port); return
    store=Store(Path(args.home).resolve()); profile_path=store.home/'profile.json'
    try:
        if args.command=='init':
            for source,name in [('profile.example.json','profile.json'),('sources.example.json','sources.json')]:
                target=store.home/name
                if not target.exists(): shutil.copyfile(ROOT/'templates'/source,target)
            print('Edit '+str(profile_path)+' with your verified details. Then run check.'); store.export()
        elif args.command=='check': validate_profile(read_json(profile_path)); print('Profile valid. Review resume PDFs before applying.')
        elif args.command=='import':
            jobs=read_jobs(args.file)
            for job in jobs: print(store.add(job),job['title'],job['company'])
            print(store.export())
        elif args.command=='discover':
            config=read_json(args.config or store.home/'sources.json')
            if not config.get('sources'): raise ValueError('Add company board tokens to sources.json first. See README.')
            jobs,errors=discover(config)
            for job in jobs: print(store.add(job),job['title'],job['location'])
            for error in errors: print('Source failed:',error,file=sys.stderr)
            print(f'{len(jobs)} matching jobs; {len(errors)} source errors.'); print(store.export())
            if errors and not jobs: return 1
        elif args.command=='list':
            for job in store.rows(): print(f"{job['id']}  {job['status']:24}  {job['company']} — {job['title']} ({job['location']})")
        elif args.command=='prepare':
            print(prepare(store,args.id,validate_profile(read_json(profile_path)),args.ai)); store.export()
        elif args.command in ('apply','run'):
            from .browser import run_application
            profile=validate_profile(read_json(profile_path)); adapter=read_json(args.adapter) if args.adapter else None
            keys=[args.id] if args.command=='apply' else [j['id'] for j in store.rows() if store.can_apply(j['id'])][:max(0,min(args.limit,20))]
            for key in keys:
                if args.command=='run' and not store.get(key)['resume']: prepare(store,key,profile,args.ai)
                run_application(store,key,profile,adapter)
        elif args.command=='mark':
            old=store.get(args.id)
            print(f"Change {old['company']} / {old['title']}: {old['status']} -> {args.status}")
            if input('Only after checking the portal/email. Type UPDATE: ')!='UPDATE': return
            store.update(args.id,args.status,note='User report: '+args.note); print(store.export())
        elif args.command=='export': print(store.export())
        elif args.command=='watch':
            from .service import Worker
            print('Discovery worker running. No background applications will be submitted. Use stop or Ctrl+C.')
            report=Worker(store,auto_prepare=args.prepare).run(args.interval,args.once)
            if report: print(json.dumps(report,indent=2))
        elif args.command=='health':
            from datetime import datetime,timezone
            for name in ('heartbeat.json','attention.json'):
                path=store.home/name
                if path.exists():
                    info=read_json(path)
                    if name=='heartbeat.json':
                        info['age_seconds']=round((datetime.now(timezone.utc)-datetime.fromisoformat(info['at'])).total_seconds())
                        info['note']='Old heartbeat may mean a stalled/stopped worker; check phase, age and log.'
                    print(name,json.dumps(info,indent=2))
                else: print(name+': no record yet')
        elif args.command=='stop':
            (store.home/'worker.stop').touch(); print('Stop requested. Worker will exit after its current cycle.')
        elif args.command=='gmail-login':
            from .gmail import Gmail
            Gmail(store.home,interactive=True); print('Gmail connected read-only. Token saved locally; keep data private.')
        elif args.command=='inbox':
            from .gmail import Gmail
            print('Candidates only: inspect each message; no automatic status changes.')
            print(json.dumps(Gmail(store.home).action_candidates(args.days),indent=2))
        elif args.command=='link-email':
            from .gmail import Gmail,normalize_message
            job=store.get(args.id); client=Gmail(store.home)
            msg=normalize_message(client.service.users().messages().get(userId='me',id=args.message_id,format='full').execute())
            print('Application:',job['company'],job['title']); print('Email:',msg['sender'],msg['subject']); print(msg['text'][:5000])
            if input('Does this email CONFIRM receipt of this exact job application? Type CONFIRM: ')=='CONFIRM':
                with store.connect() as c:
                    linked=c.execute('SELECT job_id FROM mail_links WHERE message_id=?',(msg['id'],)).fetchone()
                    if linked and linked['job_id']!=args.id: raise ValueError('Email already belongs to a different job.')
                    c.execute('INSERT OR IGNORE INTO mail_links VALUES(?,?,?)',(msg['id'],args.id,str(time.time())))
                # Acknowledgement must not downgrade an interview/offer/rejection.
                status=job['status'] if job['status'] in ('INTERVIEW','OFFER','REJECTED') else 'CONFIRMED'
                store.update(args.id,status,note='User verified confirmation email',evidence='https://mail.google.com/mail/u/0/#all/'+msg['id']); print(store.export())
    except (ValueError,FileNotFoundError,PermissionError,ImportError,RuntimeError) as exc:
        print('Action required:',exc,file=sys.stderr); return 1
    except KeyboardInterrupt: print('\nPaused. Check list before resuming.'); return 130
    return 0
