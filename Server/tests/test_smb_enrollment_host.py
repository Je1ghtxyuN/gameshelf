import os
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gameshelf_server.smb_enrollment_host import ProofFiles


class ProofFilesTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name).resolve()
        self.root.chmod(0o711)
        self.challenge = 'a'*32
        self.proof = 'b'*64

    def test_publish_read_only_proof_without_replacing_existing_file(self):
        with patch('gameshelf_server.smb_enrollment_host.os.fchown') as chown:
            files = ProofFiles(self.root, owner=os.geteuid())
            name = files.publish(20001,self.challenge,self.proof)
            path = self.root/name
            self.assertEqual(path.read_text(),self.proof)
            self.assertEqual(path.stat().st_mode & 0o777,0o400)
            self.assertEqual(chown.call_args.args[1:],(20001,-1))
            with self.assertRaises(FileExistsError):
                files.publish(20002,self.challenge,'c'*64)
            files.remove(self.challenge)
            self.assertFalse(path.exists())

    def test_invalid_inputs_and_insecure_root_are_rejected(self):
        files = ProofFiles(self.root, owner=os.geteuid())
        for uid, challenge, proof in [(0,self.challenge,self.proof),
                                      (20001,'../outside',self.proof),
                                      (20001,self.challenge,'bad')]:
            with self.assertRaises(ValueError):
                files.publish(uid,challenge,proof)
        self.root.chmod(0o777)
        with self.assertRaises(ValueError):
            files.publish(20001,self.challenge,self.proof)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_purge_removes_only_expired_proof_files(self):
        with patch('gameshelf_server.smb_enrollment_host.os.fchown'):
            files = ProofFiles(self.root,owner=os.geteuid())
            old = self.root/files.publish(20001,self.challenge,self.proof)
            current = self.root/files.publish(20001,'c'*32,self.proof)
            unrelated = self.root/'notes'
            unrelated.write_text('keep')
            os.utime(old,(100,100))
            os.utime(current,(699,699))
            os.utime(unrelated,(100,100))
            files.purge_expired(now=700)
            self.assertFalse(old.exists())
            self.assertTrue(current.exists())
            self.assertTrue(unrelated.exists())


