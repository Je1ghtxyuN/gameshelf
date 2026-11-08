"""Synthetic proof mechanics only; no actual Windows execution or NAS mounts."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import unittest
from unittest.mock import patch
import test_unified_library
from test_service import FakeDriver

PROFILE='gameshelf.synthetic-relative-windows.v1'
FIXTURE=Path(__file__).resolve().parents[2]/'Contracts/Fixtures/reviewed-relative-windows/build_fixture.py'


class ProofDriver(FakeDriver):
    def open(self,lease,lower,user,*,save_paths=None):
        share=super().open(lease,lower,user,save_paths=save_paths)
        view=self.active[lease['id']]
        entry=view/'Fixture.exe'
        if not entry.exists(): shutil.copyfile(lower/'Fixture.exe',entry)
        (view/'savedata').mkdir(exist_ok=True)
        return share
    def verify_launch_profile(self,lease,profile):
        from gameshelf_server.reviewed_profiles import verify_export
        verify_export(self.active[lease['id']],profile)


class ReviewedLaunchProfileTests(unittest.TestCase):
    setUp = test_unified_library.UnifiedLibraryTests.setUp
    request = test_unified_library.UnifiedLibraryTests.request
    denied = test_unified_library.UnifiedLibraryTests.denied

    def fixture(self):
        payload=runpy.run_path(str(FIXTURE))['build']()
        folder=self.source/'Synthetic'; folder.mkdir()
        (folder/'Fixture.exe').write_bytes(payload); (folder/'savedata').mkdir()
        self.s.scan(self.root['id'])
        cid=next(c['id'] for c in self.s.candidates() if c['relative']=='Synthetic')
        game=self.s.publish(cid)['id']; self.s.grant(self.a['id'],game,True)
        self.s.driver=self.driver=ProofDriver(self.s.state)
        return game,folder,payload

    def enable(self,game,enabled=True,token=None,**extra):
        return self.request('POST',f'/api/admin/games/{game}/launch-profile',dict(reviewedProfileID=PROFILE,enabled=enabled,**extra),token or self.at)

    def test_profile_registry_explicit_optin_and_pinned_success_evidence(self):
        game,folder,payload=self.fixture()
        profiles=self.request('GET','/api/admin/launch-profiles',token=self.at)['profiles']
        profile=next(p for p in profiles if p['id']==PROFILE)
        self.assertTrue(profile['synthetic']); self.assertFalse(profile['crossVariantSharing'])
        self.assertEqual(profile['entrySHA256'],hashlib.sha256(payload).hexdigest())
        old=self.s.session(self.a,game,'old')
        self.assertNotIn('windowsLaunch',old)
        self.s.release(self.a,old['leaseID'])
        self.enable(game)
        lease=self.request('POST',f'/api/library/games/{game}/session',dict(variantID=game,deviceID='windows'))
        proof=lease['windowsLaunch']
        self.assertEqual((proof['entryPath'],proof['isolation'],proof['saveProfileVerified']),('Fixture.exe','reviewed-relative-saves',True))
        for field in ('entrySHA256','rulesSHA256','evidenceSHA256'):
            self.assertEqual(proof[field],profile[field])
        self.assertEqual(proof['reviewedProfileID'],PROFILE)
        self.assertEqual(proof['profileRevision'],profile['revision'])
        self.assertEqual(proof['savePaths'],[dict(path='savedata',kind='directory')])
        self.assertEqual(proof['sourceSignature'],self.s.db.execute('SELECT signature FROM games WHERE id=?',(game,)).fetchone()[0])
        self.assertTrue(lease['profileKey'].endswith('/'+game))
        self.denied(lambda:self.s.session(self.a,game,'v1 bypass'),409)
        self.s.release(self.a,lease['leaseID'])
        legacy=self.s.session(self.a,game,'legacy')
        self.assertEqual(legacy['windowsLaunch'],proof)

    def test_admin_validation_unknown_profiles_and_busy_canonical(self):
        game,folder,payload=self.fixture()
        self.denied(lambda:self.enable(game,token=self.pt),403)
        self.denied(lambda:self.request('GET','/api/admin/launch-profiles'),403)
        self.denied(lambda:self.enable(game,enabled='true'),400)
        self.denied(lambda:self.request('POST',f'/api/admin/games/{game}/launch-profile',dict(reviewedProfileID='unreviewed',enabled=True),self.at),404)
        self.denied(lambda:self.enable(game,saveProfileVerified=True),400)
        self.enable(game); active=self.s.session(self.a,game,'active')
        self.denied(lambda:self.enable(game,False),409)
        self.s.release(self.a,active['leaseID']); self.enable(game,False)
        self.assertNotIn('windowsLaunch',self.s.session(self.a,game,'disabled'))

    def test_arbitrary_executable_or_rule_cannot_obtain_reviewed_status(self):
        game,folder,payload=self.fixture()
        info=(folder/'Fixture.exe').stat()
        (folder/'Fixture.exe').write_bytes(payload[:-1]+b'\x01')
        os.utime(folder/'Fixture.exe',ns=(info.st_atime_ns,info.st_mtime_ns))
        self.denied(lambda:self.enable(game),409)
        self.assertFalse(self.s.db.execute('SELECT 1 FROM reviewed_launch_optins WHERE game_id=?',(game,)).fetchone())

    def test_entry_crypto_checked_on_launch_even_when_tree_signature_unchanged(self):
        game,folder,payload=self.fixture(); self.enable(game)
        info=(folder/'Fixture.exe').stat()
        (folder/'Fixture.exe').write_bytes(payload[:-1]+b'\x01')
        os.utime(folder/'Fixture.exe',ns=(info.st_atime_ns,info.st_mtime_ns))
        self.denied(lambda:self.s.session(self.a,game,'tampered source'),409)
        self.assertEqual(self.driver.active,{})
        self.assertFalse(any(event[0]=='open' for event in self.driver.events))

    def test_copied_up_account_entry_tampering_and_save_symlinks_fail_closed(self):
        game,folder,payload=self.fixture(); self.enable(game)
        lease=self.s.session(self.a,game,'first'); view=self.driver.active[lease['leaseID']]
        self.s.release(self.a,lease['leaseID'])
        (view/'Fixture.exe').write_bytes(b'MZunreviewed copied up executable')
        self.denied(lambda:self.s.session(self.a,game,'tampered view'),409)
        self.assertEqual(self.driver.active,{})
        (view/'Fixture.exe').write_bytes(payload)
        (view/'savedata'/'progress.txt').symlink_to(folder/'Fixture.exe')
        self.denied(lambda:self.s.session(self.a,game,'symlink target'),409)
        self.assertEqual(self.driver.active,{})
        self.assertEqual((folder/'Fixture.exe').read_bytes(),payload)

    def test_driver_without_export_proof_cannot_certify_or_export(self):
        game,folder,payload=self.fixture(); self.enable(game)
        self.s.driver=self.driver=FakeDriver(self.s.state)
        self.denied(lambda:self.s.session(self.a,game,'unsupported driver'),409)
        self.assertEqual(self.driver.active,{})
        self.assertFalse(any(event[0]=='open' for event in self.driver.events))

    def test_optin_reauthorizes_after_source_read_and_no_crossvariant_sharing(self):
        game,folder,payload=self.fixture(); original=self.s._lower
        def revoke(row):
            result=original(row); self.s.logout(self.at); return result
        with patch.object(self.s,'_lower',revoke): self.denied(lambda:self.enable(game),401)
        self.assertFalse(self.s.db.execute('SELECT 1 FROM reviewed_launch_optins WHERE game_id=?',(game,)).fetchone())
        self.at=self.s.login('owner','correct horse battery staple')['token']; self.enable(game)
        self.denied(lambda:self.request('POST',f'/api/library/games/{game}/session',dict(variantID=game,deviceID='windows',saveCompatibilityKey='pretend-shared')),409)

    def test_profile_cannot_certify_removed_published_entry(self):
        game,folder,payload=self.fixture(); self.enable(game)
        self.s.db.execute('UPDATE games SET entries=? WHERE id=?',(json.dumps([dict(path='Other.exe',backend='wine',engine='unknown')]),game))
        self.denied(lambda:self.s.session(self.a,game,'removed launcher'),409)
        self.assertEqual(self.driver.active,{})

    def test_rule_mismatch_rejected_and_optin_persists_without_merging_account_saves(self):
        game,folder,payload=self.fixture()
        self.s.db.execute('UPDATE games SET save_paths=? WHERE id=?',(json.dumps([dict(path='AppData',kind='directory')]),game))
        self.denied(lambda:self.enable(game),409)
        self.s.db.execute('UPDATE games SET save_paths=? WHERE id=?',(json.dumps([dict(path='savedata',kind='directory')]),game))
        self.enable(game)
        from gameshelf_server.service import Service
        reopened=Service(self.s.state,self.driver,[self.source],clock=lambda:self.now)
        try:
            first=reopened.session(self.a,game,'A')
            save=self.driver.active[first['leaseID']]/'savedata'/'progress.txt'; save.write_bytes(b'account A fixture')
            self.s.grant(self.b['id'],game,True)
            second=reopened.session(self.b,game,'B')
            self.assertIn('windowsLaunch',second)
            self.assertFalse((self.driver.active[second['leaseID']]/'savedata'/'progress.txt').exists())
            reopened.release(self.b,second['leaseID']); reopened.release(self.a,first['leaseID'])
            self.assertEqual(save.read_bytes(),b'account A fixture')
        finally: reopened.close()
