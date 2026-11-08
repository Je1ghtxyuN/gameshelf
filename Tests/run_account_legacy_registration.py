"""Temporary source files and actual Swift SharedAPI loopback, no NAS/game launch."""
import json,os,subprocess,tempfile,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1]
S,U,R,O,C,N=['a'*32,'b'*32,'c'*32,'d'*32,'e'*32,'f'*32]
ENTRY=dict(path='Game.exe',backend='wine',engine='Windows')
state=dict(calls=[],mode='valid',added=False)
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,value):
  b=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
 def do_GET(self):
  if self.path=='/api/fixture/state':self.send(dict(calls=state['calls']));return
  assert self.headers.get('Authorization')=='Bearer fixture-owner'
  if self.path=='/api/library':
   variants=[dict(id=O,platform='windows',entries=[ENTRY],available=not state['added'])]
   if state['added']:variants.append(dict(id=N,platform='windows',entries=[ENTRY],available=True))
   self.send(dict(schemaVersion=2,serverID=S,user=dict(id=U,username='Fixture',admin=True),collections=[],games=[dict(id=O,title='Title',revision=0,favorite=False,collectionIDs=[],playtimeSeconds=0,variants=variants)]))
  elif self.path=='/api/admin/library/editions':
   edition=dict(id=O,canonicalID=O,candidateID=C,rootID=R,relative='Title',signature='0'*64,entries=[ENTRY],savePaths=[])
   self.send(dict(editions=[edition]+([dict(edition,id='1'*32,canonicalID='1'*32)] if state['mode']=='ambiguous' else [])))
  else:self.send_error(404)
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}')
  if self.path=='/api/fixture/reset':state.update(calls=[],mode=body['mode'],added=False);self.send(dict(calls=[]));return
  assert self.headers.get('Authorization')=='Bearer fixture-owner'
  state['calls'].append(self.path)
  if self.path==f'/api/admin/roots/{R}/candidates':
   assert body['games'][0]['relative']=='Title' and body['games'][0]['entryPath']=='Game.exe' and len(body['games'][0]['entrySHA256'])==64
   self.send(dict(entryFingerprintsVerified=state['mode']!='fingerprint-fail',candidates=[dict(id=C,root_id=R,title='Title',relative='Title',entries=[ENTRY],available=True,detectedSavePaths=[])]))
  elif self.path==f'/api/admin/candidates/{C}/publish':self.send(dict(id=N,title='Title',entries=[ENTRY]))
  elif self.path==f'/api/admin/library/games/{O}/variants':
   assert body['variantIDs']==[N];self.send(dict(canonicalID=O,variantIDs=[O,N]))
  elif self.path=='/api/admin/library/add':
   assert body==dict(candidateIDs=[C],userID=U,entryPaths={C:'Game.exe'});state['added']=True;self.send(dict(games=[]))
  else:self.send_error(404)
if __name__=='__main__':
 with tempfile.TemporaryDirectory(prefix='gameshelf-legacy-registration-',dir='/private/tmp') as name:
  out=Path(name);game=out/'Games/Title';game.mkdir(parents=True);(game/'Game.exe').write_bytes(b'MZ-test-fixture')
  sources=['Core','VisualNovelProbe','LibraryDiscovery','SharedLibrary','PinnedHTTP','PersonalLibrary','PersonalLibraryMedia','SharedAdministration','SharedScanSelection','SharedEntryFingerprint','AccountLibrary','AccountLibraryMigration','AccountOwnerTransition','AccountLegacyRegistration']
  subprocess.run(['swiftc','-swift-version','5','-module-cache-path',str(ROOT/'build/module-cache'),'-framework','AppKit','-framework','Network',*[str(ROOT/f'Sources/{s}.swift') for s in sources],str(ROOT/'Tests/AccountLegacyRegistrationTests.swift'),'-o',str(out/'test')],check=True)
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
  try:subprocess.run([str(out/'test')],env={**os.environ,'LEGACY_FIXTURE_URL':f'http://127.0.0.1:{server.server_port}','LEGACY_FIXTURE_ROOT':name},check=True,timeout=40)
  finally:server.shutdown();server.server_close()
