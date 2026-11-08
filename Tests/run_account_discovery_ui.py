#!/usr/bin/env python3
"""Independent AppKit + loopback HTTP acceptance. Does not run the real App."""
import json, os, pathlib, subprocess, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT=pathlib.Path(__file__).resolve().parents[1]
C='c'*32; D='d'*32; E='e'*32; R='f'*32
rows=[dict(id=C,root_id=R,title='A Fixture v1.2',relative='A v1.2',entries=[dict(path='Game.exe',backend='wine',engine='Windows'),dict(path='Game.app',backend='native',engine='macOS')],available=True,detectedSavePaths=[],added=False,ignored=False),dict(id=D,root_id=R,title='B Added',relative='B',entries=[dict(path='Game.exe',backend='wine',engine='Windows')],available=True,detectedSavePaths=[],added=True,ignored=False),dict(id=E,root_id=R,title='C Ignored',relative='C',entries=[dict(path='Game.exe',backend='wine',engine='Windows')],available=False,detectedSavePaths=[],added=False,ignored=True)]
roots=[dict(id=R,title='Fixture Games',path='/srv/fixture-games')]
state=dict(scans=0,adds=0,lastAdd=None,lastIgnore=None,lastRoot=None,requests=[])
lock=threading.Lock()
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self): self.handle_api()
    def do_POST(self): self.handle_api()
    def handle_api(self):
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}')
        with lock:
            if self.path=='/fixture/control':
                if body.get('emptyRoots'):roots.clear()
                if body.get('resetCandidate'):rows[0]['added']=False
                answer=json.loads(json.dumps(state))
            elif self.path=='/fixture/state': answer=json.loads(json.dumps(state))
            else:
                if self.headers.get('Authorization')!='Bearer fixture-token':
                    self.send_error(401); return
                state['requests'].append([self.command,self.path,body])
                if self.command=='GET' and self.path=='/api/admin/roots': answer=dict(roots=list(roots))
                elif self.command=='POST' and self.path=='/api/admin/roots':
                    assert set(body)=={'path','title'}
                    state['lastRoot']=body
                    root=dict(id=R,**body);roots.append(root);answer=root
                elif self.command=='GET' and self.path=='/api/library/candidates': answer=dict(candidates=json.loads(json.dumps(rows)))
                elif self.command=='POST' and self.path=='/api/admin/library/add':
                    assert set(body)=={'candidateIDs','userID','entryPaths'}
                    state['lastAdd']=body;state['adds']+=1
                    for row in rows:
                        if row['id'] in body['candidateIDs']:row['added']=True
                    answer=dict(games=[])
                elif self.command=='POST' and self.path=='/api/library/candidates/ignore':
                    assert set(body)=={'candidateIDs','ignored'}
                    state['lastIgnore']=body
                    for row in rows:
                        if row['id'] in body['candidateIDs']:row['ignored']=body['ignored']
                    answer=dict(ok=True)
                elif self.command=='POST' and self.path==f'/api/admin/roots/{R}/scan':state['scans']+=1;answer=dict(complete=True,discovered=3,issues=[])
                else:self.send_error(404);return
        if self.command=='POST':time.sleep(.15)
        data=json.dumps(answer).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers()
        try:self.wfile.write(data)
        except BrokenPipeError:pass

def main():
    with tempfile.TemporaryDirectory(prefix='account-discovery-ui-',dir='/private/tmp') as temp:
        out=pathlib.Path(temp)
        # Use exact production wire implementations; omit unrelated LibraryStore
        # extensions and production App/bootstrap. No network implementation mock.
        core=(ROOT/'Sources/Core.swift').read_text();(out/'Types.swift').write_text(core[:core.index('enum MZWebProfile')])
        shared=(ROOT/'Sources/SharedLibrary.swift').read_text();a=shared.index('extension LibraryStore');b=shared.index('private final class SharedNoRedirect',a)
        (out/'SharedWire.swift').write_text(shared[:a]+shared[b:])
        (out/'APIValue.swift').write_text('import Foundation\n'+'extension SharedAPI {'+(ROOT/'Sources/PersonalLibraryMedia.swift').read_text().split('extension SharedAPI {',1)[1])
        sources=[out/'Types.swift',out/'SharedWire.swift',out/'APIValue.swift']+[ROOT/p for p in ['Sources/PinnedHTTP.swift','Sources/SharedAdministration.swift','Sources/SharedSessionTokens.swift','Sources/DiscoveryPresentation.swift','Tests/Fixtures/AccountDiscoveryAppFixture.swift','Tests/AccountDiscoveryUITests.swift']]
        implementation=ROOT/'Sources/AccountDiscoveryWindow.swift'
        if implementation.exists():sources.append(implementation)
        binary=out/'AccountDiscoveryUITests'
        subprocess.run(['swiftc','-swift-version','5','-module-cache-path','/private/tmp/account-discovery-module-cache','-framework','AppKit','-framework','Network',*[str(p) for p in sources],'-o',str(binary)],check=True)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:subprocess.run([str(binary)],env={**os.environ,'DISCOVERY_FIXTURE_URL':f'http://127.0.0.1:{server.server_port}'},check=True)
        finally:server.shutdown();server.server_close()
        assert state['scans']==1,state
        print('PASS real SharedAPI loopback requests:',json.dumps(state['requests'],ensure_ascii=False))
if __name__=='__main__':main()
