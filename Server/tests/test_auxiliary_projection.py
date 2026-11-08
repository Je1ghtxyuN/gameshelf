"""Historical helper entries stay in v1 storage but are not v2 launch choices."""
import json
import unittest
from unittest.mock import patch

import test_service


class AuxiliaryProjectionTests(unittest.TestCase):
    setUp = test_service.ServiceTests.setUp
    publish = test_service.ServiceTests.publish

    def test_legacy_catalog_retains_helpers_but_v2_preserves_only_game_entries(self):
        game = self.publish()
        self.s.grant(self.a['id'], game['id'], True)
        original = self.s.catalog(self.a)['games'][0]['entries']
        helpers = [dict(path=p, backend='wine', engine='unknown') for p in (
            'notification_helper.exe', 'Support/CHROME_CRASHPAD_HANDLER.EXE',
            'crashpad_handler.exe', 'helper.exe')]
        historical = original + helpers
        encoded = json.dumps(historical)
        self.s.db.execute('UPDATE games SET entries=? WHERE id=?', (encoded, game['id']))
        old_catalog = self.s.catalog(self.a)
        self.assertEqual(old_catalog['games'][0]['entries'], historical)
        with patch.object(self.s, '_lower', side_effect=AssertionError('projection must not inspect source')):
            library = self.s.library(self.a)
        self.assertEqual([g['id'] for g in library['games']], [game['id']])
        self.assertEqual(library['games'][0]['variants'], [dict(
            id=game['id'], platform='windows', entries=original, available=True, label='Novel')])
        self.assertEqual(self.s.catalog(self.a), old_catalog)
        self.assertEqual(self.s.db.execute('SELECT entries FROM games WHERE id=?',
                                         (game['id'],)).fetchone()[0], encoded)

    def test_historical_helper_only_edition_keeps_card_without_windows_variant(self):
        game = self.publish()
        self.s.grant(self.a['id'], game['id'], True)
        helper = dict(path='notification_helper.exe', backend='renpy', engine='renpy')
        self.s.db.execute('UPDATE games SET entries=? WHERE id=?',
                          (json.dumps([helper]), game['id']))
        card = self.s.library(self.a)['games'][0]
        self.assertEqual(card['id'], game['id'])
        self.assertEqual(card['variants'], [])
        self.assertEqual(self.s.catalog(self.a)['games'][0]['entries'], [helper])

    def test_scan_excludes_exact_helpers_without_excluding_similar_game_names(self):
        helpers = self.source / 'OnlyHelpers'
        helpers.mkdir()
        for name in ('notification_helper.exe', 'chrome_crashpad_handler.exe', 'CRASHPAD_HANDLER.EXE'):
            (helpers / name).write_bytes(b'MZsynthetic-helper')
            (self.source / 'Novel' / name).write_bytes(b'MZsynthetic-helper')
        for name in ('notification_helper_adventure.exe', 'Adventure_crashpad_handler.exe'):
            (self.source / 'Novel' / name).write_bytes(b'MZsynthetic-game')
        self.s.scan(self.root['id'])
        candidates = self.s.candidates()
        self.assertEqual([c['relative'] for c in candidates], ['Novel'])
        self.assertEqual({e['path'] for e in candidates[0]['entries']}, {
            'Game.exe', '翻译.exe', 'notification_helper_adventure.exe',
            'Adventure_crashpad_handler.exe'})
