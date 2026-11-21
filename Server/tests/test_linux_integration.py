"""Explicit disposable-container integration; never runs in the default suite.

Requires an empty dedicated ext4 state mount and no host-network or SMB mappings.
See README. No production source or save is used, even when enabled.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest

ENABLED = os.environ.get('GAMESHELF_LINUX_TESTS') == '1'

@unittest.skipUnless(ENABLED and sys.platform=='linux','opt-in isolated Linux mount/SMB test')
class LinuxIntegrationTests(unittest.TestCase):
    def test_real_overlay_smb_api_seed_privacy_copyup_and_revocation_barrier(self):
        from gameshelf_server.driver import LinuxDriver, run, is_mountpoint
        from gameshelf_server.http_api import make_server
        from gameshelf_server.service import Service
        self.assertEqual(os.environ.get('GAMESHELF_CONTAINER'),'1','Use only the supplied disposable container')
        self.assertEqual(os.geteuid(),0)
        scratch=Path(os.environ['GAMESHELF_INTEGRATION_DIR']).resolve()
        self.assertTrue(scratch.stat().st_mode & 0o001,'Dedicated test root and ancestors must allow uid traversal (0711)')
        self.assertEqual(run(['findmnt','-n','-o','FSTYPE','--target',str(scratch)]).stdout.strip(),'ext4')
        base=Path(tempfile.mkdtemp(prefix='gameshelf-fixture-',dir=scratch))
        base.chmod(0o711)
        rw=base/'fixture'; ro=base/'source'; state=base/'private-state'
        exports = base/'workspace-exports'
        rw.mkdir(); ro.mkdir()
        novel=rw/'Novel'; novel.mkdir()
        (novel/'game'/'saves').mkdir(parents=True)
        (novel/'save').mkdir()
        (novel/'save'/'file1.rmmzsave').write_bytes(b'ORIGINAL MZ PROGRESS')
        for p,data in [('Game.exe',b'MZfixture program'),('Actors.rvdata2',b'asset'),
                       ('Save01.rvdata2',b'OWNER PROGRESS'),('game/saves/persistent',b'OWNER RENPY')]:
            (novel/p).write_bytes(data)
        (novel/'asset-link').symlink_to('Actors.rvdata2')
        before={p.relative_to(rw).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in rw.rglob('*') if p.is_file()}
        run(['mount','--bind',str(rw),str(ro)])
        run(['mount','-o','remount,bind,ro,nosuid,nodev,noexec',str(ro)])
        driver=None; service=None; server=None; server_thread=None
        direct_leases=[]
        mount_peer=None
        try:
            driver=LinuxDriver(state,'127.0.0.1',445)
            driver.validate([ro]); driver.start()
            service=Service(state,driver,[ro])
            owner_password=secrets.token_urlsafe(24)
            service.create_user('owner',owner_password,True)
            for name in ('alice','bob'):
                service.create_user(name,name+' fixture password')
            server=make_server(service,'127.0.0.1',0)
            server_thread=threading.Thread(target=server.serve_forever)
            server_thread.start()
            def api(method,path,body=None,token=None):
                connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=45)
                headers={'Content-Type':'application/json'}
                if token: headers['Authorization']='Bearer '+token
                connection.request(method,path,json.dumps(body) if body is not None else None,headers)
                response=connection.getresponse(); payload=json.loads(response.read()); status=response.status
                connection.close(); return status,payload
            owner=api('POST','/api/login',dict(username='owner',password=owner_password))[1]['token']
            root=api('POST','/api/admin/roots',dict(path=str(ro),title='Fixture'),owner)[1]
            self.assertEqual(api('POST',f'/api/admin/roots/{root["id"]}/scan',{},owner)[0],200)
            c=api('GET','/api/admin/candidates',token=owner)[1]['candidates'][0]
            game=api('POST',f'/api/admin/candidates/{c["id"]}/publish',{},owner)[1]
            accounts={u['username']:u for u in api('GET','/api/admin/users',token=owner)[1]['users']}
            tokens={}; leases={}
            for name in ('alice','bob'):
                tokens[name]=api('POST','/api/login',dict(username=name,password=name+' fixture password'))[1]['token']
                self.assertEqual(api('GET','/api/catalog',token=tokens[name])[1]['games'],[])
                self.assertEqual(api('POST','/api/admin/grants',dict(userID=accounts[name]['id'],gameID=game['id'],allowed=True),owner)[0],200)
                status,leases[name]=api('POST',f'/api/games/{game["id"]}/session',dict(deviceID=name),tokens[name])
                self.assertEqual(status,200,'Workspace mount/export must succeed')
                row=dict(service.db.execute('SELECT * FROM leases WHERE id=?',(leases[name]['leaseID'],)).fetchone())
                options=run(['findmnt','-n','-o','OPTIONS','--target',str(driver.paths(row)['merged'])]).stdout
                self.assertIn('metacopy=on',options)
                self.assertNotIn('redirect_dir=off',options)
            def smb(lease,commands,ok=True,credentials=None):
                share=lease['share']; auth=base/'smb-auth'
                selected=credentials or share
                auth.write_text('username = '+selected['username']+'\npassword = '+selected['password']+'\n')
                auth.chmod(0o600)
                try:
                    result=subprocess.run(['smbclient','//127.0.0.1/'+share['name'],'-A',str(auth),
                        '--client-protection=encrypt','-m','SMB3','-c',commands],capture_output=True,text=True,timeout=30)
                finally:
                    auth.unlink(missing_ok=True)
                self.assertEqual(result.returncode==0,ok,result.stdout + result.stderr)
            upload=base/'upload'; downloaded=base/'download'
            game_id=game['id']
            owner_id=accounts['owner']['id']
            self.assertEqual(api('POST','/api/admin/grants',dict(userID=owner_id,gameID=game_id,allowed=True),owner)[0],200)
            session=f'/api/library/games/{game_id}/session'
            request=dict(variantID=game_id,deviceID='native-owner',windowsEntry='Game.exe')
            self.assertEqual(api('POST',session,request,tokens['alice'])[0],403)
            original_mode=(novel/'Game.exe').stat().st_mode
            status,owner_lease=api('POST',session,request,owner)
            self.assertEqual(status,200)
            self.assertEqual(owner_lease['windowsLaunch'],dict(entryPath='Game.exe',
                isolation='owner-private-overlay',saveProfileVerified=False))
            owner_row=dict(service.db.execute('SELECT * FROM leases WHERE id=?',(owner_lease['leaseID'],)).fetchone())
            exported=driver.paths(owner_row)['merged']/'Game.exe'
            self.assertTrue(exported.stat().st_mode & stat.S_IXUSR)
            self.assertEqual((novel/'Game.exe').stat().st_mode,original_mode)
            smb(owner_lease,'get Game.exe '+str(downloaded))
            self.assertEqual(downloaded.read_bytes(),(novel/'Game.exe').read_bytes())
            self.assertEqual(api('DELETE',f'/api/sessions/{owner_lease["leaseID"]}',token=owner)[0],200)
            for name in ('alice','bob'):
                smb(leases[name],'get Save01.rvdata2 '+str(downloaded),ok=False)
                smb(leases[name],'get game/saves/persistent '+str(downloaded),ok=False)
                smb(leases[name],'get asset-link '+str(downloaded))
                self.assertEqual(downloaded.read_bytes(),b'asset')
                upload.write_text(name)
                smb(leases[name],'put '+str(upload)+' Save01.rvdata2; put '+str(upload)+' .gameshelf-profile/account.txt')
            smb(leases['bob'],'ls',credentials=leases['alice']['share'],ok=False)
            upload.write_text('private new')
            smb(leases['alice'],'put '+str(upload)+' new.save; rename new.save renamed.save; del Actors.rvdata2')
            smb(leases['bob'],'get Actors.rvdata2 '+str(downloaded))
            self.assertEqual(downloaded.read_bytes(),b'asset')
            # A live OS descriptor forces EBUSY after SMB credential/share revocation.
            a=leases['alice']; row=dict(service.db.execute('SELECT * FROM leases WHERE id=?',(a['leaseID'],)).fetchone())
            held=open(driver.paths(row)['merged']/'.gameshelf-profile'/'account.txt','rb')
            try:
                self.assertEqual(api('DELETE',f'/api/sessions/{a["leaseID"]}',token=tokens['alice'])[0],503)
                self.assertEqual(api('POST',f'/api/games/{game["id"]}/session',dict(deviceID='new'),tokens['alice'])[0],503)
            finally:
                held.close()
            status,new=api('POST',f'/api/games/{game["id"]}/session',dict(deviceID='new'),tokens['alice'])
            self.assertEqual(status,200)
            smb(a,'ls',ok=False)
            smb(new,'get Save01.rvdata2 '+str(downloaded))
            self.assertEqual(downloaded.read_text(),'alice')
            smb(new,'get .gameshelf-profile/account.txt '+str(downloaded))
            self.assertEqual(downloaded.read_text(),'alice')
            smb(leases['bob'],'get Save01.rvdata2 '+str(downloaded))
            self.assertEqual(downloaded.read_text(),'bob')
            after={p.relative_to(rw).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in rw.rglob('*') if p.is_file()}
            self.assertEqual(before,after)
            self.assertEqual(api('POST','/api/admin/grants',dict(userID=accounts['bob']['id'],gameID=game['id'],allowed=False),owner)[0],200)
            self.assertEqual(api('DELETE',f'/api/sessions/{new["leaseID"]}',token=tokens['alice'])[0],200)
            smb(leases['bob'],'ls',ok=False)
            # Use the same edition through the two client session APIs.
            # SMB exercises the real overlay; these byte fixtures do not run Wine or Windows.
            service.direct_nas = True
            self.assertEqual(api('POST','/api/admin/library/add',
                                 dict(candidateIDs=[c['id']],userID=owner_id),owner)[0],200)
            family = api('GET','/api/library',token=tokens['bob'])[1]['games'][0]['variants'][0]
            self.assertTrue(family['requiresAccountWorkspace'])
            self.assertTrue(family['familyWindowsOverlay'])
            self.assertNotIn('nasSource',family)

            status, windows = api('POST',session,dict(variantID=game_id,deviceID='windows',
                                  windowsEntry='Game.exe'),tokens['alice'])
            self.assertEqual(status,200)
            self.assertEqual(windows['windowsLaunch']['isolation'],'account-private-overlay')
            receipt_path = '.gameshelf-profile/workspace-receipt-v1'
            smb(windows,'get '+receipt_path+' '+str(downloaded))
            self.assertEqual(downloaded.read_text(),windows['leaseID']+'\n'+windows['workspaceReceipt']+'\n')
            slot = 'family-slot.sav'
            upload.write_bytes(b'Windows progress')
            smb(windows,'put '+str(upload)+' '+slot)
            wine_request = dict(variantID=game_id,deviceID='mac-wine',adapter='renpy-savedir-v1')
            self.assertEqual(api('POST',session,wine_request,tokens['alice'])[0],409)
            self.assertEqual(api('DELETE',f'/api/sessions/{windows["leaseID"]}',token=tokens['alice'])[0],200)
            status, wine = api('POST',session,wine_request,tokens['alice'])
            self.assertEqual(status,200)
            smb(wine,'get '+receipt_path+' '+str(downloaded))
            self.assertNotEqual(wine['workspaceReceipt'],windows['workspaceReceipt'])
            self.assertEqual(downloaded.read_text(),wine['leaseID']+'\n'+wine['workspaceReceipt']+'\n')
            smb(wine,'get '+slot+' '+str(downloaded))
            self.assertEqual(downloaded.read_bytes(),b'Windows progress')
            upload.write_bytes(b'Wine progress')
            smb(wine,'put '+str(upload)+' '+slot)

            status, guest = api('POST',session,dict(variantID=game_id,deviceID='guest-windows',
                                windowsEntry='Game.exe'),tokens['bob'])
            self.assertEqual(status,200)
            smb(guest,'get '+slot+' '+str(downloaded),ok=False)
            smb(wine,'ls',credentials=guest['share'],ok=False)
            self.assertEqual(api('DELETE',f'/api/sessions/{guest["leaseID"]}',token=tokens['bob'])[0],200)
            self.assertEqual(api('DELETE',f'/api/sessions/{wine["leaseID"]}',token=tokens['alice'])[0],200)
            status, resumed = api('POST',session,dict(variantID=game_id,deviceID='windows',
                                  windowsEntry='Game.exe'),tokens['alice'])
            self.assertEqual(status,200)
            smb(resumed,'get '+slot+' '+str(downloaded))
            self.assertEqual(downloaded.read_bytes(),b'Wine progress')
            self.assertEqual(api('DELETE',f'/api/sessions/{resumed["leaseID"]}',token=tokens['alice'])[0],200)
            self.assertFalse((novel/slot).exists())
            self.assertEqual(before,{p.relative_to(rw).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in rw.rglob('*') if p.is_file()})
            from gameshelf_server.workspace_export import load_runtime_exports
            exports.mkdir(mode=0o711)
            run(['mount','--bind',str(exports),str(exports)])
            run(['mount','--make-shared',str(exports)])
            peer_script = (
                "from pathlib import Path; import sys; print('ready', flush=True)\n"
                "for line in sys.stdin:\n"
                "    print(int(Path(line.rstrip()).is_file()), flush=True)\n"
            )
            mount_peer = subprocess.Popen(
                ['unshare','--mount','--propagation','unchanged',sys.executable,'-c',peer_script],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            self.assertEqual(mount_peer.stdout.readline().strip(),'ready')

            def peer_has_file(path):
                mount_peer.stdin.write(str(path)+'\n')
                mount_peer.stdin.flush()
                return mount_peer.stdout.readline().strip() == '1'

            alice = accounts['alice']['id']
            alice_uid = service.db.execute('SELECT uid FROM users WHERE id=?',(alice,)).fetchone()[0]
            account_export = exports/alice
            account_export.mkdir()
            os.chown(account_export, 0, alice_uid)
            account_export.chmod(0o710)
            bob = accounts['bob']['id']
            bob_uid = service.db.execute('SELECT uid FROM users WHERE id=?',(bob,)).fetchone()[0]
            alice_login = dict(name='fixture-alice', username='nas_alice', password=secrets.token_urlsafe(24))
            bob_login = dict(name='fixture-bob', username='nas_bob', password=secrets.token_urlsafe(24))
            for identity, directory, account_uid in ((alice_login, account_export, alice_uid),
                                                       (bob_login, base/'bob-auth-fixture', bob_uid)):
                directory.mkdir(exist_ok=True)
                os.chown(directory, 0, account_uid)
                directory.chmod(0o710)
                anchor = driver._account(identity['username'],account_uid)
                run(['smbpasswd','-s','-c',str(driver.config),'-a',identity['username']],
                    input=(identity['password']+'\n')*2)
                driver.shares[identity['name']] = (directory,identity['username'],anchor)
            driver._write_config()
            proof_root = base/'enrollment-proofs'
            proof_root.mkdir(mode=0o711)
            with driver.config.open('a') as config:
                config.write(f'\n[fixture-enrollment]\npath = {proof_root}\n'
                             'valid users = nas_alice nas_bob\nread only = yes\n'
                             'guest ok = no\nwide links = no\n')
            driver._control('reload-config')
            from gameshelf_server.smb_enrollment_host import ProofFiles
            publisher = ProofFiles(proof_root)
            challenge = secrets.token_hex(16)
            proof = secrets.token_hex(32)
            smb_uid = int(run(['getent','passwd',alice_login['username']]).stdout.split(':')[2])
            filename = publisher.publish(smb_uid, challenge, proof)
            proof_share = dict(share={**alice_login, 'name':'fixture-enrollment'})
            smb(proof_share,'get '+filename+' '+str(downloaded))
            self.assertEqual(downloaded.read_text(), proof)
            smb(proof_share,'get '+filename+' '+str(downloaded), credentials=bob_login, ok=False)
            publisher.remove(challenge)
            smb(proof_share,'get '+filename+' '+str(downloaded), ok=False)
            alice_mapping = dict(accountID=alice, smbUser=alice_login['username'],
                                 share=alice_login['name'], unixUser='gsa'+str(alice_uid))
            mapping = state/'workspace-mapping.json'
            mapping.write_text(json.dumps([alice_mapping]))
            preflight = [sys.executable,'-m','gameshelf_server.workspace_export',
                         '--database',str(state/'gateway.sqlite3'),'--mapping',str(mapping),
                         '--export-root',str(exports),'--smb-conf',str(driver.config),'--emit-manifest']
            bob_share = driver.shares[bob_login['name']]
            driver.shares[bob_login['name']] = (exports,*bob_share[1:])
            driver._write_config()
            unsafe = run(preflight, check=False)
            self.assertNotEqual(unsafe.returncode,0)
            self.assertIn('Another NAS share overlaps',unsafe.stderr)
            driver.shares[bob_login['name']] = bob_share
            driver._write_config()
            manifest = state/'workspace-manifest.json'
            result = run(preflight, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest.write_text(result.stdout)
            manifest.chmod(0o600)
            driver.workspace_exports = load_runtime_exports(
                exports, manifest, {alice:alice_uid}, [state, ro], run, is_mountpoint)
            self.assertEqual(api('GET','/api/library',token=tokens['alice'])[1]['mountedWorkspaceShare'],
                             'fixture-alice')
            self.assertNotIn('mountedWorkspaceShare',api('GET','/api/library',token=tokens['bob'])[1])
            # Once system SMB reuse is configured, an older client or an
            # unconfigured member cannot request gateway-generated credentials.
            for name in ('alice','bob'):
                status, response = api('POST',session,wine_request,tokens[name])
                self.assertEqual(status,409)
                self.assertNotIn('share',response)
            mounted_request = dict(wine_request,mountedWorkspace=True)
            self.assertEqual(api('POST',session,mounted_request,tokens['bob'])[0],409)
            status, mounted = api('POST',session,mounted_request,tokens['alice'])
            self.assertEqual(status,200)
            self.assertNotIn('share',mounted)
            descriptor = mounted['mountedWorkspace']
            self.assertEqual(descriptor,dict(share='fixture-alice',relative=mounted['leaseID']))
            target = account_export/descriptor['relative']
            self.assertTrue(is_mountpoint(target))
            self.assertTrue(peer_has_file(target/slot))
            self.assertEqual((target/slot).read_bytes(),b'Wine progress')
            slot_on_share = descriptor['relative']+'/'+slot
            smb(dict(share=alice_login), 'get '+slot_on_share+' '+str(downloaded))
            self.assertEqual(downloaded.read_bytes(), b'Wine progress')
            smb(dict(share=alice_login), 'get '+slot_on_share+' '+str(downloaded), credentials=bob_login, ok=False)
            with (target/slot).open('rb'):
                self.assertEqual(api('DELETE',f'/api/sessions/{mounted["leaseID"]}',token=tokens['alice'])[0],503)
                self.assertEqual(api('POST',session,mounted_request,tokens['alice'])[0],503)
            self.assertEqual(api('DELETE',f'/api/sessions/{mounted["leaseID"]}',token=tokens['alice'])[0],200)
            self.assertFalse(target.exists())
            self.assertFalse(peer_has_file(target/slot))
            smb(dict(share=alice_login), 'get '+slot_on_share+' '+str(downloaded), ok=False)
            status, next_windows = api('POST', session, dict(variantID=game_id, deviceID='windows',
                                       windowsEntry='Game.exe', mountedWorkspace=True), tokens['alice'])
            self.assertEqual(status,200)
            target = account_export/next_windows['leaseID']
            self.assertEqual((target/slot).read_bytes(),b'Wine progress')

            def as_workspace_user(uid, script):
                return run(['setpriv','--reuid',str(uid),'--regid',str(uid),'--clear-groups',
                            sys.executable,'-c',script,str(target/slot)], check=False)

            read_slot = 'import sys; from pathlib import Path; print(Path(sys.argv[1]).read_text())'
            self.assertNotEqual(as_workspace_user(bob_uid, read_slot).returncode,0)
            self.assertEqual(as_workspace_user(alice_uid, read_slot).stdout.strip(),'Wine progress')
            upload.write_bytes(b'Reused SMB workspace progress')
            smb(dict(share=alice_login),'put '+str(upload)+' '+next_windows['leaseID']+'/'+slot)
            service.recover()
            self.assertFalse(target.exists())
            status, recovered = api('POST', session, mounted_request, tokens['alice'])
            self.assertEqual(status,200)
            target = account_export/recovered['leaseID']
            smb(dict(share=alice_login),'get '+recovered['leaseID']+'/'+slot+' '+str(downloaded))
            self.assertEqual(downloaded.read_bytes(),b'Reused SMB workspace progress')
            self.assertEqual(api('DELETE',f'/api/sessions/{recovered["leaseID"]}',token=tokens['alice'])[0],200)
            del driver.shares[bob_login['name']]
            mapping.chmod(0o600)
            driver._write_config()
            driver._control('reload-config')
            from gameshelf_server.smb_enrollment import SMBEnrollment, SMBEnrollmentAPI
            from gameshelf_server.smb_enrollment_channel import SMBEnrollmentHostClient
            member = dict(name='fixture-enrollment', username='nas_member', password=secrets.token_urlsafe(24))
            run(['groupadd', 'gsfixture'])
            run(['useradd', '-M', '-g', 'gsfixture', member['username']])
            run(['smbpasswd','-s','-c',str(driver.config),'-a',member['username']],
                input=(member['password']+'\n')*2)
            include_dir = state/'enrollment-config'
            include_dir.mkdir(mode=0o700)
            include = include_dir/'managed.conf'
            include.touch(mode=0o600)
            original_write_config = driver._write_config

            def write_fixture_config():
                # The disposable fixture runs both Samba roles in one daemon.
                original_write_config()
                with driver.config.open('a') as config:
                    config.write(f'\ninclude = {include}\n[fixture-enrollment]\npath = {proof_root}\n'
                                 'valid users = @gsfixture\nread only = yes\n'
                                 'guest ok = no\nwide links = no\nfollow symlinks = no\nsmb encrypt = required\n')

            driver._write_config = write_fixture_config
            driver._write_config()
            driver._control('reload-config')
            socket_dir = state/'enrollment-socket'
            socket_dir.mkdir(mode=0o700)
            socket_path = socket_dir/'host.sock'
            with tempfile.TemporaryFile() as host_log, subprocess.Popen(
                [sys.executable, '-m', 'gameshelf_server.smb_enrollment_host',
                 '--socket',str(socket_path),'--database',str(state/'gateway.sqlite3'),
                 '--config',str(driver.config),'--include',str(include),'--export-root',str(exports),
                 '--proof-root',str(proof_root),'--eligible-group','gsfixture',
                 '--legacy-mapping',str(mapping),
                 '--verification-share',member['name']], stderr=host_log,
            ) as host:
                try:
                    import time
                    deadline = time.monotonic()+10
                    while not socket_path.exists() and host.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.05)
                    host_log.seek(0)
                    self.assertTrue(socket_path.exists(),host_log.read().decode())
                    enrollment = SMBEnrollment(service.db)
                    service.smb_enrollment_api = SMBEnrollmentAPI(
                        enrollment, SMBEnrollmentHostClient(socket_path), member['name'],
                        register_share=lambda account, share: driver.workspace_exports.register(account,bob_uid,share),
                        existing_share=driver.workspace_exports.shares.get)
                    endpoint = '/api/account/smb-enrollment'
                    status, issued = api('POST',endpoint+'/challenge',dict(username=member['username']),tokens['bob'])
                    self.assertEqual(status,200,issued)
                    smb(dict(share=member),'get '+issued['filename']+' '+str(downloaded))
                    status, verified = api('POST',endpoint+'/proof',dict(
                        challengeID=issued['challengeID'],proof=downloaded.read_text()),tokens['bob'])
                    self.assertEqual((status,verified),(200,dict(state='verified')))
                    status, ready = api('POST',endpoint+'/workspace',{},tokens['bob'])
                    host_log.seek(0)
                    self.assertEqual(status,200,(ready,host_log.read().decode()))
                    self.assertEqual(ready,dict(state='ready',share='gs-workspace-'+bob))
                    workspace = dict(share={**member,'name':ready['share']})
                    status, first = api('POST',session,mounted_request,tokens['bob'])
                    self.assertEqual(status,200,first)
                    first_path = first['mountedWorkspace']['relative']+'/'+slot
                    upload.write_bytes(b'Self-enrolled member progress')
                    smb(workspace,'put '+str(upload)+' '+first_path)
                    smb(workspace,'get '+first_path+' '+str(downloaded),credentials=alice_login,ok=False)
                    self.assertEqual(api('DELETE',f'/api/sessions/{first["leaseID"]}',token=tokens['bob'])[0],200)
                    status, second = api('POST',session,dict(variantID=game_id,deviceID='member-windows',
                                         windowsEntry='Game.exe',mountedWorkspace=True),tokens['bob'])
                    self.assertEqual(status,200,second)
                    second_path = second['mountedWorkspace']['relative']+'/'+slot
                    smb(workspace,'get '+second_path+' '+str(downloaded))
                    self.assertEqual(downloaded.read_bytes(),b'Self-enrolled member progress')
                    self.assertEqual(api('DELETE',f'/api/sessions/{second["leaseID"]}',token=tokens['bob'])[0],200)
                    status, legacy = api('POST',session,mounted_request,tokens['alice'])
                    self.assertEqual(status,200,legacy)
                    legacy_path = legacy['mountedWorkspace']['relative']+'/'+slot
                    smb(dict(share=alice_login),'get '+legacy_path+' '+str(downloaded))
                    self.assertEqual(downloaded.read_bytes(),b'Reused SMB workspace progress')
                    self.assertEqual(api('DELETE',f'/api/sessions/{legacy["leaseID"]}',token=tokens['alice'])[0],200)
                finally:
                    service.smb_enrollment_api = None
                    host.terminate()
                    driver._write_config = original_write_config
            driver.workspace_exports = None
            del driver.shares[alice_login['name']]
            driver._write_config()
            driver._control('reload-config')
            run(['umount', str(exports)])
            service.direct_nas = False

            from gameshelf_server.mz_lease_storage import MZLeaseStorage
            from gameshelf_server.mz_save_format import read_webkit_progress, webkit_progress_snapshot
            import zlib

            def encode(value):
                return zlib.compress(json.dumps(value).encode()).decode('latin1').encode()

            def native_files(level):
                return {
                    'file1': encode(dict(system={}, party={'level': level}, map={}, player={})),
                    'global': encode([None, {'timestamp': level}]),
                }

            windows_id, web_id, canonical_id = (secrets.token_hex(16) for _ in range(3))
            sides = {windows_id: 'windows', web_id: 'web'}
            game_id = 123456
            owner_id = accounts['alice']['id']

            def reviewed_pair(lease, _):
                if lease['user_id'] == owner_id:
                    return sides[lease['game_id']], service.server_id, game_id
                return None

            driver.mz_sync = MZLeaseStorage(state, reviewed_pair, chown=driver.chown)

            def open_mz(name, variant):
                account = dict(service.db.execute('SELECT id,uid FROM users WHERE username=?', (name,)).fetchone())
                row = dict(id=secrets.token_hex(16), user_id=account['id'], game_id=variant, canonical_id=canonical_id)
                direct_leases.append(row)
                share = driver.open(row, ro/'Novel', account, save_paths=[dict(path='save', kind='directory')])
                return row, dict(share=share)

            def put(lease, path, data):
                upload.write_bytes(data)
                smb(lease, 'put '+str(upload)+' '+path)

            def get(lease, path):
                smb(lease, 'get '+path+' '+str(downloaded))
                return downloaded.read_bytes()

            windows_row, windows = open_mz('alice', windows_id)
            smb(windows,'get save/file1.rmmzsave '+str(downloaded),ok=False)
            first = native_files(17)
            for name, data in first.items():
                put(windows, 'save/'+name+'.rmmzsave', data)
            driver.close(windows_row)
            direct_leases.remove(windows_row)

            web_row, web = open_mz('alice', web_id)
            profile = '.gameshelf-profile/WebKitMZ/'
            identity = json.loads(get(web, profile+'identity.json'))['identity']
            self.assertEqual(read_webkit_progress(get(web, profile+'store.json'), identity, game_id), first)
            second = native_files(18)
            put(web, profile+'store.json', webkit_progress_snapshot(identity, game_id, second))
            driver.close(web_row)
            direct_leases.remove(web_row)

            windows_row, windows = open_mz('alice', windows_id)
            for name, data in second.items():
                self.assertEqual(get(windows, 'save/'+name+'.rmmzsave'), data)
            driver.close(windows_row)
            direct_leases.remove(windows_row)
            bob_row, bob = open_mz('bob', windows_id)
            smb(bob,'get save/file1.rmmzsave '+str(downloaded),ok=False)
            driver.close(bob_row)
            direct_leases.remove(bob_row)
            self.assertEqual((novel/'save/file1.rmmzsave').read_bytes(), b'ORIGINAL MZ PROGRESS')
            driver.stop()
            self.assertIsNotNone(driver.process.poll())
            self.assertEqual(api('GET','/api/catalog',token=tokens['alice'])[0],200,'Stopping smbd must not signal the API/test process group')
        finally:
            original_failure=sys.exc_info()[0] is not None
            errors=[]
            def attempt(label,operation):
                try:
                    operation()
                except Exception as error:
                    # Never replace the original assertion/503 with a cleanup
                    # exception. Diagnostics contain operation/class only.
                    errors.append(label+': '+type(error).__name__)
            if server:
                attempt('HTTP shutdown',server.shutdown)
                attempt('HTTP close',server.server_close)
                attempt('HTTP join',server_thread.join)
            rows=[]
            if service:
                rows=list(service.db.execute('SELECT * FROM leases'))
                attempt('lease recovery',service.recover)
            if driver:
                for row in direct_leases:
                    attempt('fixture MZ lease close', lambda r=row: driver.close(r))
                rows.extend(direct_leases)
                attempt('isolated Samba stop',driver.stop)
            if service:
                attempt('database close',service.close)
            for row in rows:
                paths=driver.paths(dict(row))
                for key in ('merged','lower'):
                    if is_mountpoint(paths[key]):
                        attempt('normal '+key+' unmount',lambda p=paths[key]:run(['umount',str(p)]))
            if is_mountpoint(ro):
                attempt('normal fixture source unmount',lambda:run(['umount',str(ro)]))
            remaining=any(is_mountpoint(p) for row in rows for p in driver.paths(dict(row)).values()) if driver else False
            if mount_peer is not None:
                mount_peer.stdin.close()
                attempt('peer namespace exit',lambda:mount_peer.wait(timeout=5))
                if mount_peer.poll() is None:
                    mount_peer.kill()
                    mount_peer.wait(timeout=5)
                mount_peer.stdout.close()
            if is_mountpoint(exports):
                attempt('normal workspace export root unmount',lambda:run(['umount',str(exports)]))
            remaining=remaining or is_mountpoint(ro) or is_mountpoint(exports)
            if not remaining:
                attempt('scratch cleanup',lambda:shutil.rmtree(base))
            if errors:
                report='Integration cleanup diagnostics (primary failure preserved): '+', '.join(errors)
                if remaining:
                    report+='; test-owned mounts remain; scratch preserved'
                if original_failure:
                    print(report,file=sys.stderr)
                else:
                    self.fail(report)
