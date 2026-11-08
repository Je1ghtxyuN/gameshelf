"""Frozen source-folder labels are display metadata, never identity matching."""
import unittest
import test_unified_library


class VariantLabelTests(unittest.TestCase):
    setUp=test_unified_library.UnifiedLibraryTests.setUp
    request=test_unified_library.UnifiedLibraryTests.request
    game=test_unified_library.UnifiedLibraryTests.game
    publish=test_unified_library.UnifiedLibraryTests.publish
    library=test_unified_library.UnifiedLibraryTests.library

    def test_label_is_frozen_source_display_not_personal_or_global_title(self):
        g=self.game(); cid=self.s.candidates()[0]['id']
        self.request('POST',f'/api/library/games/{g}/metadata',dict(revision=0,title='My personal title'))
        self.request('POST',f'/api/admin/games/{g}/metadata',dict(title='New default title'),self.at)
        self.s.publish(cid)
        card=self.library()['games'][0]; variant=card['variants'][0]
        self.assertEqual(card['title'],'My personal title')
        self.assertEqual(variant['label'],'Novel')
        self.assertNotIn('version',variant)
        self.assertNotIn('label',self.s.catalog(self.a)['games'][0])

    def test_platform_leaf_label_uses_single_source_display_basename(self):
        folder=self.source/'Parent v2'/'Windows'; folder.mkdir(parents=True)
        (folder/'Play.exe').write_bytes(b'MZfixture')
        self.s.scan(self.root['id']); candidate=next(c for c in self.s.candidates() if c['relative']=='Parent v2/Windows')
        g=self.s.publish(candidate['id'])['id']; self.s.grant(self.a['id'],g,True)
        label=self.library()['games'][0]['variants'][0]['label']
        self.assertEqual(label,'Parent v2'); self.assertNotIn('/',label); self.assertNotIn('\\',label)
        self.assertNotIn(str(self.source),label)

    def test_label_survives_reopen_and_offline_source(self):
        g=self.game(); self.source.rename(self.source.with_name('offline'))
        from gameshelf_server.service import Service
        reopened=Service(self.s.state,self.driver,[self.source],clock=lambda:self.now)
        try:
            self.assertEqual(reopened.library(self.a)['games'][0]['variants'][0]['label'],'Novel')
        finally: reopened.close()
