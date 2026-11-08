"""Removing an account card revokes only its grants and preserves private data."""
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_unified_library


class LibraryRemoveTests(unittest.TestCase):
    setUp = test_unified_library.UnifiedLibraryTests.setUp
    request = test_unified_library.UnifiedLibraryTests.request
    denied = test_unified_library.UnifiedLibraryTests.denied
    game = test_unified_library.UnifiedLibraryTests.game
    publish = test_unified_library.UnifiedLibraryTests.publish
    library = test_unified_library.UnifiedLibraryTests.library

    def linked(self):
        g=self.game(); v=self.s.publish(self.s.candidates()[0]['id'],save_paths=[dict(path='Save',kind='directory')])['id']
        self.s.grant(self.a['id'],v,True)
        self.request('POST',f'/api/admin/library/games/{g}/variants',dict(variantIDs=[v]),self.at)
        return g,v

    def test_remove_own_linked_card_preserves_data_other_accounts_and_readd(self):
        g,v=self.linked()
        self.request('POST',f'/api/library/games/{g}/metadata',dict(revision=0,favorite=True,title='My title'))
        event=dict(eventID=str(uuid.uuid4()),deviceID='mac',elapsedSeconds=17,endedAt=1000)
        self.request('POST',f'/api/library/games/{g}/playtime',event)
        before=self.library()['games'][0]
        own=self.s.session(self.a,v,'old client')
        save=self.driver.active[own['leaseID']]/'.gameshelf-profile'/'private.save'
        save.write_bytes(b'fixture private progress')
        self.s.release(self.a,own['leaseID'])
        self.s.grant(self.b['id'],v,True)
        other=self.s.session(self.b,v,'other account')
        with patch.object(self.s,'_external',side_effect=AssertionError('remove must not touch files')):
            self.assertEqual(self.request('DELETE',f'/api/library/games/{g}'),{})
            self.assertEqual(self.request('DELETE',f'/api/library/games/{g}'),{})
        self.assertEqual(self.library()['games'],[])
        self.assertEqual(self.s.catalog(self.a)['games'],[])
        self.assertEqual([x['id'] for x in self.s.catalog(self.b)['games']],[v])
        self.assertIn(other['leaseID'],self.driver.active)
        self.assertEqual(save.read_bytes(),b'fixture private progress')
        # Admin add is still required; no owner adoption or implicit self-grant.
        # This candidate has ambiguous save-rule editions, so use existing explicit
        # edition grant APIs to restore both original editions without guessing.
        for edition in (g,v):
            self.request('POST','/api/admin/grants',dict(userID=self.a['id'],gameID=edition,allowed=True),self.at)
        self.assertEqual(self.library()['games'][0],before)
        self.assertEqual(self.request('POST',f'/api/library/games/{g}/playtime',event)['playtimeSeconds'],17)

    def test_remove_rejects_every_nonclosed_own_canonical_lease(self):
        g,v=self.linked()
        lease=self.s.session(self.a,v,'legacy')
        for status in ('opening','active','revoking'):
            self.s.db.execute('UPDATE leases SET status=?,expires=0 WHERE id=?',(status,lease['leaseID']))
            grants=self.s.grants()
            self.denied(lambda:self.request('DELETE',f'/api/library/games/{g}'),409)
            self.assertEqual(self.s.grants(),grants)
        self.s.db.execute("UPDATE leases SET status='active' WHERE id=?",(lease['leaseID'],))
        self.s.release(self.a,lease['leaseID'])
        self.request('DELETE',f'/api/library/games/{g}')
        self.assertEqual(self.library()['games'],[])

    def test_remove_auth_exact_fields_and_other_account_grants(self):
        g=self.game(); path=f'/api/library/games/{g}'
        self.denied(lambda:self.app.dispatch('DELETE',path,{},''),401)
        self.denied(lambda:self.request('DELETE',path,dict(userID=self.a['id']),self.bt),400)
        self.assertEqual(self.request('DELETE',path,token=self.bt),{})
        self.assertEqual(self.library()['games'][0]['id'],g)
        self.denied(lambda:self.request('DELETE','/api/library/games/'+'0'*32),404)

    def test_remove_during_legacy_source_io_prevents_export(self):
        import threading
        g=self.game(); entered=threading.Event(); unblock=threading.Event()
        original=self.s._lower
        def paused(row):
            value=original(row); entered.set(); unblock.wait(4); return value
        with ThreadPoolExecutor(2) as pool,patch.object(self.s,'_lower',paused):
            launch=pool.submit(self.s.session,self.a,g,'legacy',token=self.pt)
            try:
                self.assertTrue(entered.wait(1))
                self.request('DELETE',f'/api/library/games/{g}')
            finally: unblock.set()
            self.denied(lambda:launch.result(timeout=4),403)
        self.assertEqual(self.driver.active,{})

    def test_readd_after_remove_restores_metadata_via_add_selection(self):
        g=self.game(); cid=self.s.candidates()[0]['id']
        self.request('POST',f'/api/library/games/{g}/metadata',dict(revision=0,favorite=True))
        self.request('DELETE',f'/api/library/games/{g}')
        restored=self.request('POST','/api/admin/library/add',dict(candidateIDs=[cid],userID=self.a['id']),self.at)
        self.assertEqual(restored['games'][0]['id'],g)
        self.assertEqual(restored['games'][0]['revision'],1)
        self.assertTrue(restored['games'][0]['favorite'])
