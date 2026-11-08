"""Run from repository root. Sends only Windows sources, never repository secrets."""
import subprocess,tarfile,pathlib
root=pathlib.Path(__file__).resolve().parents[2]
archive=root/'Windows/.build/source.tar.gz'
archive.parent.mkdir(exist_ok=True)
with tarfile.open(archive,'w:gz') as t:
 t.add(root/'Windows',arcname='Windows',filter=lambda i:None if any(p in ['bin','obj','.build','build','__pycache__'] for p in i.name.split('/')) else i)
subprocess.run(['scp',str(archive),'win:GameShelfDev/windows-src.tar.gz'],check=True)
# Use encoded static script to avoid shell-quoting differences.
import base64
ps='New-Item -ItemType Directory -Force "$env:USERPROFILE/GameShelfDev/src" | Out-Null; tar -xzf "$env:USERPROFILE/GameShelfDev/windows-src.tar.gz" -C "$env:USERPROFILE/GameShelfDev/src"; & "$env:USERPROFILE/GameShelfDev/src/Windows/Tools/remote-build.ps1"; exit $LASTEXITCODE'
(root/'Windows/build').mkdir(exist_ok=True)
with open(root/'Windows/build/windows-build.log','wb') as log:
 r=subprocess.run(['ssh','win','powershell -NoProfile -EncodedCommand '+base64.b64encode(ps.encode('utf-16le')).decode()],stdout=log,stderr=subprocess.STDOUT)
for project in ['GameShelf.Windows','GameShelf.Core','GameShelf.Core.Tests','GameShelf.Native.Tests','GameShelf.UiFixture','GameShelf.LaunchFixture']:
 subprocess.run(['scp','win:GameShelfDev/src/Windows/'+project+'/packages.lock.json',str(root/'Windows'/project/'packages.lock.json')])
print('Remote build exit:',r.returncode)
raise SystemExit(r.returncode)
