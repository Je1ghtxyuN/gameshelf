"""Batch publication errors must leave every database record unchanged."""
import unittest
import test_unified_library


class LibraryAddPreflightTests(unittest.TestCase):
    setUp = test_unified_library.UnifiedLibraryTests.setUp
    request = test_unified_library.UnifiedLibraryTests.request
    denied = test_unified_library.UnifiedLibraryTests.denied
    game = test_unified_library.UnifiedLibraryTests.game
    publish = test_unified_library.UnifiedLibraryTests.publish

    def batch_with_ambiguous_second(self):
        second=self.source/'Second'; second.mkdir()
        (second/'Play.exe').write_bytes(b'MZfixture')
        self.s.scan(self.root['id'])
        candidates={c['relative']:c for c in self.s.candidates()}
        for path in ('SaveA','SaveB'):
            self.s.publish(candidates['Second']['id'],save_paths=[dict(path=path,kind='directory')])
        ids=[candidates['Novel']['id'],candidates['Second']['id']]
        self.request('POST','/api/library/candidates/ignore',dict(candidateIDs=ids,ignored=True),self.at)
        return ids

    def assert_batch_conflict_unchanged(self,ids):
        catalog=self.s.catalog(self.a)
        snapshot=list(self.s.db.iterdump())
        self.denied(lambda:self.request('POST','/api/admin/library/add',dict(candidateIDs=ids,userID=self.admin['id']),self.at),409)
        self.assertEqual(self.s.catalog(self.a),catalog,'Failed batch modified an already-granted edition')
        self.assertEqual(list(self.s.db.iterdump()),snapshot,'Failed batch must not modify any database record')
        self.assertFalse(self.s.db.in_transaction)

    def test_ambiguous_later_candidate_cannot_refresh_existing_granted_entries(self):
        self.game()
        ids=self.batch_with_ambiguous_second()
        before=self.s.catalog(self.a)['games'][0]['entries']
        self.s.register_selected(self.root['id'],dict(games=[dict(relative='Novel',entryPath=before[-1]['path'])]),
                                 authorize=lambda:self.app.require_admin(self.at))
        self.assertNotEqual(self.s.candidates()[0]['entries'],before)
        self.assert_batch_conflict_unchanged(ids)

    def test_ambiguous_later_candidate_cannot_insert_earlier_new_edition(self):
        ids=self.batch_with_ambiguous_second()
        self.assertFalse(self.s.db.execute('SELECT 1 FROM games WHERE candidate_id=?',(ids[0],)).fetchone())
        self.assert_batch_conflict_unchanged(ids)
