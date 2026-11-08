"""Real App regression suite; temporary source copy and authenticated loopback only.
Run: python3 Tests/run_mac_integration_review.py
Optionally set GAMESHELF_TEST_SPARKLE to an existing Sparkle framework parent.
No downloads, installed application startup, game launches or NAS access.
"""
import copy,hashlib,json,os,pathlib,socket,subprocess,tempfile,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
ROOT=pathlib.Path(__file__).resolve().parents[1]
S=dict(revoked=False,base=123.,timePosts=0,dropNextTimeACK=False,holdTime=False,holdFinalLibrary=False,held=False,holdReads=0,trackHoldReads=False,laterBase=50.,timeTrace=[],durableAttemptChecks=0)
EVENTS=set();LOCK=threading.Lock();RELEASE=threading.Event();DATA_ROOT=None
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def reply(self,data,code=200):
  raw=json.dumps(data).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers()
  try:self.wfile.write(raw)
  except BrokenPipeError:pass
 def doc(self):
  game=dict(id='a'*32,title='Server game',revision=0,favorite=False,collectionIDs=[],playtimeSeconds=S['base'],variants=[dict(id='b'*32,platform='macos',entries=[dict(path='Game.app',backend='native',engine='fixture')],available=True)])
  later=dict(id='c'*32,title='Later game',revision=0,favorite=False,collectionIDs=[],playtimeSeconds=S['laterBase'],variants=[dict(id='d'*32,platform='macos',entries=[dict(path='Later.app',backend='native',engine='fixture')],available=True)])
  return dict(schemaVersion=2,serverID='fixture-server',user=dict(id='owner',username='Owner',admin=True),games=([later] if S['revoked'] else [game,later]),collections=[])
 def do_GET(self):
  if self.headers.get('Authorization')!='Bearer owner-token':self.reply({},401);return
  if self.path!='/api/library':self.reply({},404);return
  with LOCK:
   data=copy.deepcopy(self.doc());hold=False
   if S['trackHoldReads']:S['holdReads']+=1
   if S['holdFinalLibrary'] and S['holdReads']==3:S['held']=True;hold=True
  if hold:
   RELEASE.wait(8)
   with LOCK:S['held']=False;S['holdFinalLibrary']=False
  self.reply(data)
 def do_DELETE(self):
  if self.headers.get('Authorization')!='Bearer owner-token':self.reply({},401);return
  if self.path!='/api/library/games/'+('a'*32):self.reply({},404);return
  with LOCK:S['revoked']=True
  self.reply({})
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
  if self.path=='/review/control':
   with LOCK:
    if body.pop('release',False):RELEASE.set()
    if body.get('holdFinalLibrary'):S['holdReads']=0;S['trackHoldReads']=True;RELEASE.clear()
    S.update(body);answer=copy.deepcopy(S)
   self.reply(answer);return
  if self.headers.get('Authorization')!='Bearer owner-token':self.reply({},401);return
  if self.path.endswith('/playtime'):
   canonical=self.path.split('/')[-2]
   assert canonical in ['a'*32,'c'*32]
   # The real controller must persist uncertainty BEFORE every network POST.
   queues=list(DATA_ROOT.glob('AccountLibraries/*/Library/playtime-outbox.json'))
   assert len(queues)==1
   pending=json.loads(queues[0].read_text())['pending']
   event=next(event for event in pending if event['eventID']==body['eventID'])
   assert event.get('attempted') is True, 'POST started before durable markAttempted'
   with LOCK:
    S['durableAttemptChecks']+=1
    S['timePosts']+=1
    if canonical=='c'*32:
     if body['eventID'] not in EVENTS:EVENTS.add(body['eventID']);S['laterBase']+=body['elapsedSeconds']
     code=200;answer=dict(playtimeSeconds=S['laterBase'],lastPlayed=2007)
    elif S['revoked']:code=403;answer={}
    elif S['dropNextTimeACK']:
     if body['eventID'] not in EVENTS:EVENTS.add(body['eventID']);S['base']+=body['elapsedSeconds']
     S['timeTrace'].append(dict(canonical=canonical,status='lost-ack'));S['dropNextTimeACK']=False;self.close_connection=True
     try:self.connection.shutdown(socket.SHUT_RDWR)
     except OSError:pass
     self.connection.close();return
    elif S['holdTime']:code=503;answer={}
    else:
     if body['eventID'] not in EVENTS:EVENTS.add(body['eventID']);S['base']+=body['elapsedSeconds']
     code=200;answer=dict(playtimeSeconds=S['base'],lastPlayed=1010)
    S['timeTrace'].append(dict(canonical=canonical,status=code))
   self.reply(answer,code);return
  self.reply({})
with tempfile.TemporaryDirectory(prefix='mac-integration-review-',dir='/private/tmp') as temp:
 out=pathlib.Path(temp);DATA_ROOT=out/'data';sources=[];hashes={}
 for src in (ROOT/'Sources').glob('*.swift'):
  raw=src.read_bytes();hashes[src.name]=hashlib.sha256(raw).hexdigest();text=raw.decode()
  if src.name=='App.swift':text=text.split('@main struct Bootstrap {',1)[0]
  dest=out/src.name;dest.write_text(text);sources.append(dest)
 pathlib.Path('/private/tmp/mac-integration-regression-source-hashes.json').write_text(json.dumps(hashes,indent=2))
 candidates=[pathlib.Path(os.environ['GAMESHELF_TEST_SPARKLE'])] if os.environ.get('GAMESHELF_TEST_SPARKLE') else [ROOT/'build/sparkle/distribution']
 sparkle=next((path for path in candidates if (path/'Sparkle.framework').exists()),None)
 if sparkle is None:raise SystemExit('Set GAMESHELF_TEST_SPARKLE to an existing Sparkle distribution; no dependencies are downloaded')
 args=['swiftc','-swift-version','5','-whole-module-optimization','-Onone','-module-cache-path','/private/tmp/mac-review-module-cache','-F',str(sparkle),'-framework','Sparkle','-Xlinker','-rpath','-Xlinker',str(sparkle)]
 for framework in ['AppKit','WebKit','NetFS','Security','Network']:args+=['-framework',framework]
 args += [*map(str,sources),str(ROOT/'Tests/MacIntegrationReviewTests.swift'),'-o',str(out/'review')]
 subprocess.run(args,check=True)
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
 try:subprocess.run([str(out/'review'),'--data-dir',str(DATA_ROOT)],env={**os.environ,'ACCOUNT_REVIEW_URL':f'http://127.0.0.1:{server.server_port}'},check=True,timeout=75)
 finally:RELEASE.set();server.shutdown();server.server_close()
 assert S['laterBase']==57, S
 assert S['base']==133, S
 assert S['durableAttemptChecks']>=4, S
 print('PASS authenticated fixture: rejected event followed by later-game success; exactly-once server totals; every POST had a durable attempted marker')
