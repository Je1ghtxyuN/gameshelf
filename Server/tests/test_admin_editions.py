"""Read-only historical edition identity; no source scans or automatic matching."""
import json
from pathlib import PurePosixPath
import unittest
from unittest.mock import patch
import test_unified_library


class AdminEditionsTests(unittest.TestCase):
    setUp=test_unified_library.UnifiedLibraryTests.setUp
    request=test_unified_library.UnifiedLibraryTests.request
    denied=test_unified_library.UnifiedLibraryTests.denied
    publish=test_unified_library.UnifiedLibraryTests.publish

    def inventory(self,token=None):
        return self.request('GET','/api/admin/library/editions',token=token or self.at)

    def test_authentication_and_guest_cannot_read_edition_source_identities(self):
        self.denied(lambda:self.app.dispatch('GET','/api/admin/library/editions',{},''),401)
        self.denied(lambda:self.inventory(self.pt),403)
        self.denied(lambda:self.inventory(self.bt),403)
        self.assertEqual(self.inventory(),dict(editions=[]))

    def test_changed_signature_keeps_both_source_identity_records_without_writes(self):
        old=self.publish(); cid=self.s.candidates()[0]['id']
        (self.source/'Novel'/'Game.exe').write_bytes(b'MZfixture changed resource/save state')
        self.s.scan(self.root['id']); newer=self.s.publish(cid)
        self.assertNotEqual(old['id'],newer['id'])
        before=list(self.s.db.iterdump())
        legacy=self.request('GET','/api/admin/games',token=self.at)
        # The read must work without a mounted/available source and cannot
        # synchronize mappings, rescan, publish, link or infer from game titles.
        self.source.rename(self.source.with_name('source-offline'))
        with patch.object(self.s,'_external',side_effect=AssertionError('inventory must not perform source I/O')):
            result=self.inventory()
        self.assertEqual(list(self.s.db.iterdump()),before)
        self.assertEqual(self.request('GET','/api/admin/games',token=self.at),legacy)
        editions={r['id']:r for r in result['editions']}
        self.assertEqual(set(editions),{old['id'],newer['id']})
        self.assertEqual({r['canonicalID'] for r in editions.values()},{old['id'],newer['id']})
        self.assertEqual(len({r['signature'] for r in editions.values()}),2)
        for row in editions.values():
            self.assertEqual(set(row),{'id','canonicalID','candidateID','rootID','relative','signature','entries','savePaths'})
            self.assertEqual((row['candidateID'],row['rootID'],row['relative']),(cid,self.root['id'],'Novel'))
            self.assertFalse(PurePosixPath(row['relative']).is_absolute())
            for entry in row['entries']: self.assertFalse(PurePosixPath(entry['path']).is_absolute())
            for rule in row['savePaths']: self.assertFalse(PurePosixPath(rule['path']).is_absolute())
        encoded=json.dumps(result)
        self.assertNotIn(str(self.source),encoded)
        self.assertNotIn(str(self.s.state),encoded)
        self.assertNotIn(str(self.source.parent),encoded)

    def test_existing_explicit_canonical_mapping_and_save_rules_are_preserved(self):
        first=self.publish(); cid=self.s.candidates()[0]['id']
        second=self.s.publish(cid,save_paths=[dict(path='SaveA',kind='directory')])
        self.request('POST',f'/api/admin/library/games/{first["id"]}/variants',dict(variantIDs=[second['id']]),self.at)
        rows={row['id']:row for row in self.inventory()['editions']}
        self.assertEqual(set(rows),{first['id'],second['id']})
        self.assertEqual({row['canonicalID'] for row in rows.values()},{first['id']})
        self.assertEqual(rows[second['id']]['savePaths'],second['savePaths'])
        self.assertEqual(rows[first['id']]['entries'],first['entries'])
