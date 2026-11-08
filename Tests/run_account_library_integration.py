"""Real App + real SharedAPI, temporary loopback only; no game or NAS access."""
import json,os,subprocess,tempfile,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1]
class Handler(BaseHTTPRequestHandler):
    revision=0
    favorite=False
    def game(self):
        return dict(id="a"*32,title="Server game",revision=Handler.revision,favorite=Handler.favorite,collectionIDs=[],playtimeSeconds=123,variants=[dict(id="b"*32,platform="macos",entries=[dict(path="Game.app",backend="native",engine="fixture")],available=True)])
    def log_message(self,*args): pass
    def do_GET(self):
        token=self.headers.get('Authorization','')
        if self.path!='/api/library': self.send_error(404);return
        user='owner' if token=='Bearer owner-token' else 'guest'
        game=self.game()
        self.reply(dict(schemaVersion=2,serverID='fixture-server',user=dict(id=user,username=user,admin=user=='owner'),games=[game] if user=='owner' else [],collections=[]))
    def do_POST(self):
        n=int(self.headers.get('Content-Length',0));body=json.loads(self.rfile.read(n) or b'{}')
        if self.path.endswith('/metadata'):
            if body['revision']!=Handler.revision:self.send_error(409);return
            Handler.favorite=body.get('favorite',Handler.favorite);Handler.revision+=1;self.reply(dict(game=self.game()))
        else:self.reply({})
    def reply(self,data):
        payload=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='gameshelf-account-integration-',dir='/private/tmp') as tmp:
        tmp=Path(tmp);source=(ROOT/'Sources/App.swift').read_text().split('@main struct Bootstrap {',1)[0];(tmp/'App.swift').write_text(source)
        sparkle=Path(os.environ['GAMESHELF_TEST_SPARKLE'])
        sources=sorted(p for p in (ROOT/'Sources').glob('*.swift') if p.name!='App.swift')+[tmp/'App.swift',ROOT/'Tests/AccountLibraryIntegrationTests.swift']
        subprocess.run(['swiftc','-swift-version','5','-module-cache-path',str(ROOT/'build/module-cache'),'-F',str(sparkle),'-framework','Sparkle','-Xlinker','-rpath','-Xlinker',str(sparkle),'-framework','AppKit','-framework','WebKit','-framework','NetFS','-framework','Security','-framework','Network',*map(str,sources),'-o',str(tmp/'test')],check=True)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:subprocess.run([str(tmp/'test'),'--data-dir',str(tmp/'data')],env={**os.environ,'ACCOUNT_FIXTURE_URL':f'http://127.0.0.1:{server.server_port}'},check=True,timeout=60)
        finally:server.shutdown();server.server_close()
