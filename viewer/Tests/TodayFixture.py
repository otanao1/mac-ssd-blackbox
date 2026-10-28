"""Only synthetic records. Use the runtime's schema without opening a monitor DB."""
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone

from blackbox.store import SCHEMA

G = 1024**3
root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
path = root / 'today-fixture.sqlite3'
# A dedicated fixture is rebuilt, never a selected production source.
if path.exists():
    path.unlink()
db = sqlite3.connect(path)
db.executescript(SCHEMA)
day = datetime(2026, 10, 28, tzinfo=timezone.utc).timestamp()

def l1(seconds, free=100, total=256, error=None, path='/fixture/disk'):
    db.execute('INSERT INTO l1 VALUES(?,?,?,?,?,?,?,?)', (day+seconds,path,total*G,(total-free)*G,free*G,free*G,'synthetic',error))

l1(-1); l1(1,100); l1(3600,98); l1(3700,97,error='synthetic error'); l1(86400,80)
# One observed endpoint and a date with no records.
l1(-86400+120,101)

def scan(id, layer='L9', trigger='incident', started=100, state='succeeded', ended=None):
    db.execute('INSERT INTO scans(id,layer,trigger,targets,options,state,created,started,ended,error) VALUES(?,?,?,?,?,?,?,?,?,?)',
               (id,layer,trigger,json.dumps(['/fixture/app/*']),json.dumps({'max_depth':7,'threshold':G}),state,day+started if started is not None else day+90,
                None if started is None else day+started, None if started is None or state in ('pending','running') else day+(ended if ended is not None else started+10),
                'synthetic error' if state=='failed' else None))

scan('shared',trigger='request',state='partial'); scan('l3','L3',started=90); scan('l2','L2',started=80)
scan('periodic','L3','periodic',200); scan('pending','L3',started=None,state='pending')
scan('failed','L3','periodic',210,'failed'); scan('cancelled','L3','periodic',220,'cancelled')
scan('running','L9',started=230,state='running'); scan('manual','L9','manual',240)
scan('not-a-scan','L1','periodic',250); scan('tomorrow',started=86400); scan('yesterday',started=-10)
scan('future-today',started=8000); scan('unknown-trigger','L3','unknown',260); scan('unrelated-request',trigger='request',started=270); scan('crossday',trigger='request',started=280)
for n in range(105):
    db.execute('INSERT INTO results(scan_id,path,kind,bytes,status,error) VALUES(?,?,?,?,?,?)',
               ('shared',f'/fixture/app/item{n:03}', 'directory',n*G,'error' if n==0 else 'ok','synthetic error' if n==0 else None))
change={'path':'/fixture/app/cache','kind':'directory','before_bytes':G,'bytes':3*G,'delta':2*G}
small=change|{'bytes':int(1.3*G),'delta':int(.3*G)}

def incident(id, state, reason=None, seconds=300, archived=False, malformed=False):
    ev={'free_change':-3*G}
    if reason: ev['reason']=reason
    if state=='identified': ev['findings']=[change]
    detection={'threshold':G,'current':{'free':97*G},'rules':[{'rule':'DROP_SPACE_1m','threshold':G,'baseline':{'free':100*G}}]}
    sid='archived' if archived else 'shared'
    step={'layer':'L2','scan_id':'l2','state':'succeeded','compared_count':1,'changes':[small]}
    if reason or state!='unidentified':
        step={'layer':'L9','targets':['/fixture/app/*'], 'scans':{sid:{'state':'partial','started':day+100,'ended':day+110,'targets':json.dumps(['/fixture/app/*']),'options':'{"max_depth":7}'}},
              'target_evaluations':{'/fixture/app/*':{'target':'/fixture/app/*','scan_id':sid,'baseline_id':None if reason=='no_baseline' else 'old', 'scan_complete':reason!='scan_incomplete','baseline_complete':reason!='scan_incomplete',
                 'reason':reason,'changes':[small], 'findings':[change] if state=='identified' else [],'failures':1 if reason=='scan_incomplete' else 0,
                 'failure_excerpts':[{'path':'/fixture/app/blocked','status':'error'}] if reason=='scan_incomplete' else [], 'excerpt':[{'path':'/fixture/app','bytes':3*G,'status':'ok'}]}}}
    if state=='investigating': step={'layer':'L9','scan_id':'running'}
    db.execute('INSERT INTO incidents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (id,day+seconds,'DROP_SPACE',state,'{' if malformed else json.dumps(detection),json.dumps(ev),json.dumps([change] if reason=='no_baseline' else []),json.dumps([step]),'',json.dumps([sid,'old']),'L9' if state=='investigating' else 'done',day+seconds))

incident('identified','identified','growth_identified');incident('identified-shared','identified','growth_identified')
incident('A','unidentified');incident('B','unidentified','no_baseline');incident('C','unidentified','insufficient_evidence');incident('D','unidentified','scan_incomplete')
incident('archived','unidentified','no_baseline',archived=True);incident('active','investigating');incident('old','unidentified',malformed=True)
# Unknown historical format must not silently turn into A.
db.execute("UPDATE incidents SET investigation='[]', evaluation='{}' WHERE id='old'")
incident('future-today','unidentified',seconds=8000);incident('next-day','unidentified',seconds=86400);incident('previous-day','unidentified',seconds=-1)
db.execute("UPDATE incidents SET investigation=?, scan_ids=? WHERE id='previous-day'", (json.dumps([{'layer':'L9','scan_id':'crossday'}]),json.dumps(['crossday','manual'])))
db.commit(); db.close()
print('Created synthetic Today fixture')