class SambaShareTests(unittest.TestCase):
    def test_share_uses_stable_name_and_exact_verified_identities(self):
        from gameshelf_server.smb_enrollment_host import workspace_share
        account = 'a'*32
        name, options = workspace_share(Path('/srv/gameshelf'),account,'member-1','gs_uid')
        self.assertEqual(name,'gs-workspace-'+account)
        self.assertEqual(options, {
            'path': '/srv/gameshelf/'+account, 'valid users': 'member-1', 'force user': 'gs_uid',
            'guest ok': 'no', 'read only': 'no', 'browseable': 'no', 'wide links': 'no',
            'admin users': '', 'force group': '', 'smb encrypt': 'required',
            'create mask': '0600', 'force create mode': '0600', 'directory mask': '0700',
            'force directory mode': '0700',
        })

    def test_rejects_config_injection_and_unconfined_paths(self):
        from gameshelf_server.smb_enrollment_host import workspace_share
        account = 'a'*32
        for root, key, user, anchor in [
            ('/','a'*32,'member','gs_uid'),
            ('exports',account,'member','gs_uid'),
            ('/srv/%U',account,'member','gs_uid'),
            ('/srv/exports\n[evil]',account,'member','gs_uid'),
            ('/srv/exports','../outside','member','gs_uid'),
            ('/srv/exports',account,'@wheel','gs_uid'),
            ('/srv/exports',account,'member','gs_uid\nadmin users = root'),
        ]:
            with self.subTest(root=root,key=key,user=user,anchor=anchor), self.assertRaises(ValueError):
                workspace_share(Path(root),key,user,anchor)

    def test_managed_include_is_idempotent_and_rolls_back_reload_failure(self):
        from gameshelf_server.smb_enrollment_host import install_workspace_share
        with tempfile.TemporaryDirectory() as directory:
            include = Path(directory).resolve()/'managed.conf'
            include.touch(mode=0o600)
            checks = []

            def validate(candidate):
                checks.append(candidate.read_text())
                self.assertEqual(include.read_text(), original)

            def install(account, user, validate, reload):
                return install_workspace_share(include,Path('/srv/gameshelf'),account,user,'gs_uid',
                                               validate,reload,owner=os.geteuid())

            original = ''
            account = 'a'*32
            name = install(account,'member',validate,lambda:None)
            self.assertEqual(name,'gs-workspace-'+account)
            self.assertIn('valid users = member',include.read_text())
            original = include.read_text()
            self.assertEqual(install(account,'member',validate,lambda:None),name)
            self.assertEqual(len(checks),2)
            with self.assertRaises(ValueError):
                install(account,'other',validate,lambda:None)

            reloads = []
            def fail_first_reload():
                reloads.append(include.read_text())
                if len(reloads) == 1:
                    raise OSError('fixture')
            with self.assertRaises(OSError):
                install('b'*32,'other',validate,fail_first_reload)
            self.assertEqual(include.read_text(),original)
            self.assertEqual(reloads[-1],original)

    def test_preflight_failure_leaves_include_untouched(self):
        from gameshelf_server.smb_enrollment_host import install_workspace_share
        with tempfile.TemporaryDirectory() as directory:
            include = Path(directory).resolve()/'managed.conf'
            include.touch(mode=0o600)
            def reject(candidate):
                raise ValueError('overlapping share')
            with self.assertRaisesRegex(ValueError,'overlapping'):
                install_workspace_share(include,Path('/srv/gameshelf'),'a'*32,'member','gs_uid',
                                        reject,lambda:self.fail('invalid configuration reloaded'),
                                        owner=os.geteuid())
            self.assertEqual(include.read_bytes(),b'')

    def test_effective_preflight_blocks_existing_share_name_and_unsafe_permissions(self):
        from gameshelf_server.smb_enrollment_host import validate_samba_candidate, workspace_share
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            config, include, candidate = (base/name for name in ('smb.conf','managed.conf','new.conf'))
            config.write_text(f'[global]\ninclude = {include}\n')
            include.touch()
            root = base/'exports'
            account = 'a'*32
            share, options = workspace_share(root,account,'member','gs_uid')
            candidate.write_text('['+share+']\n'+''.join(f'{key} = {value}\n' for key,value in options.items()))
            existing = {'nas':{'path':str(base/'games')}}
            override = {}

            def query(args):
                raw = Path(args[-1]).read_text()
                shares = dict(existing)
                if str(candidate) in raw:
                    shares[share] = {**options,**override}
                section = next((arg.split('=',1)[1] for arg in args if arg.startswith('--section-name=')),None)
                if section:
                    key = next(arg.split('=',1)[1] for arg in args if arg.startswith('--parameter-name='))
                    return shares[section][key]
                return '[global]\n'+''.join(f'[{name}]\n' for name in shares)

            validate_samba_candidate(config,include,candidate,root,query)
            override['guest ok'] = 'yes'
            with self.assertRaisesRegex(ValueError,'guest ok'):
                validate_samba_candidate(config,include,candidate,root,query)
            override.clear()
            existing[share.upper()] = {'path':str(base/'old')}
            with self.assertRaisesRegex(ValueError,'existing Samba share'):
                validate_samba_candidate(config,include,candidate,root,query)

    @unittest.skipUnless(shutil.which('testparm'),'requires Samba testparm')
    def test_generated_share_matches_real_samba_effective_values(self):
        from gameshelf_server.smb_enrollment_host import validate_samba_candidate, workspace_share
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            config = base/'smb.conf'
            include = base/'managed.conf'
            candidate = base/'candidate.conf'
            root = base/'exports'
            root.mkdir()
            config.write_text(f'[global]\nserver role = standalone server\ninclude = {include}\n')
            include.touch()
            share, options = workspace_share(root,'a'*32,'member','gs_uid')
            candidate.write_text('['+share+']\n'+''.join(f'{key} = {value}\n' for key,value in options.items()))

            def query(args):
                return subprocess.run(args,capture_output=True,text=True,check=True,timeout=5).stdout
            validate_samba_candidate(config,include,candidate,root,query)


