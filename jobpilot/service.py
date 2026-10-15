"""Continuous read/prepare worker. This module never submits applications."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import time
from .core import now,read_json,write_json,validate_profile
from .sources import read_jobs,discover
from .resume import prepare

class ProcessLock:
    """OS-owned lock: released on crash; no stale PID guessing."""
    def __init__(self,path): self.path=Path(path); self.handle=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.handle=open(self.path,'a+b')
        self.handle.seek(0,2)
        if self.handle.tell()==0: self.handle.write(b'0'); self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.handle.close(); self.handle=None
            raise RuntimeError(f'Another process already holds {self.path.name}. Stop it before restarting.') from None
        return self
    def __exit__(self,*args):
        if self.handle:
            if os.name=='nt':
                import msvcrt
                self.handle.seek(0); msvcrt.locking(self.handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(),fcntl.LOCK_UN)
            self.handle.close(); self.handle=None

def backoff_seconds(failures): return min(900,60*2**min(max(0,failures-1),4))

def notify(home,event,message,job_id=None):
    write_json(Path(home)/'attention.json',{'at':now(),'event':event,'message':message,'job_id':job_id})
    print('\a'+message,flush=True)
    if os.name=='nt':
        try:
            import winsound
            winsound.MessageBeep()
        except (ImportError,RuntimeError): pass

class Worker:
    def __init__(self,store,auto_prepare=False,prepare_limit=5):
        self.store=store; self.home=store.home; self.auto_prepare=auto_prepare
        self.prepare_limit=max(1,min(prepare_limit,20))
        self.logger=logging.getLogger('jobpilot.'+str(self.home))
        self.logger.setLevel(logging.INFO); self.logger.propagate=False
        if not self.logger.handlers:
            handler=RotatingFileHandler(self.home/'worker.log',maxBytes=1_000_000,backupCount=3,encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            self.logger.addHandler(handler)
    def heartbeat(self,phase,**fields):
        write_json(self.home/'heartbeat.json',{'at':now(),'pid':os.getpid(),'phase':phase,**fields})
    def cycle(self):
        report={'imported':0,'prepared':0,'errors':[]}
        self.heartbeat('working')
        incoming=self.home/'incoming'; incoming.mkdir(exist_ok=True)
        for path in sorted(incoming.iterdir()):
            if path.suffix.lower() not in ('.json','.csv') or not path.is_file(): continue
            try:
                jobs=read_jobs(path)
                for j in jobs: self.store.add(j)
                report['imported']+=len(jobs)
                destination=self.home/'processed-imports'; destination.mkdir(exist_ok=True)
                path.rename(destination/(str(time.time_ns())+'-'+path.name))
            except Exception as exc:
                report['errors'].append('Import '+path.name+': '+type(exc).__name__)
                destination=self.home/'failed-imports'; destination.mkdir(exist_ok=True)
                try: path.rename(destination/(str(time.time_ns())+'-'+path.name))
                except OSError: pass
        try:
            config=read_json(self.home/'sources.json')
            if config.get('sources'):
                jobs,errors=discover(config)
                report['errors'].extend(errors)
                for j in jobs: self.store.add(j)
                report['imported']+=len(jobs)
        except Exception as exc: report['errors'].append('Discovery: '+type(exc).__name__)
        if self.auto_prepare:
            try:
                profile=validate_profile(read_json(self.home/'profile.json'))
                candidates=[j for j in self.store.rows() if j['status']=='DISCOVERED' and j['description'].strip()][:self.prepare_limit]
                for job in candidates:
                    try: prepare(self.store,job['id'],profile); report['prepared']+=1
                    except Exception as exc: report['errors'].append('Prepare '+job['id']+': '+type(exc).__name__)
            except Exception as exc: report['errors'].append('Profile: '+type(exc).__name__+'; run check to see details')
        try: self.store.export()
        except Exception as exc: report['errors'].append('Excel export: '+type(exc).__name__+'; close Excel and retry')
        self.logger.info('Cycle imported=%d prepared=%d errors=%s',report['imported'],report['prepared'],report['errors'])
        for handler in self.logger.handlers: handler.close()
        self.heartbeat('idle',**report)
        return report
    def run(self,interval=1200,once=False):
        if interval<60: raise ValueError('Minimum discovery interval is 60 seconds.')
        with ProcessLock(self.home/'worker.lock'):
            (self.home/'worker.stop').unlink(missing_ok=True)
            failures=0
            try:
                while True:
                    try: report=self.cycle()
                    except Exception as exc:
                        self.logger.error('Cycle failed: %s',type(exc).__name__)
                        report={'errors':[type(exc).__name__]}
                    failures=failures+1 if report['errors'] else 0
                    if once: return report
                    delay=max(interval,backoff_seconds(failures)) if failures else interval
                    deadline=time.monotonic()+delay
                    self.heartbeat('waiting',next_run_epoch=time.time()+delay,errors=report['errors'])
                    while time.monotonic()<deadline:
                        if (self.home/'worker.stop').exists(): return
                        time.sleep(min(5,max(0,deadline-time.monotonic())))
                        self.heartbeat('waiting',next_run_epoch=time.time()+max(0,deadline-time.monotonic()),errors=report['errors'])
            except KeyboardInterrupt: return
            finally:
                self.heartbeat('stopped')
                for handler in self.logger.handlers: handler.close()
