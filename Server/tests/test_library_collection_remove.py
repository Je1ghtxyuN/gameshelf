"""Own collection deletion atomically removes memberships with CAS revisions."""
import json
import sqlite3
import uuid
import unittest
import test_unified_library


class LibraryCollectionRemoveTests(unittest.TestCase):
    setUp = test_unified_library.UnifiedLibraryTests.setUp
    request = test_unified_library.UnifiedLibraryTests.request
    denied = test_unified_library.UnifiedLibraryTests.denied
    game = test_unified_library.UnifiedLibraryTests.game
    publish = test_unified_library.UnifiedLibraryTests.publish
    library = test_unified_library.UnifiedLibraryTests.library

    def prepare(self):
        g=self.game(); self.s.grant(self.b['id'],g,True)
        cid=str(uuid.uuid4()); keep=str(uuid.uuid4())
        for token in (self.pt,self.bt):
            for key in (cid,keep):
                self.request('POST','/api/library/collections',dict(id=key,name='Fixture '+key),token)
            self.request('POST',f'/api/library/games/{g}/metadata',dict(revision=0,collectionIDs=[cid,keep],favorite=True),token)
        return g,cid,keep

    def test_remove_own_collection_memberships_revisions_and_idempotent_retry(self):
        g,cid,keep=self.prepare(); other=self.library(self.bt)
        with unittest.mock.patch.object(self.s,'_external',side_effect=AssertionError('no source I/O')):
            self.assertEqual(self.request('DELETE',f'/api/library/collections/{cid}'),{})
            self.assertEqual(self.request('DELETE',f'/api/library/collections/{cid}'),{})
        own=self.library(); meta=own['games'][0]
        self.assertEqual(meta['revision'],2)
        self.assertEqual(meta['collectionIDs'],[keep]); self.assertTrue(meta['favorite'])
        self.assertEqual([c['id'] for c in own['collections']],[keep])
        self.assertEqual(self.library(self.bt),other)
        self.denied(lambda:self.request('POST',f'/api/library/games/{g}/metadata',dict(revision=1,title='stale')),409)

    def test_delete_only_increments_metadata_that_referenced_collection(self):
        g,cid,keep=self.prepare()
        self.request('POST',f'/api/library/games/{g}/metadata',dict(revision=1,collectionIDs=[keep]))
        self.request('DELETE',f'/api/library/collections/{cid}')
        self.assertEqual(self.library()['games'][0]['revision'],2)
        # A collection existing only in the other account is not touched.
        self.request('DELETE',f'/api/library/collections/{cid}')
        self.assertIn(cid,[c['id'] for c in self.library(self.bt)['collections']])

    def test_auth_and_no_cross_account_selector(self):
        g,cid,keep=self.prepare(); path=f'/api/library/collections/{cid}'
        self.denied(lambda:self.app.dispatch('DELETE',path,{},''),401)
        self.denied(lambda:self.request('DELETE',path,dict(userID=self.b['id'])),400)
        self.denied(lambda:self.request('DELETE','/api/library/collections/not-a-uuid'),404)

    def test_failure_to_update_membership_rolls_back_collection_deletion(self):
        g,cid,keep=self.prepare(); snapshot=list(self.s.db.iterdump())
        self.s.db.execute("CREATE TEMP TRIGGER fail_membership BEFORE UPDATE ON library_metadata BEGIN SELECT RAISE(ABORT,'fixture rollback'); END")
        try:
            with self.assertRaises(sqlite3.IntegrityError): self.request('DELETE',f'/api/library/collections/{cid}')
            self.assertFalse(self.s.db.in_transaction)
            self.assertEqual(list(self.s.db.iterdump()),snapshot)
        finally: self.s.db.execute('DROP TRIGGER fail_membership')