class SambaIdentityTests(unittest.TestCase):
    def test_canonical_passdb_identity_requires_eligible_unix_uid(self):
        from gameshelf_server.smb_enrollment_host import resolve_samba_identity
        record = ('Unix username: member\nUser SID: S-1-5-21-1-2-3-1001\n'
                  'Account Flags: [U          ]\n')
        def query(args):
            if args[0] == 'pdbedit':
                self.assertNotIn('-w',args)
                return record
            if args[0] == 'id':
                return '1001 2600'
            self.assertEqual(args,['getent','passwd','member'])
            return 'member:x:1001:1001::/nonexistent:/usr/sbin/nologin'
        self.assertEqual(resolve_samba_identity('MEMBER',2600,'/etc/samba/smb.conf',query),
                         ('member',1001,'S-1-5-21-1-2-3-1001'))
        with self.assertRaisesRegex(ValueError,'eligible'):
            resolve_samba_identity('member',2700,'/etc/samba/smb.conf',query)
        record = record.replace('[U ', '[DU ')
        with self.assertRaisesRegex(ValueError,'eligible'):
            resolve_samba_identity('member',2600,'/etc/samba/smb.conf',query)

    def test_duplicate_or_wrong_samba_identity_is_rejected(self):
        from gameshelf_server.smb_enrollment_host import resolve_samba_identity
        record = ('Unix username: somebody-else\nUser SID: S-1-5-21-1-2-3-1001\n'
                  'Account Flags: [U          ]\n')
        for response in (record,record.replace('somebody-else','member')+record):
            with self.assertRaises(ValueError):
                resolve_samba_identity('member',2600,'/etc/samba/smb.conf',lambda _:response)

    def test_adapter_rechecks_identity_before_delivery_and_provision(self):
        from gameshelf_server.smb_enrollment_host import SambaEnrollmentHost
        stable = ('member',1001,'S-1-5-21-1-2-3-1001')
        account = 'a'*32
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            host = SambaEnrollmentHost(
                config=base/'smb.conf', include=base/'managed.conf', export_root=base/'exports',
                proof_root=base/'proofs', eligible_gid=2600,
                workspace_identity=lambda _: (20001,'gs_uid'), query=lambda _: '')
            with patch('gameshelf_server.smb_enrollment_host.resolve_samba_identity',
                       return_value=stable) as resolve, patch.object(host.proofs,'publish') as publish, \
                 patch('gameshelf_server.smb_enrollment_host.prepare_workspace_directory') as prepare, \
                 patch('gameshelf_server.smb_enrollment_host.install_workspace_share',
                       return_value='gs-workspace-'+account) as install:
                principal = host.resolve('member')
                host.deliver(principal,'b'*32,'c'*64)
                publish.assert_called_once_with(1001,'b'*32,'c'*64)
                self.assertEqual(host.provision(account,principal),'gs-workspace-'+account)
                prepare.assert_called_once_with(base/'exports',account,20001)
                self.assertEqual(install.call_args.args[:5],
                                 (base/'managed.conf',base/'exports',account,'member','gs_uid'))
                resolve.return_value = ('member',1001,'S-1-5-21-1-2-3-1002')
                with self.assertRaisesRegex(ValueError,'identity changed'):
                    host.provision(account,principal)
                self.assertEqual(install.call_count,1)

    def test_workspace_unix_identity_can_be_created_once_and_reused(self):
        from gameshelf_server.smb_enrollment_host import ensure_workspace_user
        from subprocess import CompletedProcess
        users, groups, commands = {}, {}, []
        uid = 20001
        name = 'gsa20001'

        def run(args, *, check=True):
            commands.append(args)
            if args[0] == 'getent':
                records = users if args[1] == 'passwd' else groups
                row = records.get(args[2])
                return CompletedProcess(args,0 if row else 2,row or '', '')
            if args[0] == 'groupadd':
                groups[name] = groups[str(uid)] = f'{name}:x:{uid}:'
            elif args[0] == 'useradd':
                users[name] = users[str(uid)] = f'{name}:x:{uid}:{uid}::/nonexistent:/usr/sbin/nologin'
            return CompletedProcess(args,0,'','')

        self.assertEqual(ensure_workspace_user(uid,run),name)
        self.assertEqual(ensure_workspace_user(uid,run),name)
        self.assertEqual([cmd[0] for cmd in commands].count('useradd'),1)
        self.assertEqual([cmd[0] for cmd in commands].count('groupadd'),1)
        users[str(uid)] = 'unrelated:x:20001:20001::/home/unrelated:/bin/sh'
        with self.assertRaisesRegex(ValueError,'conflict'):
            ensure_workspace_user(uid,run)
        self.assertEqual([cmd[0] for cmd in commands].count('useradd'),1)
