import json
import os
import unittest
from unittest.mock import patch
import test_unified_library
from namespace_support import setup_sources,NamespaceDriver,PROFILE,KEY,RELATIVE
from gameshelf_server.service import Service
from gameshelf_server.http_api import Application


class SaveNamespaceTests(unittest.TestCase):
    setUp=test_unified_library.UnifiedLibraryTests.setUp
    request=test_unified_library.UnifiedLibraryTests.request
    denied=test_unified_library.UnifiedLibraryTests.denied
    library=test_unified_library.UnifiedLibraryTests.library

    def reviewed(self):
        mac,win,manifest=setup_sources(self)
        old=self.s; self.s=Service(old.state,NamespaceDriver(old.state),[self.source],clock=lambda:self.now)
        self.addCleanup(self.s.close); old.close(); self.driver=self.s.driver; self.app=Application(self.s)
        self.s.register_selected(self.root['id'],dict(games=[dict(relative=mac.name,entryPath='.'),dict(relative=win.name,entryPath='Play.exe')]),authorize=lambda:self.app.require_admin(self.at))
        candidates={r['relative']:r for r in self.s.candidates()}
        mg=self.s.publish(candidates[mac.name]['id'],save_paths=manifest['profiles'][0]['variants'][0]['savePaths'])['id']
        wg=self.s.publish(candidates[win.name]['id'])['id']
        self.request('POST',f'/api/admin/library/games/{mg}/variants',dict(variantIDs=[wg]),self.at)
        for user in (self.a,self.b):
            for game in (mg,wg): self.s.grant(user['id'],game,True)
        return mg,wg,manifest

    def activate(self,canonical,variants,**extra):
        return self.request('POST',f'/api/admin/library/games/{canonical}/save-compatibility',
            dict(variantIDs=variants,compatibilityKey=KEY,reviewedProfileID=PROFILE,**extra),self.at)

    def test_optin_native_dot_windows_lease_proof_and_same_canonical_data(self):
        mac,win,_=self.reviewed(); self.activate(mac,[mac,win])
        first=self.request('POST',f'/api/library/games/{mac}/session',dict(variantID=mac,deviceID='mac',saveCompatibilityKey=KEY,adapter='renpy-savedir-v1'))
        ns=first['saveNamespace']; self.assertEqual(set(ns),{'key','relativePath','adapter','reviewedProfileID','revision','rulesSHA256'})
        self.assertEqual((ns['relativePath'],ns['adapter']),(RELATIVE,'renpy-savedir-v1'))
        data=self.driver.namespace_paths[first['leaseID']]; (data/'slot1.save').write_bytes(b'fixture shared slot')
        self.denied(lambda:self.s.session(self.a,win,'old API',adapter='renpy-savedir-v1'),409)
        self.s.release(self.a,first['leaseID'])
        second=self.s.session(self.a,win,'old API',adapter='renpy-savedir-v1')
        self.assertEqual(second['saveNamespace'],ns)
        self.assertEqual(self.driver.namespace_paths[second['leaseID']],data)
        self.assertEqual((data/'slot1.save').read_bytes(),b'fixture shared slot')
        proof=second['windowsLaunch']; self.assertEqual(proof['adapter'],'renpy-savedir-v1')
        self.assertEqual(proof['relativeSavePath'],RELATIVE); self.assertEqual(proof['entryPath'],'Play.exe')
        self.assertNotIn('arguments',proof); self.assertNotIn('args',proof)
        other=self.s.session(self.b,win,'other account',adapter='renpy-savedir-v1')
        self.assertNotEqual(other['saveNamespace']['key'],ns['key'])
        self.assertNotEqual(self.driver.namespace_paths[other['leaseID']],data)
        self.assertFalse((self.driver.namespace_paths[other['leaseID']]/'slot1.save').exists())
        self.assertTrue(second['profileKey'].endswith('/'+win))
        variants=self.library()['games'][0]['variants']; self.assertEqual({v['saveCompatibilityKey'] for v in variants},{KEY})

    def test_nonempty_legacy_saves_and_reserved_target_preserved_409(self):
        mac,win,_=self.reviewed(); self.activate(mac,[mac,win])
        upper=self.driver.upper(self.a['id'],win); save=upper/'game/saves/slot.save'; save.parent.mkdir(parents=True); save.write_bytes(b'old private progress')
        self.denied(lambda:self.s.session(self.a,mac,'mac',adapter='renpy-savedir-v1'),409)
        self.assertEqual(save.read_bytes(),b'old private progress'); self.assertEqual(self.driver.active,{})
        save.unlink(); reserved=upper/RELATIVE/'existing.save'; reserved.parent.mkdir(parents=True); reserved.write_bytes(b'prior reserved data')
        self.denied(lambda:self.s.session(self.a,win,'win',adapter='renpy-savedir-v1'),409)
        self.assertEqual(reserved.read_bytes(),b'prior reserved data')

    def test_no_optin_unknown_profiles_unreviewed_editions_and_key_bypass(self):
        mac,win,_=self.reviewed()
        self.denied(lambda:self.request('POST',f'/api/library/games/{mac}/session',dict(variantID=win,deviceID='win',saveCompatibilityKey=KEY,adapter='renpy-savedir-v1')),409)
        self.activate(mac,[mac])
        self.denied(lambda:self.s.session(self.a,win,'unlisted old API',adapter='renpy-savedir-v1'),409)
        self.denied(lambda:self.request('POST',f'/api/library/games/{mac}/session',dict(variantID=mac,deviceID='mac',saveCompatibilityKey='other-key',adapter='renpy-savedir-v1')),409)
        self.denied(lambda:self.activate(mac,[mac],verified=True),400)

    def test_admin_only_registry_permissions_fingerprints_and_active_edit_gates(self):
        mac,win,_=self.reviewed()
        self.denied(lambda:self.request('POST',f'/api/admin/library/games/{mac}/save-compatibility',dict(variantIDs=[mac,win],compatibilityKey=KEY,reviewedProfileID=PROFILE)),403)
        self.activate(mac,[mac,win]); active=self.s.session(self.a,mac,'active',adapter='renpy-savedir-v1')
        self.denied(lambda:self.activate(mac,[mac,win]),409)
        self.s.release(self.a,active['leaseID'])
        target=self.source/'Reviewed-Windows/renpy/arguments.py'; target.write_bytes(b'tampered runtime args')
        self.denied(lambda:self.s.session(self.a,win,'tampered',adapter='renpy-savedir-v1'),409)
        catalog=self.s.state/'reviewed-renpy-profiles.json'; catalog.chmod(0o644)
        with self.assertRaises(Exception): Service(self.s.state,self.driver,[self.source])

    def test_unknown_old_edition_workspace_never_silently_opens_blank_namespace(self):
        mac,win,_=self.reviewed()
        old=self.s.publish(next(c['id'] for c in self.s.candidates() if c['relative']=='Reviewed-Windows'),save_paths=[dict(path='game/saves',kind='directory'),dict(path='old-save',kind='directory')])['id']
        self.request('POST',f'/api/admin/library/games/{mac}/variants',dict(variantIDs=[old]),self.at)
        self.activate(mac,[mac,win])
        upper=self.driver.upper(self.a['id'],old); private=upper/'unknown-private'; private.parent.mkdir(parents=True); private.write_bytes(b'unknown old progress')
        self.denied(lambda:self.s.session(self.a,mac,'blank view forbidden',adapter='renpy-savedir-v1'),409)
        self.assertEqual(private.read_bytes(),b'unknown old progress')

    def test_native_public_proof_and_old_client_without_capability_fail_closed(self):
        mac,win,_=self.reviewed(); self.activate(mac,[mac,win])
        self.denied(lambda:self.s.session(self.a,mac,'old unaware Mac'),409)
        self.denied(lambda:self.request('POST',f'/api/library/games/{mac}/session',dict(variantID=mac,deviceID='unaware')),409)
        self.assertFalse(any(e[0]=='open' for e in self.driver.events))
        lease=self.s.session(self.a,mac,'aware Mac',adapter='renpy-savedir-v1')
        proof=lease['renpyLaunch']
        self.assertEqual(proof['platform'],'macos'); self.assertEqual(proof['entryPath'],'.')
        self.assertEqual(proof['relativeSavePath'],RELATIVE)
        self.assertEqual(proof['reviewedProfileID'],lease['saveNamespace']['reviewedProfileID'])
        self.assertEqual(proof['rulesSHA256'],lease['saveNamespace']['rulesSHA256'])
        self.assertTrue(any(f['role']=='metadata' and f['path']=='Contents/Info.plist' for f in proof['fingerprints']))
        self.assertTrue(all(set(f)=={'path','role','sourceSHA256','exportSHA256'} for f in proof['fingerprints']))
        encoded=json.dumps(proof)
        self.assertNotIn('replacementBase64',encoded); self.assertNotIn(str(self.source),encoded)
        self.assertNotIn('variant',lease['saveNamespace'])

    def test_failed_namespace_close_retains_revoking_canonical_writer(self):
        mac,win,_=self.reviewed(); self.activate(mac,[mac,win])
        lease=self.s.session(self.a,mac,'aware Mac',adapter='renpy-savedir-v1')
        self.driver.fail_revoke=True
        self.denied(lambda:self.s.release(self.a,lease['leaseID']),503)
        self.assertEqual(self.s.db.execute('SELECT status FROM leases WHERE id=?',(lease['leaseID'],)).fetchone()[0],'revoking')
        self.denied(lambda:self.s.session(self.a,win,'must not open',adapter='renpy-savedir-v1'),503)
        self.assertEqual(set(self.driver.active),{lease['leaseID']})
