import importlib.util
import configparser
import stat
from pathlib import Path
import subprocess
import tempfile
import unittest

AVAILABLE = importlib.util.find_spec('gameshelf_server.driver') is not None

class Commands:
    def __init__(self):
        self.calls = []
        self.fail_unmount = False
    def __call__(self, args, *, input=None, check=True):
        self.calls.append((args, input))
        if args[0] == 'findmnt':
            out = 'ext4' if 'FSTYPE' in args else 'ro,relatime'
            return subprocess.CompletedProcess(args, 0, out, '')
        if args[0] == 'getent':
            return subprocess.CompletedProcess(args, 2, '', '')
        if args[0] == 'umount' and self.fail_unmount:
            raise subprocess.CalledProcessError(32, args)
        return subprocess.CompletedProcess(args, 0, '', '')

class DriverTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(AVAILABLE, 'Linux gateway driver must exist')
        from gameshelf_server.driver import LinuxDriver
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.source = self.base/'source'
        self.source.mkdir()
        (self.source/'Game.exe').write_text('MZunchanged')
        self.run = Commands()
        self.mounted = set()
        self.d = LinuxDriver(self.base/'state', 'gateway.example.invalid', 1445, runner=self.run,
                             mount_probe=lambda p: str(p) in self.mounted,
                             prepare_view=lambda *args: None, chown=lambda *args: None)
        self.lease = dict(id='a'*32, user_id='b'*32, game_id='c'*32)
        self.user = dict(id='b'*32, uid=20000)
    def test_generic_windows_view_prepares_adjacent_and_unity_mono_pe_modules(self):
        view = self.d.paths(self.lease)['merged']
        view.mkdir(parents=True)
        image = b'MZ' + bytes(58) + (64).to_bytes(4, 'little') + b'PE\0\0'
        for name, data in (('Game.exe', image), ('UnityPlayer.dll', image), ('data.dll', b'text')):
            path = view / name
            path.write_bytes(data)
            path.chmod(0o600)
        target = self.source / 'outside.dll'
        target.write_bytes(image)
        target.chmod(0o600)
        (view / 'link.dll').symlink_to(target)
        mono_runtime = view / 'MonoBleedingEdge' / 'EmbedRuntime'
        mono_runtime.mkdir(parents=True)
        mono = mono_runtime / 'mono-2.0-bdwgc.dll'
        mono.write_bytes(image)
        mono.chmod(0o600)
        (mono_runtime / 'linked.dll').symlink_to(target)
        unrelated = view / 'unrelated'
        unrelated.mkdir()
        (unrelated / 'plugin.dll').write_bytes(image)
        (unrelated / 'plugin.dll').chmod(0o600)
        self.mounted.add(str(view))

        self.d.prepare_generic_windows_entry(self.lease, 'Game.exe')

        for name in ('Game.exe', 'UnityPlayer.dll'):
            self.assertEqual(stat.S_IMODE((view / name).stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((view / 'data.dll').stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(mono.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((unrelated / 'plugin.dll').stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_single_external_endpoint_disables_multichannel_across_share_reloads(self):
        def check_config():
            config = configparser.RawConfigParser()
            config.read_string(self.d.config.read_text())
            self.assertEqual(config.get('global', 'server multi channel support', fallback='yes'), 'no')
        check_config()
        self.d.open(self.lease, self.source, self.user)
        check_config()
        self.d.close(self.lease)
        check_config()
    def test_windows_unity_refuses_unknown_save_redirect_assembly_before_export(self):
        from gameshelf_server.common import APIError

        entry = 'CYANBRAIN.exe'
        (self.source / entry).write_bytes(b'MZ')
        view = self.d.paths(self.lease)['merged']
        managed = view / 'CYANBRAIN_Data' / 'Managed'
        managed.mkdir(parents=True)
        (managed / 'Assembly-CSharp.dll').write_bytes(b'unreviewed assembly')
        (view / entry).write_bytes(b'MZ')
        self.d.mount_probe = lambda path: any(
            command[:3] == ['mount', '-t', 'overlay'] and command[-1] == str(path)
            for command, _ in self.run.calls
        )
        self.lease['windows_entry'] = entry

        with self.assertRaisesRegex(APIError, 'CYANBRAIN'):
            self.d.open(self.lease, self.source, self.user)
        self.assertFalse(self.d.shares)
        self.assertFalse(any(command[0] == 'smbpasswd' for command, _ in self.run.calls))

    def test_unity_runtime_patch_is_readable_by_its_smb_owner_under_private_umask(self):
        import os
        from unittest.mock import patch
        from gameshelf_server.unity_save_redirect import ASSEMBLY, ENTRY

        view = self.d.paths(self.lease)['merged']
        view.mkdir(parents=True)
        self.lease['windows_entry'] = ENTRY
        self.d.mount_probe = lambda path: any(
            command[0] == 'mount' and command[-1] == str(path)
            for command, _ in self.run.calls
        )
        owners = {}
        self.d.chown = lambda path, uid, gid: owners.__setitem__(path, (uid, gid))
        old = os.umask(0o077)
        try:
            with patch('gameshelf_server.unity_save_redirect.prepare', return_value=b'reviewed patch'):
                self.d.open(self.lease, self.source, self.user)
        finally:
            os.umask(old)
        runtime = self.d._runtime_patch_file(self.lease)
        self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o500)
        self.assertEqual(owners[runtime], (self.user['uid'], self.user['uid']))
        self.assertEqual(stat.S_IMODE(runtime.parent.stat().st_mode), 0o700)
        self.assertIn((['mount', '-o', 'remount,bind,ro,nosuid,nodev,noexec',
                        str(view / ASSEMBLY)], None), self.run.calls)
    def test_wine_unity_uses_same_save_redirection_as_windows(self):
        from unittest.mock import patch
        from gameshelf_server.unity_save_redirect import ASSEMBLY, ENTRY
        (self.source/ENTRY).write_bytes(b'MZ')
        view=self.d.paths(self.lease)['merged']
        view.mkdir(parents=True)
        self.d.mount_probe=lambda path:any(command[0]=='mount' and command[-1]==str(path) for command,_ in self.run.calls)
        self.assertNotIn('windows_entry',self.lease)
        with patch('gameshelf_server.unity_save_redirect.prepare',return_value=b'reviewed patch') as prepare:
            self.d.open(self.lease,self.source,self.user)
        prepare.assert_called_once_with(self.source,view)
        self.assertTrue(self.d._runtime_patch_file(self.lease).is_file())
        self.assertIn((['mount','-o','remount,bind,ro,nosuid,nodev,noexec',str(view/ASSEMBLY)],None),self.run.calls)

    def test_private_paths_and_encrypted_smb_no_secret_arguments(self):
        share = self.d.open(self.lease, self.source, self.user)
        config = self.d.config.read_text()
        self.assertIn('server min protocol = SMB3_00', config)
        self.assertIn('server smb encrypt = required', config)
        self.assertIn('follow symlinks = yes', config)
        self.assertIn('wide links = no', config)
        self.assertIn('guest ok = no', config)
        self.assertIn('valid users = '+share['username'], config)
        self.assertNotIn(share['password'], config)
        self.assertNotIn(share['password'], repr([args for args, _ in self.run.calls]))
        self.assertTrue(any(share['password'] in (data or '') for _, data in self.run.calls))
        self.assertEqual((self.source/'Game.exe').read_text(), 'MZunchanged')
        args = next(a for a, _ in self.run.calls if a[0] == 'mount' and 'overlay' in a)
        self.assertIn('metacopy=on', args[args.index('-o')+1])
        self.assertIn('redirect_dir=on',args[args.index('-o')+1])
        self.assertIn('nosuid,nodev,noexec', args[args.index('-o')+1])
        self.assertIn('/'+'b'*32+'/'+'c'*32+'/upper', args[args.index('-o')+1])
    def test_workspace_receipt_rotates_with_each_lease_without_touching_source(self):
        self.d.open(self.lease, self.source, self.user)
        profile = self.d.paths(self.lease)['merged']/'.gameshelf-profile'
        marker = profile/'workspace-receipt-v1'
        token = self.lease['workspace_receipt']
        self.assertRegex(token, r'^[a-f0-9]{64}$')
        self.assertEqual(marker.read_bytes(), (self.lease['id']+'\n'+token+'\n').encode('ascii'))
        second = dict(self.lease, id='d'*32)
        self.d.close(self.lease)
        self.d.open(second, self.source, self.user)
        self.assertNotEqual(second['workspace_receipt'], token)
        self.assertEqual(marker.read_bytes(), (second['id']+'\n'+second['workspace_receipt']+'\n').encode('ascii'))
        self.assertFalse((self.source/'.gameshelf-profile').exists())

    def test_close_revokes_credentials_share_connections_before_unmount(self):
        share = self.d.open(self.lease, self.source, self.user)
        paths = self.d.paths(self.lease)
        self.mounted.update([str(paths['merged']), str(paths['lower'])])
        self.run.calls.clear()
        self.d.close(self.lease)
        calls = [a for a, _ in self.run.calls]
        revoke = next(i for i,a in enumerate(calls) if a[0] == 'smbpasswd' and '-x' in a)
        close = next(i for i,a in enumerate(calls) if 'close-share' in a)
        unmount = next(i for i,a in enumerate(calls) if a[0] == 'umount')
        self.assertLess(revoke, close)
        self.assertLess(close, unmount)
        self.assertNotIn('['+share['name']+']', self.d.config.read_text())
        self.assertFalse(any('-l' in a or '-f' in a for a in calls if a[0] == 'umount'))
    def test_mz_sync_uses_private_view_before_publish_and_upper_after_unmount(self):
        from unittest.mock import Mock

        sync = Mock()
        self.d.mz_sync = sync
        paths = self.d.paths(self.lease)
        def prepare(lease, lower, user, view):
            self.assertEqual((lease, lower, user, view), (self.lease, self.source, self.user, paths['merged']))
            self.assertFalse(any(args[0] == 'smbpasswd' and '-a' in args for args, _ in self.run.calls))
        sync.prepare.side_effect = prepare
        self.d.open(self.lease, self.source, self.user)
        sync.prepare.assert_called_once()
        self.mounted.add(str(paths['merged']))
        self.run.fail_unmount = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.d.close(self.lease)
        sync.commit.assert_not_called()
        self.run.fail_unmount = False
        self.mounted.clear()
        self.d.close(self.lease)
        sync.commit.assert_called_once_with(self.lease, paths['upper'])

    def test_failed_unmount_propagates(self):
        self.d.open(self.lease, self.source, self.user)
        self.mounted.add(str(self.d.paths(self.lease)['merged']))
        self.run.fail_unmount = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.d.close(self.lease)
    def test_reject_unsafe_ids_and_non_ext4(self):
        with self.assertRaises(ValueError):
            self.d.paths(dict(self.lease, game_id='../escape'))
        original = self.run
        def wrongfs(args, **kw):
            if args[0] == 'findmnt' and 'FSTYPE' in args:
                return subprocess.CompletedProcess(args, 0, 'overlay', '')
            return original(args, **kw)
        self.d.run = wrongfs
        with self.assertRaises(RuntimeError):
            self.d.validate([self.source])


    def test_passdb_read_failure_must_not_count_as_revocation(self):
        self.d.open(self.lease,self.source,self.user)
        original=self.run
        def failing(args, **kw):
            if args[0] in ('smbpasswd','pdbedit'):
                return subprocess.CompletedProcess(args,255,'','cannot read database')
            return original(args,**kw)
        self.d.run=failing
        with self.assertRaises(RuntimeError):
            self.d.close(self.lease)

    def test_smb_identities_have_unique_unix_uids_and_private_force_user(self):
        self.d.open(self.lease,self.source,self.user)
        commands=[a for a,_ in self.run.calls if a[0]=='useradd']
        self.assertEqual(len(commands),2)
        self.assertNotIn('--non-unique',repr(commands))
        session=next(a for a in commands if a[-1].startswith('gs'+'a'*24))
        self.assertNotIn('--uid',session)
        self.assertIn('--system',session)
        self.assertIn('force user = gsa20000',self.d.config.read_text())
    def test_samba_uid_can_traverse_workspace_ancestors_under_private_umask(self):
        import os
        old=os.umask(0o077)
        try:
            self.d.open(self.lease,self.source,self.user)
        finally:
            os.umask(old)
        self.assertEqual((self.d.state/'workspaces').stat().st_mode & 0o777,0o711)
    def test_save_whiteouts_synced_before_initialization_manifest(self):
        self.d.open(self.lease,self.source,self.user)
        self.assertTrue(any(a[0]=='sync' and '-f' in a for a,_ in self.run.calls))
    def test_reopen_does_not_rewrite_or_rescan_private_progress(self):
        calls=[]
        self.d.prepare_view=lambda *args:calls.append(args)
        self.d.open(self.lease,self.source,self.user)
        self.d.close(self.lease)
        view=self.d.paths(self.lease)['merged']
        (view/'private.sav').write_text('keep progress')
        (view/'now-dangling-alias').symlink_to('deleted-private-file')
        second=dict(self.lease,id='d'*32)
        self.d.open(second,self.source,self.user)
        self.assertEqual(len(calls),1)
        self.assertEqual((view/'private.sav').read_text(),'keep progress')
    def test_samba_process_has_private_process_group(self):
        from unittest.mock import patch, MagicMock
        process=MagicMock(); process.poll.return_value=None
        with patch('gameshelf_server.driver.subprocess.Popen',return_value=process) as popen:
            self.d.start()
        self.assertIs(popen.call_args.kwargs.get('start_new_session'),True)
    def test_bind_mount_detection_and_physical_source_state_overlap(self):
        import gameshelf_server.driver as module
        self.assertTrue(hasattr(module,'mount_table'),'mountinfo parsing required: ismount misses same-filesystem binds')
        info="""30 20 8:1 /games /sources ro - ext4 /dev/example ro
31 20 8:1 /games/private-state /state rw - ext4 /dev/example rw
32 20 8:1 /fixture /private\\040space/lower ro - ext4 /dev/example ro
"""
        table=module.mount_table(info)
        self.assertIn('/private space/lower',{row['mount'] for row in table})
        self.assertTrue(module.physical_overlap(Path('/state'),Path('/sources'),table))
        self.assertFalse(module.physical_overlap(Path('/state'),Path('/sources/other-game'),table))
if __name__ == '__main__':
    unittest.main()
