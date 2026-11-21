"""Mount command sequencing on temporary trees; not real Linux mount acceptance."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import unittest
from unittest.mock import patch
import test_save_namespaces
from namespace_support import RELATIVE
from gameshelf_server.driver import LinuxDriver
from gameshelf_server import privacy


class NamespaceDriverTests(unittest.TestCase):
    # Fixture helpers only; this class does not inherit/reload service test cases.
    setUp=test_save_namespaces.SaveNamespaceTests.setUp
    request=test_save_namespaces.SaveNamespaceTests.request
    denied=test_save_namespaces.SaveNamespaceTests.denied
    reviewed=test_save_namespaces.SaveNamespaceTests.reviewed
    activate=test_save_namespaces.SaveNamespaceTests.activate

    def driver_case(self):
        mac,win,_=self.reviewed(); self.activate(mac,[mac,win])
        game=self.s.db.execute('SELECT * FROM games WHERE id=?',(win,)).fetchone()
        context=self.s._namespace_for_game(self.a,game,mac)
        self.commands=[]; self.mounted=set(); self.fail_bind=False; self.fail_child_unmount=False
        def runner(args,*,input=None,check=True):
            self.commands.append(args)
            if args[0]=='getent': return subprocess.CompletedProcess(args,2,'','')
            if args[0]=='mount':
                if '--bind' in args and args[-1].endswith(RELATIVE) and self.fail_bind: raise subprocess.CalledProcessError(32,args)
                if '--bind' in args or '-t' in args: self.mounted.add(args[-1])
            if args[0]=='umount':
                if args[-1].endswith(RELATIVE) and self.fail_child_unmount: raise subprocess.CalledProcessError(32,args)
                self.mounted.discard(args[-1])
            return subprocess.CompletedProcess(args,0,'','')
        self.d=LinuxDriver(self.s.state,'synthetic.invalid',runner=runner,mount_probe=lambda p:str(p) in self.mounted,
            prepare_view=lambda root,uid,source:shutil.copytree(source,root,dirs_exist_ok=True),chown=lambda *a:None)
        # The runner mocks Unix accounts; keep filesystem ownership at the real
        # test uid instead of requiring a root chown to a gateway uid.
        self.d._account=lambda username,uid:'fixture-unix-account'
        cleanup={key:context[key] for key in ('key','adapter','relativePath','serverID','userID','canonicalID','compatibilityKey')}
        lease=dict(id='f'*32,user_id=self.a['id'],game_id=win,canonical_id=mac,save_namespace=json.dumps(cleanup))
        return lease,context,self.source/'Reviewed-Windows',dict(self.a,uid=os.getuid())

    def open(self,lease,context,source,user):
        actual=privacy.mask_seed_saves
        with patch('gameshelf_server.privacy.mask_seed_saves',side_effect=lambda root,rules,uid:actual(root,rules,uid,chown=lambda *a:None)):
            return self.d.open(lease,source,user,save_paths=context['variant']['savePaths'],save_namespace=context)

    def test_bind_before_export_and_close_child_before_edition_overlay(self):
        lease,context,source,user=self.driver_case()
        share=self.open(lease,context,source,user); self.d.verify_save_namespace(lease,context)
        target=str(self.d.paths(lease)['merged']/RELATIVE)
        bind=next(i for i,c in enumerate(self.commands) if c[0]=='mount' and '--bind' in c and c[-1]==target)
        export=next(i for i,c in enumerate(self.commands) if c[0]=='smbpasswd' and '-a' in c)
        self.assertLess(bind,export)
        self.assertIn('/'+lease['game_id']+'/upper',next(c[c.index('-o')+1] for c in self.commands if c[0]=='mount' and '-t' in c))
        self.commands.clear(); self.d.close(lease)
        close=next(i for i,c in enumerate(self.commands) if 'close-share' in c)
        unmounts=[(i,c[-1]) for i,c in enumerate(self.commands) if c[0]=='umount']
        self.assertLess(close,unmounts[0][0]); self.assertEqual(unmounts[0][1],target)
        self.assertEqual([p for _,p in unmounts[1:]],[str(self.d.paths(lease)['merged']),str(self.d.paths(lease)['lower'])])

    def test_windows_images_are_executable_only_in_the_private_view(self):
        lease,context,source,user=self.driver_case()
        runtime=source/'lib/py3-windows-x86_64'
        runtime.mkdir(parents=True)
        image=b'MZ'+bytes(58)+(64).to_bytes(4,'little')+b'PE\0\0'
        for name,data in [('Game.exe',image),('python39.dll',image),('_renpy.pyd',image),('fake.dll',b'plain data')]:
            (runtime/name).write_bytes(data)
        (source/'game/installer.exe').write_bytes(image)
        expected=['Play.exe','lib/py3-windows-x86_64/Game.exe','lib/py3-windows-x86_64/python39.dll',
                  'lib/py3-windows-x86_64/_renpy.pyd']
        other=['game/installer.exe','lib/py3-windows-x86_64/fake.dll','renpy/main.py']
        for name in expected+other:
            (source/name).chmod(0o600)

        self.open(lease,context,source,user)

        view=self.d.paths(lease)['merged']
        self.assertTrue(all((view/name).stat().st_mode & stat.S_IXUSR for name in expected))
        self.assertTrue(all(not (view/name).stat().st_mode & stat.S_IXUSR for name in other))
        self.assertTrue(all(stat.S_IMODE((source/name).stat().st_mode)==0o600 for name in expected+other))

    def test_child_unmount_failure_preserves_overlay_barrier(self):
        lease,context,source,user=self.driver_case(); self.open(lease,context,source,user)
        self.fail_child_unmount=True; self.commands.clear()
        with self.assertRaises(subprocess.CalledProcessError): self.d.close(lease)
        unmounts=[c[-1] for c in self.commands if c[0]=='umount']
        self.assertEqual(unmounts,[str(self.d.paths(lease)['merged']/RELATIVE)])
        self.assertIn(str(self.d.paths(lease)['merged']),self.mounted)

    def test_bind_failure_does_not_export_or_change_source(self):
        lease,context,source,user=self.driver_case(); self.fail_bind=True
        before={str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()}
        with self.assertRaises(subprocess.CalledProcessError): self.open(lease,context,source,user)
        self.assertFalse(any(c[0]=='smbpasswd' and '-a' in c for c in self.commands))
        self.d.close(lease)
        self.assertEqual(before,{str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()})

    def test_nonempty_legacy_blocks_before_any_mount_or_export(self):
        lease,context,source,user=self.driver_case()
        upper=self.d.paths(lease)['upper']; save=upper/'game/saves/slot.save'; save.parent.mkdir(parents=True); save.write_bytes(b'preserve fixture')
        self.commands.clear()
        with self.assertRaises(Exception): self.open(lease,context,source,user)
        self.assertFalse(any(c[0] in ('mount','smbpasswd') for c in self.commands))
        self.assertEqual(save.read_bytes(),b'preserve fixture')
