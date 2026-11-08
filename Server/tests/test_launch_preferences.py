"""Entry/language choices belong to one account and platform, not a publication."""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_unified_library


class LaunchPreferenceTests(unittest.TestCase):
    setUp=test_unified_library.UnifiedLibraryTests.setUp
    request=test_unified_library.UnifiedLibraryTests.request
    denied=test_unified_library.UnifiedLibraryTests.denied
    game=test_unified_library.UnifiedLibraryTests.game
    publish=test_unified_library.UnifiedLibraryTests.publish
    library=test_unified_library.UnifiedLibraryTests.library

    def prefer(self,g,entry='Game.exe',variant=None,platform='windows',token=None):
        return self.request('POST',f'/api/library/games/{g}/launch-preferences',
                            dict(platform=platform,variantID=variant or g,entryPath=entry),token)

    def test_add_selection_account_entry_preference_preserves_global_order(self):
        g=self.game(); cid=self.s.candidates()[0]['id']; before=self.s.catalog(self.a)
        selected=before['games'][0]['entries'][-1]['path']
        self.s.register_selected(self.root['id'],dict(games=[dict(relative='Novel',entryPath=selected)]),authorize=lambda:self.app.require_admin(self.at))
        body=dict(candidateIDs=[cid],userID=self.b['id'],entryPaths={cid:selected})
        added=self.request('POST','/api/admin/library/add',body,self.at)
        self.assertEqual(added['games'][0]['preferredEntries'],dict(windows=dict(variantID=g,entryPath=selected)))
        self.assertEqual(self.s.catalog(self.a),before)
        self.assertNotIn('preferredEntries',self.library()['games'][0])
        self.assertEqual(self.request('POST','/api/admin/library/add',body,self.at),added)
        self.assertEqual(self.library(self.bt)['games'][0]['preferredEntries'],added['games'][0]['preferredEntries'])

    def test_own_preference_update_clear_and_remove_restore(self):
        g=self.game(); self.s.grant(self.b['id'],g,True)
        self.prefer(g,'翻译.exe')
        expected=dict(windows=dict(variantID=g,entryPath='翻译.exe'))
        self.assertEqual(self.library()['games'][0]['preferredEntries'],expected)
        self.assertNotIn('preferredEntries',self.library(self.bt)['games'][0])
        self.request('DELETE',f'/api/library/games/{g}')
        self.s.grant(self.a['id'],g,True)
        self.assertEqual(self.library()['games'][0]['preferredEntries'],expected)
        cleared=self.request('POST',f'/api/library/games/{g}/launch-preferences',dict(platform='windows',variantID=None,entryPath=None))
        self.assertNotIn('preferredEntries',cleared['game'])

    def test_unknown_stale_wrong_platform_and_other_variant_rejected(self):
        g=self.game(); other=self.source/'Other'; other.mkdir(); (other/'Play.exe').write_bytes(b'MZfixture')
        self.s.scan(self.root['id']); cid=next(c['id'] for c in self.s.candidates() if c['relative']=='Other')
        v=self.s.publish(cid)['id']
        self.denied(lambda:self.prefer(g,variant=v),403)
        self.denied(lambda:self.prefer(g,platform='macos'),409)
        self.denied(lambda:self.prefer(g,entry='helper.exe'),409)
        self.denied(lambda:self.prefer(g,entry='../escape.exe'),400)
        self.denied(lambda:self.prefer(g,token=self.bt),403)
        (self.source/'Novel'/'Game.exe').write_bytes(b'MZchanged')
        self.denied(lambda:self.prefer(g),409)
        self.assertNotIn('preferredEntries',self.library()['games'][0])

    def test_any_own_canonical_lease_blocks_preference_not_other_account(self):
        g=self.game(); self.s.grant(self.b['id'],g,True)
        lease=self.s.session(self.a,g,'active')
        for status in ('opening','active','revoking'):
            self.s.db.execute('UPDATE leases SET status=?,expires=0 WHERE id=?',(status,lease['leaseID']))
            self.denied(lambda:self.prefer(g),409)
        self.prefer(g,token=self.bt)
        self.assertNotIn('preferredEntries',self.library()['games'][0])

    def test_invalid_batch_entry_choice_does_not_write_anything(self):
        self.s.scan(self.root['id']); cid=self.s.candidates()[0]['id']
        before=list(self.s.db.iterdump())
        for choices,status in [({cid:'../escape.exe'},400),({cid:'helper.exe'},409),({'0'*32:'Game.exe'},400)]:
            self.denied(lambda:self.request('POST','/api/admin/library/add',dict(candidateIDs=[cid],userID=self.a['id'],entryPaths=choices),self.at),status)
            self.assertEqual(list(self.s.db.iterdump()),before)

    def test_preference_rechecks_token_and_source_snapshot_after_io(self):
        g=self.game(); original=self.s._lower
        def revoke(row):
            result=original(row); self.s.logout(self.pt); return result
        with patch.object(self.s,'_lower',revoke): self.denied(lambda:self.prefer(g),401)
        self.assertFalse(self.s.db.execute('SELECT 1 FROM library_launch_preferences').fetchone())

    def test_preferred_macos_and_windows_choices_are_independent(self):
        (self.source/'Novel'/'Native.app'/'Contents').mkdir(parents=True)
        self.s.scan(self.root['id']); cid=self.s.candidates()[0]['id']; g=self.s.publish(cid)['id']
        self.s.grant(self.a['id'],g,True)
        self.prefer(g,'Native.app',platform='macos'); self.prefer(g,'Game.exe')
        prefs=self.library()['games'][0]['preferredEntries']
        self.assertEqual(set(prefs),{'macos','windows'})
        self.assertEqual(prefs['macos'],dict(variantID=g,entryPath='Native.app'))
        self.assertEqual(prefs['windows'],dict(variantID=g,entryPath='Game.exe'))
