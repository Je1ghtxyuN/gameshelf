import json
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_service
from gameshelf_server.http_api import Application
from gameshelf_server.common import APIError


class UnifiedLibraryTests(unittest.TestCase):
    publish = test_service.ServiceTests.publish
    denied = test_service.ServiceTests.denied
    # Inherit fixture helpers, not the v1 test methods.
    def setUp(self):
        test_service.ServiceTests.setUp(self)
        self.app = Application(self.s)
        self.at = self.s.login('owner', 'correct horse battery staple')['token']
        self.pt = self.s.login('alice', 'alice password long')['token']
        self.bt = self.s.login('bob', 'bob password long')['token']
    def request(self, method, path, body=None, token=None):
        return self.app.dispatch(method,path,body or {},token or self.pt)
    def library(self,token=None):
        return self.request('GET','/api/library',token=token)
    def game(self):
        g=self.publish(); self.s.grant(self.a['id'],g['id'],True); return g['id']
    def test_v2_identity_default_mapping_and_roots(self):
        g=self.game(); lib=self.library()
        self.assertEqual(lib['schemaVersion'],2)
        self.assertEqual(lib['user'],dict(id=self.a['id'],username='alice',admin=False))
        self.assertEqual(lib['games'][0]['id'],g)
        variant=lib['games'][0]['variants'][0]
        self.assertEqual((variant['id'],variant['platform']),(g,'windows'))
        self.assertNotIn('nasSource',variant)
        self.assertEqual(self.library(self.at)['games'],[])
        owner=self.s.authenticate(self.at)
        self.s.grant(owner['id'],g,True)
        owner_variant=self.library(self.at)['games'][0]['variants'][0]
        relative=self.s.db.execute('SELECT relative FROM games WHERE id=?',(g,)).fetchone()[0]
        self.assertEqual(owner_variant['nasSource'],dict(rootID=self.root['id'],relative=relative))
        self.assertEqual(self.library(self.bt)['games'],[])
        roots=self.request('GET','/api/library/roots')['roots']
        self.assertEqual(roots,[dict(id=self.root['id'],title='Games')])
        self.assertNotIn(str(self.source),json.dumps(lib))
        self.denied(lambda:self.app.dispatch('GET','/api/library',{},''),401)
    def test_family_direct_nas_exposes_only_granted_relative_sources(self):
        game = self.game()
        self.assertNotIn('nasSource', self.library()['games'][0]['variants'][0])
        self.s.direct_nas = True
        library = self.library()
        self.assertTrue(library['directNas'])
        source = library['games'][0]['variants'][0]['nasSource']
        self.assertEqual(source, dict(rootID=self.root['id'], relative='Novel'))
        self.assertNotIn(str(self.source), json.dumps(library))
        self.assertEqual(self.library(self.bt)['games'], [])
        self.s.grant(self.a['id'], game, False)
        self.assertEqual(self.library()['games'], [])

    def test_family_nas_does_not_offer_raw_source_with_detected_saves(self):
        source_save = self.source/'Novel'/'slot1.sav'
        source_save.write_bytes(b'private source progress')
        self.s.direct_nas = True
        game = self.game()
        variant = self.library()['games'][0]['variants'][0]
        self.assertEqual(variant['id'], game)
        self.assertTrue(variant['requiresAccountWorkspace'])
        self.assertNotIn('nasSource', variant)
        self.assertEqual(source_save.read_bytes(), b'private source progress')

    def test_family_scan_add_is_visible_to_current_and_future_accounts(self):
        self.s.direct_nas = True
        private_game = self.game()
        self.assertEqual(self.library(self.bt)['games'], [])
        candidate = self.s.candidates()[0]['id']
        owner = self.s.authenticate(self.at)
        result = self.request('POST', '/api/admin/library/add',
                              dict(candidateIDs=[candidate],userID=owner['id']), self.at)
        self.assertEqual(result['games'][0]['id'], private_game)
        self.assertEqual([game['id'] for game in self.library(self.bt)['games']], [private_game])
        self.s.create_user('later', 'later fixture password')
        later = self.s.login('later', 'later fixture password')['token']
        self.assertEqual([game['id'] for game in self.library(later)['games']], [private_game])
        route = f'/api/library/games/{private_game}'
        self.request('POST', route+'/playtime',
                     dict(eventID=str(uuid.uuid4()),deviceID='pc',elapsedSeconds=12,endedAt=1000), self.bt)
        self.assertEqual(self.library(self.bt)['games'][0]['playtimeSeconds'], 12)
        self.assertEqual(self.library(later)['games'][0]['playtimeSeconds'], 0)
        self.denied(lambda: self.request('DELETE', route, token=self.bt), 403)
        self.request('DELETE', route, token=self.at)
        self.assertEqual(self.library(self.bt)['games'], [])
        self.assertEqual([game['id'] for game in self.library()['games']], [private_game])

    def test_generic_windows_launch_is_owner_only_and_requires_a_published_entry(self):
        g = self.game()
        owner = self.s.authenticate(self.at)
        self.s.grant(owner['id'], g, True)
        path = f'/api/library/games/{g}/session'
        request = dict(variantID=g, deviceID='windows', windowsEntry='Game.exe')
        self.denied(lambda: self.request('POST', path, request), 403)
        self.denied(lambda: self.request('POST', path, dict(request, windowsEntry='uninstall.exe'), self.at), 409)
        with patch.object(self.driver, 'prepare_generic_windows_entry', create=True) as prepare:
            lease = self.request('POST', path, request, self.at)
            self.assertEqual(lease['windowsLaunch'], dict(entryPath='Game.exe',
                isolation='owner-private-overlay', saveProfileVerified=False))
            self.assertEqual(prepare.call_args.args[1], 'Game.exe')

    def test_family_windows_workspace_is_available_to_library_members_with_save_rules(self):
        self.s.direct_nas = True
        (self.source/'Novel'/'slot1.sav').write_bytes(b'private source progress')
        game = self.game()
        route = f'/api/library/games/{game}/session'
        request = dict(variantID=game, deviceID='windows', windowsEntry='Game.exe')
        with patch.object(self.driver, 'prepare_generic_windows_entry', create=True) as prepare:
            self.assertTrue(self.library()['games'][0]['variants'][0]['familyWindowsOverlay'])
            self.denied(lambda: self.request('POST', route, request, self.bt), 403)
            self.denied(lambda: self.request('POST', route, dict(request, windowsEntry='uninstall.exe')), 409)
            lease = self.request('POST', route, request)
            self.assertEqual(lease['userID'], self.a['id'])
            self.assertEqual(lease['windowsLaunch'], dict(entryPath='Game.exe',
                isolation='account-private-overlay', saveProfileVerified=False))
            self.assertEqual(prepare.call_args.args[1], 'Game.exe')
            self.denied(lambda: self.request('POST', route, request), 409)

    def test_family_windows_first_run_uses_workspace_even_without_detected_saves(self):
        self.s.direct_nas = True
        game = self.game()
        route = f'/api/library/games/{game}/session'
        request = dict(variantID=game, deviceID='first-launch', windowsEntry='Game.exe')
        with patch.object(self.driver, 'prepare_generic_windows_entry', create=True):
            variant = self.library()['games'][0]['variants'][0]
            self.assertTrue(variant['requiresAccountWorkspace'])
            self.assertTrue(variant['familyWindowsOverlay'])
            self.assertNotIn('nasSource', variant)
            self.assertEqual(self.request('POST', route, request)['windowsLaunch']['isolation'],
                             'account-private-overlay')

    def test_metadata_cas_and_account_collections(self):
        g=self.game(); cid=str(uuid.uuid4())
        self.request('POST','/api/library/collections',dict(id=cid,name='Favorites'))
        p=f'/api/library/games/{g}/metadata'
        result=self.request('POST',p,dict(revision=0,favorite=True,collectionIDs=[cid]))
        self.assertEqual(result['game']['revision'],1)
        self.assertTrue(result['game']['favorite'])
        self.denied(lambda:self.request('POST',p,dict(revision=0,title='stale')),409)
        self.denied(lambda:self.request('POST',p,dict(revision=1,title='stolen'),self.bt),403)
        with ThreadPoolExecutor(2) as pool:
            def update(title):
                try: return self.request('POST',p,dict(revision=1,title=title))['game']['revision']
                except APIError as e: return e.status
            self.assertEqual(sorted(pool.map(update,['A','B'])),[2,409])
    def test_time_events_retry_conflict_and_bounds(self):
        g=self.game(); p=f'/api/library/games/{g}/playtime'
        event=dict(eventID=str(uuid.uuid4()),deviceID='mac',elapsedSeconds=12,endedAt=1000)
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda _:self.request('POST',p,event),range(2)))
        self.assertEqual(results,[dict(playtimeSeconds=12,lastPlayed=1000)]*2)
        self.denied(lambda:self.request('POST',p,dict(event,elapsedSeconds=13)),409)
        for value in (-1,86401,True,float('nan')):
            self.denied(lambda:self.request('POST',p,dict(event,eventID=str(uuid.uuid4()),elapsedSeconds=value)),400)
        self.denied(lambda:self.request('POST',p,event,self.bt),403)
    def test_add_reuses_scan_retry_without_implicit_grants(self):
        self.s.scan(self.root['id']); c=self.s.candidates()[0]
        body=dict(candidateIDs=[c['id']],userID=self.a['id'])
        with patch.object(self.s,'scan',side_effect=AssertionError('must reuse scan')):
            first=self.request('POST','/api/admin/library/add',body,self.at)
            again=self.request('POST','/api/admin/library/add',body,self.at)
        self.assertEqual(first,again)
        self.assertEqual(len(self.library()['games']),1)
        self.assertEqual(self.library(self.bt)['games'],[])
        self.denied(lambda:self.request('POST','/api/admin/library/add',body),403)
        self.denied(lambda:self.request('POST','/api/admin/library/add',dict(body,candidateIDs=[c['id'],'0'*32]),self.at),404)
    def test_link_old_sessions_canonical_writer_and_stable_profiles(self):
        g=self.game()
        other=self.source/'MacOnly'; (other/'Play.app'/'Contents').mkdir(parents=True)
        self.s.scan(self.root['id']); candidate=next(c for c in self.s.candidates() if c['relative']=='MacOnly')
        v=self.s.publish(candidate['id'])['id']; self.s.grant(self.a['id'],v,True)
        p=f'/api/admin/library/games/{g}/variants'
        self.denied(lambda:self.request('POST',p,dict(variantIDs=[v])),403)
        self.request('POST',p,dict(variantIDs=[v]),self.at)
        self.assertEqual(len(self.library()['games']),1)
        self.assertEqual({x['platform'] for x in self.library()['games'][0]['variants']},{'windows','macos'})
        lease=self.s.session(self.a,g,'old mac')
        self.denied(lambda:self.s.session(self.a,v,'windows'),409)
        self.denied(lambda:self.request('POST',p,dict(variantIDs=[v]),self.at),409)
        self.s.release(self.a,lease['leaseID'])
        self.denied(lambda:self.request('POST',f'/api/library/games/{g}/session',dict(variantID=v,deviceID='mac',saveCompatibilityKey='shared')),409)
        lease=self.request('POST',f'/api/library/games/{g}/session',dict(variantID=v,deviceID='mac'))
        self.assertTrue(lease['profileKey'].endswith('/'+v))
        self.assertEqual({x['id'] for x in self.s.catalog(self.a)['games']},{g,v})
        self.s.grant(self.b['id'],g,True)
        self.s.session(self.b,g,'another account')
    def test_parallel_legacy_different_variants_only_one_writer(self):
        g=self.game(); v=self.s.publish(self.s.candidates()[0]['id'],save_paths=[dict(path='Save',kind='directory')])['id']
        self.s.grant(self.a['id'],v,True)
        self.request('POST',f'/api/admin/library/games/{g}/variants',dict(variantIDs=[v]),self.at)
        def launch(edition):
            try: return self.s.session(self.a,edition,'device')['leaseID']
            except APIError as e: return e.status
        with ThreadPoolExecutor(2) as pool: results=list(pool.map(launch,[g,v]))
        self.assertEqual(results.count(409),1)
        self.assertEqual(len(self.driver.active),1)

    def test_mz_snapshot_commit_uses_canonical_close_barrier(self):
        import zlib
        from gameshelf_server.mz_save_format import (commit_progress_snapshot, read_progress_snapshot,
                                                    read_webkit_progress, webkit_progress_snapshot)

        web = self.game()
        windows = self.s.publish(self.s.candidates()[0]['id'], save_paths=[
            dict(path='save',kind='directory')
        ])['id']
        self.request('POST',f'/api/admin/library/games/{web}/variants',dict(variantIDs=[windows]),self.at)
        for user in (self.a, self.b):
            for variant in (web, windows):
                self.s.grant(user['id'],variant,True)

        def snapshot(user):
            return self.s.state / (user['id']+'-'+web+'.json')

        def encode(value):
            return zlib.compress(json.dumps(value).encode()).decode('latin1').encode()

        progress = dict(system={}, party={'level': 17}, map={}, player={})
        files = {'file1': encode(progress), 'global': encode([None, {'timestamp': 1}])}
        first = self.s.session(self.a,windows,'windows')
        close_driver = self.driver.close
        commit_failure = True

        def close_and_commit(lease):
            close_driver(lease)
            if lease['id'] == first['leaseID']:
                if commit_failure:
                    raise OSError('synthetic snapshot write failure')
                commit_progress_snapshot(snapshot(self.a),123456,files,expected={})

        with patch.object(self.driver,'close',side_effect=close_and_commit):
            self.denied(lambda:self.s.release(self.a,first['leaseID']),503)
            self.denied(lambda:self.s.session(self.a,web,'mac'),503)
            self.assertFalse(snapshot(self.a).exists())
            other = self.s.session(self.b,web,'other account')
            self.assertFalse(snapshot(self.b).exists())
            self.s.release(self.b,other['leaseID'])
            commit_failure = False
            self.s.release(self.a,first['leaseID'])
            second = self.s.session(self.a,web,'mac')
            reopened = read_progress_snapshot(snapshot(self.a),123456)
            mac_snapshot = webkit_progress_snapshot('a'*64,123456,reopened)
            self.assertEqual(read_webkit_progress(mac_snapshot,'a'*64,123456),files)
            self.s.release(self.a,second['leaseID'])

    def test_malformed_selectors_fail_as_client_errors(self):
        g=self.game()
        self.denied(lambda:self.request('POST',f'/api/library/games/{g}/session',dict(variantID={},deviceID='mac')),400)
        self.s.scan(self.root['id'])
        self.denied(lambda:self.request('POST','/api/admin/library/add',dict(candidateIDs=[self.s.candidates()[0]['id']],userID=[]),self.at),400)

    def test_time_events_and_account_metadata_survive_reopen(self):
        g=self.game(); event=dict(eventID=str(uuid.uuid4()),deviceID='mac',elapsedSeconds=5,endedAt=10)
        self.request('POST',f'/api/library/games/{g}/playtime',event)
        self.request('POST',f'/api/library/games/{g}/metadata',dict(revision=0,favorite=True))
        from gameshelf_server.service import Service
        reopened=Service(self.s.state,self.driver,[self.source],clock=lambda:self.now)
        try:
            app=Application(reopened)
            result=app.dispatch('POST',f'/api/library/games/{g}/playtime',event,self.pt)
            self.assertEqual(result['playtimeSeconds'],5)
            self.assertTrue(app.dispatch('GET','/api/library',{},self.pt)['games'][0]['favorite'])
            self.s.grant(self.b['id'],g,True)
            other=self.library(self.bt)['games'][0]
            self.assertFalse(other['favorite']); self.assertEqual(other['revision'],0)
            self.assertEqual(other['playtimeSeconds'],0)
        finally: reopened.close()

    def test_ungranted_linked_variant_hidden_and_launch_denied(self):
        g=self.game(); v=self.s.publish(self.s.candidates()[0]['id'],save_paths=[dict(path='Save',kind='directory')])['id']
        self.request('POST',f'/api/admin/library/games/{g}/variants',dict(variantIDs=[v]),self.at)
        self.assertEqual([x['id'] for x in self.library()['games'][0]['variants']],[g])
        self.denied(lambda:self.request('POST',f'/api/library/games/{g}/session',dict(variantID=v,deviceID='mac')),403)

    def test_link_preserves_account_records_and_busy_failed_shutdown(self):
        g=self.game(); v=self.s.publish(self.s.candidates()[0]['id'],save_paths=[dict(path='Save',kind='directory')])['id']
        self.s.grant(self.a['id'],v,True)
        self.request('POST',f'/api/library/games/{v}/metadata',dict(revision=0,favorite=True))
        self.denied(lambda:self.request('POST',f'/api/admin/library/games/{g}/variants',dict(variantIDs=[v]),self.at),409)
        self.assertEqual(len(self.library()['games']),2)
        lease=self.s.session(self.a,g,'old')
        self.driver.fail_revoke=True
        self.denied(lambda:self.s.release(self.a,lease['leaseID']),503)
        self.denied(lambda:self.s.session(self.a,g,'other'),503)
        self.driver.fail_revoke=False
        self.s.release(self.a,lease['leaseID'])

    def test_add_rechecks_admin_token_after_filesystem_io(self):
        self.s.scan(self.root['id']); cid=self.s.candidates()[0]['id']
        original=self.s._lower
        def revoke_during_validation(row):
            value=original(row)
            self.s.logout(self.at)
            return value
        with patch.object(self.s,'_lower',revoke_during_validation):
            self.denied(lambda:self.request('POST','/api/admin/library/add',dict(candidateIDs=[cid],userID=self.a['id']),self.at),401)
        self.assertEqual(self.library()['games'],[])
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM games').fetchone()[0],0)

    def test_session_rechecks_canonical_after_link_during_source_io(self):
        g=self.game(); v=self.s.publish(self.s.candidates()[0]['id'],save_paths=[dict(path='Save',kind='directory')])['id']
        self.s.grant(self.a['id'],v,True)
        original=self.s._lower
        def link_during_validation(row):
            value=original(row)
            self.request('POST',f'/api/admin/library/games/{g}/variants',dict(variantIDs=[v]),self.at)
            return value
        with patch.object(self.s,'_lower',link_during_validation):
            lease=self.s.session(self.a,v,'old')
        self.denied(lambda:self.s.session(self.a,g,'new'),409)
        self.assertTrue(lease['profileKey'].endswith('/'+v))

    def test_fixture_is_synthetic_wire_library(self):
        from pathlib import Path
        fixture=json.loads((Path(__file__).resolve().parents[2]/'Contracts/unified-library-v2.json').read_text())
        self.assertEqual(fixture['schemaVersion'],2)
        self.assertNotIn(str(self.source),json.dumps(fixture))
        self.assertEqual({v['platform'] for v in fixture['games'][0]['variants']},{'windows','macos'})
    def test_canonical_cover_allows_only_logical_game_grantees(self):
        import base64
        from test_onboarding import JPEG
        g=self.game(); v=self.s.publish(self.s.candidates()[0]['id'],save_paths=[dict(path='Save',kind='directory')])['id']
        self.s.grant(self.b['id'],v,True)
        self.request('POST',f'/api/admin/library/games/{g}/variants',dict(variantIDs=[v]),self.at)
        self.request('POST',f'/api/admin/games/{g}/cover',dict(data=base64.b64encode(JPEG).decode()),self.at)
        cover=self.request('GET',f'/api/library/games/{g}/cover',token=self.bt)
        self.assertEqual(cover['mime'],'image/jpeg')
        self.assertEqual(base64.b64decode(cover['data']),JPEG)
        self.s.grant(self.b['id'],v,False)
        self.denied(lambda:self.request('GET',f'/api/library/games/{g}/cover',token=self.bt),403)
    def test_library_availability_reflects_persisted_scan_without_rescan(self):
        import shutil
        g=self.game()
        shutil.rmtree(self.source/'Novel')
        self.s.scan(self.root['id'])
        with patch.object(self.s,'scan',side_effect=AssertionError('library must reuse scan')):
            self.assertFalse(self.library()['games'][0]['variants'][0]['available'])
        self.assertEqual(self.s.catalog(self.a)['games'][0]['id'],g)

    def test_mz_executable_is_windows_html_is_web_and_unknown_is_not_macos(self):
        (self.source/'Novel'/'index.html').write_text('<html>synthetic</html>')
        g=self.game()
        entries=[dict(path='Game.exe',backend='rpgmz',engine='RPG Maker MZ'),
                 dict(path='index.html',backend='rpgmz',engine='RPG Maker MZ'),
                 dict(path='unknown.bin',backend='unknown',engine='unknown'),
                 dict(path='unknown-native.bin',backend='native',engine='unknown')]
        self.s.db.execute('UPDATE games SET entries=? WHERE id=?',(json.dumps(entries),g))
        variants=self.library()['games'][0]['variants']
        self.assertEqual({v['platform'] for v in variants},{'windows','web'})
        self.assertEqual(next(v for v in variants if v['platform']=='windows')['entries'],[entries[0]])
        self.assertEqual(next(v for v in variants if v['platform']=='web')['entries'],[entries[1]])
        self.assertEqual(self.s.catalog(self.a)['games'][0]['entries'],entries)
