"""Account-scoped persisted scan-selection state; fixture directories only."""
import sqlite3
import unittest
from unittest.mock import patch
import test_unified_library
from gameshelf_server.http_api import Application
from gameshelf_server.service import Service, tree_signature


class LibraryCandidateTests(unittest.TestCase):
    setUp = test_unified_library.UnifiedLibraryTests.setUp
    request = test_unified_library.UnifiedLibraryTests.request
    denied = test_unified_library.UnifiedLibraryTests.denied

    def scan(self):
        self.s.scan(self.root['id'])
        return self.s.candidates()[0]['id']

    def candidates(self, token=None):
        return self.request('GET','/api/library/candidates',token=token or self.at)['candidates']

    def ignore(self, ids, ignored=True, token=None):
        return self.request('POST','/api/library/candidates/ignore',dict(candidateIDs=ids,ignored=ignored),token or self.at)

    def second_admin(self):
        account=self.s.create_user('other-admin','other admin password',True)
        token=self.s.login('other-admin','other admin password')['token']
        return account,token

    def test_candidate_state_permissions_and_v1_shape_unchanged(self):
        cid=self.scan(); old=self.s.candidates()[0]
        for method,path,body in [('GET','/api/library/candidates',{}),('POST','/api/library/candidates/ignore',dict(candidateIDs=[cid],ignored=True))]:
            self.denied(lambda:self.app.dispatch(method,path,body,''),401)
            self.denied(lambda:self.request(method,path,body,self.pt),403)
        row=self.candidates()[0]
        self.assertEqual(row,dict(old,added=False,ignored=False))
        self.assertEqual(self.request('GET','/api/admin/candidates',token=self.at)['candidates'],[old])
        self.assertNotIn('added',self.s.candidates()[0])
        self.assertNotIn('ignored',self.s.candidates()[0])

    def test_ignore_shared_across_devices_reopen_and_account_isolated(self):
        cid=self.scan(); other,other_token=self.second_admin()
        source_before=tree_signature(self.source)
        response=self.ignore([cid]); self.assertTrue(response['candidates'][0]['ignored'])
        device_two=self.s.login('owner','correct horse battery staple')['token']
        with patch.object(self.s,'scan',side_effect=AssertionError('must reuse persisted scan')):
            self.assertTrue(self.candidates(device_two)[0]['ignored'])
            self.assertFalse(self.candidates(other_token)[0]['ignored'])
        reopened=Service(self.s.state,self.driver,[self.source],clock=lambda:self.now)
        try:
            row=Application(reopened).dispatch('GET','/api/library/candidates',{},device_two)['candidates'][0]
            self.assertTrue(row['ignored'])
        finally: reopened.close()
        self.ignore([cid],False,device_two)
        self.assertFalse(self.candidates()[0]['ignored'])
        self.assertEqual(tree_signature(self.source),source_before)
        self.assertEqual(self.s.grants(),[])

    def test_added_requires_current_signature_and_own_edition_grant(self):
        cid=self.scan(); game=self.s.publish(cid)['id']
        self.s.grant(self.a['id'],game,True)
        self.assertFalse(self.candidates()[0]['added'])
        self.s.grant(self.admin['id'],game,True)
        self.assertTrue(self.candidates()[0]['added'])
        second_device=self.s.login('owner','correct horse battery staple')['token']
        self.assertTrue(self.candidates(second_device)[0]['added'])
        self.s.grant(self.admin['id'],game,False)
        self.assertFalse(self.candidates()[0]['added'])
        # A historical grant must not label a newer scanned edition as added.
        self.s.grant(self.admin['id'],game,True)
        (self.source/'Novel'/'Game.exe').write_bytes(b'MZchanged fixture version')
        self.s.scan(self.root['id'])
        self.assertFalse(self.candidates()[0]['added'])
        # Publication alone is not enough; one matching edition must be granted.
        new=self.s.publish(cid)['id']
        self.assertFalse(self.candidates()[0]['added'])
        self.s.grant(self.admin['id'],new,True)
        self.assertTrue(self.candidates()[0]['added'])

    def test_add_clears_only_target_account_ignored_and_is_retry_safe(self):
        cid=self.scan(); other,other_token=self.second_admin()
        self.ignore([cid]); self.ignore([cid],token=other_token)
        body=dict(candidateIDs=[cid],userID=other['id'])
        first=self.request('POST','/api/admin/library/add',body,self.at)
        self.assertEqual(self.request('POST','/api/admin/library/add',body,self.at),first)
        self.assertEqual((self.candidates(other_token)[0]['added'],self.candidates(other_token)[0]['ignored']),(True,False))
        self.assertEqual((self.candidates()[0]['added'],self.candidates()[0]['ignored']),(False,True))
        self.request('POST','/api/admin/library/add',dict(body,userID=self.admin['id']),self.at)
        self.assertEqual((self.candidates()[0]['added'],self.candidates()[0]['ignored']),(True,False))

    def test_ignore_validates_whole_batch_without_partial_mutation(self):
        cid=self.scan()
        self.denied(lambda:self.ignore([cid,'0'*32]),404)
        self.assertFalse(self.candidates()[0]['ignored'])
        for body in [dict(candidateIDs=[cid],ignored=1),dict(candidateIDs=[cid],ignored='true'),
                     dict(candidateIDs=[],ignored=True),dict(candidateIDs=[cid,cid],ignored=True),
                     dict(candidateIDs=['../escape'],ignored=True),dict(candidateIDs=[{}],ignored=True),
                     dict(candidateIDs=[cid],ignored=True,userID=self.a['id'])]:
            self.denied(lambda:self.request('POST','/api/library/candidates/ignore',body,self.at),400)
        self.assertFalse(self.candidates()[0]['ignored'])

    def test_ignore_rolls_back_if_a_later_batch_write_fails(self):
        cid=self.scan()
        extra=self.source/'Second'; extra.mkdir(); (extra/'Play.exe').write_bytes(b'MZfixture')
        self.s.scan(self.root['id'])
        second=next(c['id'] for c in self.s.candidates() if c['id']!=cid)
        # Establish the API/table before injecting an actual SQLite write failure.
        self.ignore([cid],False)
        self.s.db.execute("CREATE TEMP TRIGGER fail_ignore BEFORE INSERT ON library_candidate_ignores WHEN NEW.candidate_id='"+second+"' BEGIN SELECT RAISE(ABORT,'fixture transaction failure'); END")
        try:
            with self.assertRaises(sqlite3.IntegrityError): self.ignore([cid,second])
            self.assertFalse(self.s.db.in_transaction)
            self.assertTrue(all(not c['ignored'] for c in self.candidates()))
        finally: self.s.db.execute('DROP TRIGGER fail_ignore')
