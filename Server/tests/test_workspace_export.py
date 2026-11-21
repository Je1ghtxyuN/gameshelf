from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from gameshelf_server.workspace_export import WorkspaceExportMounts, load_runtime_exports, validate_exports, validate_share_boundaries


class WorkspaceExportTests(unittest.TestCase):
    def setUp(self):
        self.accounts = {'a'*32: 20000, 'b'*32: 20001}
        self.exports = [
            dict(accountID='a'*32, smbUser='member_a', share='gameshelf-a',
                 unixUser='workspace_a'),
            dict(accountID='b'*32, smbUser='member_b', share='gameshelf-b',
                 unixUser='workspace_b'),
        ]
        self.uids = {'workspace_a': 20000, 'workspace_b': 20001}
        self.config = {
            item['share']: {'path': '/export/'+item['accountID'], 'valid users': item['smbUser'],
                            'force user': item['unixUser'], 'guest ok': 'No', 'read only': 'No',
                            'wide links': 'No', 'force group': '', 'admin users': ''}
            for item in self.exports
        }

    def validate(self):
        return validate_exports(self.exports, self.accounts, '/export',
                                lambda share, key: self.config[share][key], self.uids.get)

    def test_distinct_authenticated_accounts(self):
        self.assertEqual(self.validate(), dict(zip(self.accounts, ('gameshelf-a', 'gameshelf-b'))))

    def test_rejects_reused_smb_identity_and_incorrect_unix_owner(self):
        self.exports[1]['smbUser'] = 'MEMBER_A'
        with self.assertRaises(ValueError):
            self.validate()
        self.exports[1]['smbUser'] = 'member_b'
        self.uids['workspace_b'] = 20000
        with self.assertRaises(ValueError):
            self.validate()

    def test_share_boundaries_reject_parent_nested_and_bind_alias(self):
        allowed = {'a'*32: 'gameshelf-a'}
        root = Path('/exports')
        for path in ('/', '/exports', '/exports/other', '/alias'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'overlap'):
                validate_share_boundaries(
                    root, allowed, {'other':path},
                    lambda left, right: right == Path('/alias'))
        validate_share_boundaries(root, allowed,
                                  {'nas':'/games', 'GameShelf-A':str(root/('a'*32))},
                                  lambda left, right: False)

    def test_share_boundaries_reject_unresolved_homes_and_dynamic_paths(self):
        for shares in ({'homes':''}, {'dynamic':'/shares/%U'}, {'relative':'shares'}):
            with self.subTest(shares=shares), self.assertRaisesRegex(ValueError, 'static'):
                validate_share_boundaries(Path('/exports'), {}, shares, lambda left, right: False)

    def test_rejects_share_leaks(self):
        for key, value in [('valid users','member_a member_b'), ('guest ok','Yes'),
                           ('wide links','Yes'), ('path','/export'), ('force group','root'),
                           ('admin users','member_a')]:
            with self.subTest(key=key):
                original = self.config['gameshelf-a'][key]
                self.config['gameshelf-a'][key] = value
                with self.assertRaises(ValueError):
                    self.validate()
                self.config['gameshelf-a'][key] = original

    @unittest.skipUnless(shutil.which('testparm'), 'requires Samba testparm')
    def test_effective_samba_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            conf = Path(directory)/'smb.conf'
            sections = ['[global]\nserver role = standalone server\nsecurity = user\n']
            for share, options in self.config.items():
                sections.append(f'[{share}]\n'+''.join(f'{key} = {value}\n' for key, value in options.items()))
            conf.write_text('\n'.join(sections))

            def setting(share, key):
                return subprocess.run(['testparm','-s','--section-name='+share,
                                       '--parameter-name='+key,str(conf)],
                                      capture_output=True,text=True,check=True).stdout

            self.assertEqual(len(validate_exports(self.exports,self.accounts,'/export',setting,self.uids.get)), 2)
            conf.write_text(conf.read_text().replace('force user = workspace_b', 'force user = workspace_a'))
            with self.assertRaisesRegex(ValueError, 'force user'):
                validate_exports(self.exports,self.accounts,'/export',setting,self.uids.get)

    def test_runtime_rejects_export_under_game_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            export = root/'exports'
            export.mkdir()
            with self.assertRaisesRegex(ValueError, 'separate'):
                load_runtime_exports(export, root/'manifest.json', self.accounts, [root], None, None)

    def test_mounted_lease_remains_reserved_when_unmount_is_busy(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            account = 'a'*32
            lease_id = 'c'*32
            (root/account).mkdir()
            target = root/account/lease_id
            source = root/'merged'
            source.mkdir()
            mounted = {source}
            busy = True

            def run(args):
                if args[:2] == ['mount', '--rbind']:
                    mounted.add(Path(args[-1]))
                elif args[:2] == ['umount', '--recursive']:
                    if busy:
                        raise RuntimeError('busy')
                    mounted.remove(Path(args[-1]))

            mounts = WorkspaceExportMounts(root, {account: 'gameshelf-a'}, run, mounted.__contains__)
            with patch.object(mounts, '_account_directory', return_value=root/account):
                descriptor = mounts.publish(account, 20000, lease_id, source)
                self.assertEqual(descriptor, dict(share='gameshelf-a', relative=lease_id))
                self.assertTrue(target in mounted)
                with self.assertRaises(RuntimeError):
                    mounts.revoke(account, lease_id)
                self.assertTrue(target.exists())
                busy = False
                mounts.revoke(account, lease_id)
                mounts.revoke(account, lease_id)
                self.assertFalse(target.exists())
