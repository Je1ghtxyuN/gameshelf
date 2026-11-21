"""Send Windows sources to the development workspace and build over SSH."""
import base64
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import tarfile

root = Path(__file__).resolve().parents[2]
configured = os.environ.get('GAMESHELF_BUILD_ROOT') or r'D:\GameShelfDev'
if not re.fullmatch(r'[A-Za-z]:[\\/][A-Za-z0-9_. /\\-]+', configured):
    raise SystemExit('GAMESHELF_BUILD_ROOT must be an absolute Windows directory.')
workspace = PureWindowsPath(configured)
if not workspace.is_absolute() or '..' in workspace.parts:
    raise SystemExit('GAMESHELF_BUILD_ROOT must be an absolute Windows directory.')
remote = f'win:{workspace.as_posix()}'


def powershell(script, **kwargs):
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    return subprocess.run(['ssh', 'win', 'powershell -NoProfile -EncodedCommand ' + encoded], **kwargs)


setup = f"$env:GAMESHELF_BUILD_ROOT='{workspace}'; $dev=$env:GAMESHELF_BUILD_ROOT; $ErrorActionPreference='Stop'; "
powershell(setup + "New-Item -ItemType Directory -Force (Join-Path $dev 'src') | Out-Null", check=True)
archive = root / 'Windows/.build/source.tar.gz'
archive.parent.mkdir(exist_ok=True)
ignored = {'bin', 'obj', '.build', 'build', '__pycache__'}
with tarfile.open(archive, 'w:gz') as output:
    output.add(root / 'Windows', arcname='Windows',
               filter=lambda item: None if set(item.name.split('/')) & ignored else item)
subprocess.run(['scp', str(archive), f'{remote}/windows-src.tar.gz'], check=True)
script = setup + """
tar -xzf (Join-Path $dev 'windows-src.tar.gz') -C (Join-Path $dev 'src')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& (Join-Path $dev 'src/Windows/Tools/remote-build.ps1')
exit $LASTEXITCODE
"""
log_path = root / 'Windows/build/windows-build.log'
log_path.parent.mkdir(exist_ok=True)
with log_path.open('wb') as log:
    result = powershell(script, stdout=log, stderr=subprocess.STDOUT)
if result.returncode == 0:
    for project in ('GameShelf.Windows', 'GameShelf.Core', 'GameShelf.Core.Tests',
                    'GameShelf.Native.Tests', 'GameShelf.UiFixture', 'GameShelf.LaunchFixture'):
        subprocess.run(['scp', f'{remote}/src/Windows/{project}/packages.lock.json',
                        str(root / 'Windows' / project / 'packages.lock.json')], check=True)
print('Remote build exit:', result.returncode)
raise SystemExit(result.returncode)
