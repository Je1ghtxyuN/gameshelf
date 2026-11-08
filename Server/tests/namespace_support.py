"""Synthetic reviewed source/config fixtures; never a real RenPy export or NAS."""
import hashlib
import json
import os
from pathlib import Path
import shutil
from test_service import FakeDriver

PROFILE='fixture.renpy-savedir.v1'
KEY='fixture-compatible-save-v1'
RELATIVE='.gameshelf-profile/CanonicalSaves/renpy'


def setup_sources(case):
    mac=case.source/'Reviewed.app'; win=case.source/'Reviewed-Windows'
    records=[]
    for platform,folder,prefix,entry in [('macos',mac,'Contents/Resources/autorun/','.'),('windows',win,'','Play.exe')]:
        entry_file='Contents/MacOS/Launcher' if platform=='macos' else 'Play.exe'
        files=[(entry_file,'entry',b'#!/bin/sh\n# synthetic entry only; not native acceptance\n' if platform=='macos' else b'MZsynthetic reviewed fixture entry; not executable acceptance'),
               (prefix+'renpy/bootstrap.py','bootstrap',b'# synthetic bootstrap fingerprint\n'),
               (prefix+'renpy/arguments.py','arguments',b'# synthetic --savedir handler fingerprint\n'),
               (prefix+'renpy/main.py','main',b'# synthetic primary savedir init\n'),
               (prefix+'renpy/savelocation.py','locations',b'# synthetic single primary location only\n'),
               (prefix+'renpy/persistent.py','persistent',b'# synthetic RENPY_MULTIPERSISTENT only\n'),
               (prefix+'renpy/savetoken.py','tokens',b'# synthetic RENPY_PATH_TO_SAVES only\n'),
               (prefix+'game/options.rpy','config',b'# synthetic reviewed save configuration\n')]
        if platform=='macos': files.append(('Contents/Info.plist','metadata',b'<plist>synthetic app metadata</plist>'))
        for path,role,data in files:
            target=folder/path; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
        (folder/(prefix+'renpy/__init__.py')).write_bytes(b'')
        legacy=prefix+'game/saves'; (folder/legacy).mkdir(parents=True)
        records.append(dict(id=platform,platform=platform,entryPath=entry,entryBackend='native' if platform=='macos' else 'renpy',
            savePaths=[dict(path=legacy,kind='directory')],legacySavePaths=[dict(path=legacy,kind='directory')],
            fingerprints=[dict(path=path,role=role,sha256=hashlib.sha256(data).hexdigest()) for path,role,data in files]))
    manifest=dict(schemaVersion=1,profiles=[dict(id=PROFILE,revision=1,adapter='renpy-savedir-v1',compatibilityKey=KEY,
        synthetic=True,isolationPolicy='single-primary-location-and-fixed-env-v1',review=dict(by='synthetic-test-review',basis='Fixture-only evidence; no actual runtime acceptance'),variants=records)])
    catalog=case.s.state/'reviewed-renpy-profiles.json'
    catalog.write_text(json.dumps(manifest)); catalog.chmod(0o600)
    return mac,win,manifest


class NamespaceDriver(FakeDriver):
    def upper(self,account,game):
        return self.state/'workspaces'/account/game/'upper'
    def open(self,lease,lower,user,*,save_paths=None,save_namespace=None):
        from gameshelf_server.namespace_storage import prepare_namespace
        data=None
        if save_namespace:
            data=prepare_namespace(self.state,lease,dict(user,uid=os.getuid()),save_namespace,
                lambda game:self.upper(lease['user_id'],game),chown=lambda *a:None)
        share=super().open(lease,lower,user,save_paths=save_paths)
        if save_namespace:
            view=self.active[lease['id']]
            for item in save_namespace['variant']['fingerprints']:
                target=view/item['path']; target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists(): shutil.copyfile(lower/item['path'],target)
            target=view/RELATIVE; target.mkdir(parents=True,exist_ok=True)
            if not hasattr(self,'namespace_paths'): self.namespace_paths={}
            self.namespace_paths[lease['id']]=data
        return share
    def verify_save_namespace(self,lease,namespace):
        from gameshelf_server.save_profiles import verify_variant
        verify_variant(self.active[lease['id']],namespace['variant'],export=True)
    def close(self,lease):
        super().close(lease)
