"""Collection names must remain representable in the account-owned Mac store."""
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor
import test_unified_library
from gameshelf_server.common import APIError


class LibraryCollectionNamesTests(unittest.TestCase):
    setUp = test_unified_library.UnifiedLibraryTests.setUp
    request = test_unified_library.UnifiedLibraryTests.request
    denied = test_unified_library.UnifiedLibraryTests.denied

    def upsert(self, name, cid=None, token=None, **extra):
        return self.request('POST','/api/library/collections',dict(id=cid or str(uuid.uuid4()),name=name,**extra),token)

    def test_trim_codepoint_bounds_and_casefold_unique_per_account(self):
        cid=str(uuid.uuid4())
        first=self.upsert('  Straße  ',cid)
        self.assertEqual(first['collections'],[dict(id=cid,name='Straße')])
        self.denied(lambda:self.upsert('STRASSE'),409)
        self.denied(lambda:self.upsert(' strasse '),409)
        self.upsert('STRASSE',cid)  # excludes own ID from duplicate comparison
        self.upsert('STRASSE',token=self.bt)  # separate account namespace
        self.upsert('😀'*80)
        for name in (' ', '\u3000\t ', 'x'*81, '😀'*81, 'bad\x00name', '\ud800'):
            self.denied(lambda:self.upsert(name),400)

    def test_optional_expected_name_create_only_and_exact_match(self):
        cid=str(uuid.uuid4())
        self.upsert('One',cid,expectedName=None)
        self.denied(lambda:self.upsert('Two',cid,expectedName=None),409)
        self.denied(lambda:self.upsert('Two',cid,expectedName='one'),409)
        self.denied(lambda:self.upsert('Two',cid,expectedName='missing'),409)
        self.denied(lambda:self.upsert('Two',expectedName='One'),409)
        self.denied(lambda:self.upsert('Two',cid,expectedName=1),400)
        changed=self.upsert('Two',cid,expectedName='One')
        self.assertEqual(changed['collections'],[dict(id=cid,name='Two')])
        self.assertEqual(self.upsert('Legacy overwrite',cid)['collections'][0]['name'],'Legacy overwrite')

    def test_parallel_expected_name_updates_only_one_wins_and_conflict_does_not_mutate(self):
        cid=str(uuid.uuid4()); self.upsert('Before',cid)
        def update(name):
            try: self.upsert(name,cid,expectedName='Before'); return 200
            except APIError as error: return error.status
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(update,['A','B'])),[200,409])
        other=str(uuid.uuid4()); self.upsert('Reserved',other)
        before=list(self.s.db.iterdump())
        self.denied(lambda:self.upsert('reserved',cid),409)
        self.assertEqual(list(self.s.db.iterdump()),before)
