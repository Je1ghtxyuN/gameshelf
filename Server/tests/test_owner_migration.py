"""Internal coordinator acceptance: temporary reviewed fixtures, never real saves."""
import importlib.util
import hashlib
import json
import os
import sqlite3
import unittest
import uuid
from unittest.mock import patch

import test_service
from namespace_support import setup_sources, NamespaceDriver, PROFILE
from gameshelf_server.service import Service
from gameshelf_server.common import APIError


class OwnerMigrationTests(unittest.TestCase):
    denied = test_service.ServiceTests.denied
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('gameshelf_server.owner_migration'),
                             'trusted owner coordinator must exist')
        from gameshelf_server.owner_migration import OwnerMigrationCoordinator
        self.Coordinator = OwnerMigrationCoordinator
        self.APIError = APIError
        test_service.ServiceTests.setUp(self)
        _, win, _ = setup_sources(self)
        old = self.s
        self.s = Service(old.state, NamespaceDriver(old.state), [self.source], clock=lambda: self.now)
        self.s.driver.chown = lambda *args: None  # temporary same-UID fixture only
        self.addCleanup(self.s.close)
        old.close()
        self.s.db.execute('UPDATE users SET uid=? WHERE id=?', (os.getuid(), self.admin['id']))
        self.admin['uid'] = os.getuid()
        self.s.register_selected(self.root['id'], dict(games=[dict(relative=win.name, entryPath='Play.exe')]),
                                 authorize=lambda: self.s._require_library_admin(self.admin))
        candidate = next(c for c in self.s.candidates() if c['relative'] == win.name)
        self.game = self.s.publish(candidate['id'])['id']
        row = self.s.db.execute('SELECT * FROM games WHERE id=?', (self.game,)).fetchone()
        self.legacy = self.source.parent / 'legacy-owner'
        (self.legacy / 'game/saves').mkdir(parents=True, mode=0o700)
        (self.legacy / 'game/saves/slot.save').write_bytes(b'old owner progress')
        self.path = self.source.parent / 'owner-manifest.json'
        st = self.legacy.stat()
        self.manifest = dict(schemaVersion=1, operationID=uuid.uuid4().hex,
            serverID=self.s.server_id, accountID=self.admin['id'], localID=str(uuid.uuid4()).upper(),
            selectionID='a'*64, canonicalID=self.game, variantID=self.game,
            candidateID=row['candidate_id'], rootID=row['root_id'], relative=row['relative'],
            sourceSignature=row['signature'], reviewedProfileID=PROFILE, profileVariantID='windows',
            evidenceSHA256=self.s.save_profiles[PROFILE]['evidenceSHA256'], sourceRoot=str(self.legacy),
            sourceIdentity=dict(device=st.st_dev, inode=st.st_ino, uid=st.st_uid),
            mapping=dict(source='game/saves', target='.'))
        binding = self.s.state / 'owner-migration-owner.json'
        binding.write_text(json.dumps(dict(serverID=self.s.server_id, accountID=self.admin['id'])))
        binding.chmod(0o600)
        self.write_manifest()
        self.c = self.Coordinator(self.s)

    def history(self, token, operation=None, method='GET'):
        from gameshelf_server.http_api import Application
        return Application(self.s).dispatch(method,
            '/api/account/owner-migrations/' + (operation or self.manifest['operationID']) + '/history',
            {}, token)

    def test_http_history_is_owner_only_and_immutable(self):
        from gameshelf_server import legacy_saves
        op = self.manifest['operationID']
        owner = self.s.login('owner', 'correct horse battery staple')['token']
        guest = self.s.login('alice', 'alice password long')['token']
        self.s.create_user('other-admin', 'another admin password', admin=True)
        other_admin = self.s.login('other-admin', 'another admin password')['token']
        self.denied(lambda: self.history(owner, 'f'*32), 404)
        self.reserve(); self.c.apply(op)
        self.denied(lambda: self.history(owner), 409)
        self.c.commit(op)
        expected = self.c.verify_history_transition(op)
        self.c.namespace_data(op).joinpath('slot.save').write_bytes(b'legitimate later progress')
        with patch.object(legacy_saves, 'apply_import', side_effect=AssertionError('no import')), \
             patch.object(self.Coordinator, '_operation', side_effect=AssertionError('no operation lock')):
            self.assertEqual(self.history(owner), expected)
        for token, code in [(None, 401), (guest, 403), (other_admin, 403)]:
            self.denied(lambda: self.history(token), code)
        self.denied(lambda: self.history(owner, method='POST'), 404)
        receipt = self.s.state/'owner-migrations'/op/'receipt.json'
        receipt.write_bytes(receipt.read_bytes() + b' ')
        self.denied(lambda: self.history(owner), 409)
        self.s.logout(owner)
        self.denied(lambda: self.history(owner), 401)
        owner = self.s.login('owner', 'correct horse battery staple')['token']
        self.s.db.execute('UPDATE users SET disabled=1 WHERE id=?', (self.admin['id'],))
        self.denied(lambda: self.history(owner), 401)

    def test_http_history_never_initializes_storage(self):
        owner = self.s.login('owner', 'correct horse battery staple')['token']
        self.s.db.execute('DROP TABLE owner_migration_operations')
        (self.s.state/'owner-migration-signing.key').unlink()
        (self.s.state/'owner-migrations').rmdir()
        self.denied(lambda: self.history(owner), 404)
        self.assertFalse((self.s.state/'owner-migration-signing.key').exists())
        self.assertFalse((self.s.state/'owner-migrations').exists())
        self.assertIsNone(self.s.db.execute("SELECT 1 FROM sqlite_master WHERE name='owner_migration_operations'").fetchone())

    def write_manifest(self):
        self.path.write_text(json.dumps(self.manifest))
        self.path.chmod(0o600)

    def reserve(self):
        return self.c.reserve(self.path)

    def blocked(self, call):
        from gameshelf_server.legacy_saves import SaveImportError
        with self.assertRaises((APIError, SaveImportError)) as error:
            call()
        if isinstance(error.exception, APIError):
            self.assertIn(error.exception.status, (400, 403, 404, 409, 503))

    def test_prepared_receipt_cannot_commit_aliases_and_atomic_server_commit(self):
        op = self.manifest['operationID']
        self.reserve()
        self.assertIsNone(self.s.db.execute('SELECT 1 FROM grants WHERE user_id=? AND game_id=?',
                                            (self.admin['id'], self.game)).fetchone())
        receipt = self.c.apply(op)
        self.assertEqual(receipt['payload']['localID'], self.manifest['localID'])
        self.assertEqual(receipt['payload']['namespaceKey'].split('/')[:3],
                         [self.s.server_id, self.admin['id'], self.game])
        self.assertNotIn(str(self.legacy), json.dumps(receipt))
        self.blocked(lambda: self.c.verify_committed_receipt(op))
        self.blocked(lambda: self.c.verify_transition(op))
        self.blocked(lambda: self.c.authenticate_committed_receipt(op))
        self.blocked(lambda: self.s.commit_migration_receipt(self.admin, self.game, op,
                     dict(status='applied', digest=receipt['receiptSHA256'])))
        self.blocked(lambda: self.s.session(self.admin, self.game, 'old', adapter='renpy-savedir-v1'))
        self.blocked(lambda: self.s.library_session(self.admin, self.game,
                     dict(variantID=self.game, deviceID='new', adapter='renpy-savedir-v1')))
        self.s.db.execute("CREATE TEMP TRIGGER fail_commit BEFORE INSERT ON migration_receipts BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.commit(op)
        self.assertIsNotNone(self.s.db.execute('SELECT 1 FROM migration_barriers WHERE operation_id=?', (op,)).fetchone())
        self.s.db.execute('DROP TRIGGER fail_commit')
        self.assertEqual(self.c.commit(op), receipt)
        self.assertEqual(self.c.verify_committed_receipt(op), receipt)
        bridge = self.c.verify_transition(op)
        self.assertEqual(set(bridge), {'payload', 'receiptSHA256', 'envelopeSHA256'})
        self.assertEqual(bridge['receiptSHA256'], receipt['receiptSHA256'])
        self.assertEqual(bridge['envelopeSHA256'], hashlib.sha256(
            (self.s.state/'owner-migrations'/op/'receipt.json').read_bytes()).hexdigest())
        self.assertEqual(self.c.commit(op), receipt)
        self.assertEqual(self.c.status(op)['phase'], 'committed')
        self.assertEqual((self.legacy/'game/saves/slot.save').read_bytes(), b'old owner progress')
        self.assertFalse(self.s.driver.active)
        self.assertEqual(self.s.db.execute('SELECT user_id FROM grants WHERE game_id=?',
                                          (self.game,)).fetchall()[0][0], self.admin['id'])

    def test_interruption_after_primitive_commit_recovers_same_plan_after_restart(self):
        from gameshelf_server import legacy_saves
        op = self.manifest['operationID']
        self.reserve()
        original = legacy_saves.apply_import
        calls = 0
        def interrupted(plan):
            nonlocal calls
            result = original(plan)
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt('simulated process termination after durable import')
            return result
        with patch.object(legacy_saves, 'apply_import', interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.c.apply(op)
        self.assertEqual(self.c.status(op)['phase'], 'planned')
        reopened = Service(self.s.state, NamespaceDriver(self.s.state), [self.source], clock=lambda: self.now)
        reopened.driver.chown = lambda *args: None
        self.addCleanup(reopened.close)
        recovery = self.Coordinator(reopened)
        self.blocked(lambda: reopened.session(self.admin, self.game, 'must remain reserved', adapter='renpy-savedir-v1'))
        recovered = recovery.apply(op)
        self.assertEqual(recovery.commit(op), recovered)
        self.assertEqual(recovery.verify_committed_receipt(op), recovered)

    def test_canonical_source_profile_and_rule_proofs_fail_before_any_grant(self):
        for key, value in [('canonicalID', 'f'*32), ('candidateID', 'f'*32),
                           ('sourceSignature', '0'*64), ('evidenceSHA256', '0'*64),
                           ('reviewedProfileID', 'unknown'), ('accountID', self.a['id']),
                           ('mapping', dict(source='unknown/saves', target='.'))]:
            with self.subTest(key=key):
                old = self.manifest[key]
                self.manifest[key] = value
                self.write_manifest()
                self.blocked(self.reserve)
                self.manifest[key] = old
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM grants').fetchone()[0], 0)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM migration_barriers').fetchone()[0], 0)

    def test_active_writer_and_rebound_operation_rejected(self):
        self.s.grant(self.admin['id'], self.game, True)
        lease = self.s.session(self.admin, self.game, 'active')
        self.blocked(self.reserve)
        self.s.release(self.admin, lease['leaseID'])
        self.reserve()
        self.manifest['localID'] = str(uuid.uuid4()).upper()
        self.write_manifest()
        self.blocked(self.reserve)
        self.blocked(lambda: self.s.set_save_compatibility(self.admin, self.game,
             dict(variantIDs=[self.game], compatibilityKey='fixture-compatible-save-v1', reviewedProfileID=PROFILE)))

    def test_receipt_tamper_and_changed_target_or_source_never_release_barrier(self):
        op = self.manifest['operationID']
        self.reserve()
        receipt = self.c.apply(op)
        path = self.s.state/'owner-migrations'/op/'receipt.json'
        original = path.read_bytes()
        receipt['payload']['localID'] = str(uuid.uuid4()).upper()
        path.write_text(json.dumps(receipt))
        self.blocked(lambda: self.c.commit(op))
        path.write_bytes(original)
        target = self.c.namespace_data(op)/'slot.save'
        target.write_bytes(b'unexpected newer progress')
        self.blocked(lambda: self.c.commit(op))
        self.assertIsNotNone(self.s.db.execute('SELECT 1 FROM migration_barriers WHERE operation_id=?', (op,)).fetchone())
        self.assertEqual(target.read_bytes(), b'unexpected newer progress')
        self.assertFalse(self.s.driver.active)

    def test_unsafe_manifest_and_unknown_nonempty_workspace_preserved(self):
        self.path.chmod(0o644)
        self.blocked(self.reserve)
        self.path.chmod(0o600)
        self.reserve()
        old = self.s.driver.upper(self.admin['id'], self.game)/'game/saves/old.save'
        old.parent.mkdir(parents=True)
        old.write_bytes(b'existing server progress')
        self.blocked(lambda: self.c.apply(self.manifest['operationID']))
        self.assertEqual(old.read_bytes(), b'existing server progress')
        self.blocked(lambda: self.c.verify_committed_receipt(self.manifest['operationID']))

    def test_writable_legacy_root_is_not_trusted_owner_source(self):
        self.legacy.chmod(0o777)
        self.blocked(self.reserve)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM migration_barriers').fetchone()[0], 0)

    def test_original_source_changed_after_import_blocks_commit(self):
        op = self.manifest['operationID']
        self.reserve(); self.c.apply(op)
        (self.legacy/'game/saves/slot.save').write_bytes(b'new external writer progress')
        self.blocked(lambda: self.c.commit(op))
        self.assertIsNotNone(self.s.db.execute('SELECT 1 FROM migration_barriers WHERE operation_id=?', (op,)).fetchone())

    def test_namespace_identity_changed_after_import_blocks_commit(self):
        op = self.manifest['operationID']
        self.reserve(); self.c.apply(op)
        file = self.c.namespace_data(op).parent/'namespace.json'
        value = json.loads(file.read_text()); value['canonicalID'] = 'f'*32
        file.write_text(json.dumps(value))
        self.blocked(lambda: self.c.commit(op))
        self.assertIsNotNone(self.s.db.execute('SELECT 1 FROM migration_barriers WHERE operation_id=?', (op,)).fetchone())

    def test_legitimate_postcommit_progress_is_preserved_and_history_auth_is_separate(self):
        op = self.manifest['operationID']
        self.reserve(); receipt = self.c.apply(op); self.c.commit(op)
        target = self.c.namespace_data(op)/'slot.save'
        target.write_bytes(b'legitimate postcommit gameplay progress')
        before = target.stat()
        self.blocked(lambda: self.c.verify_transition(op))
        self.assertEqual(target.read_bytes(), b'legitimate postcommit gameplay progress')
        self.assertEqual((target.stat().st_ino, target.stat().st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        self.assertTrue(hasattr(self.c, 'authenticate_committed_receipt'),
                        'Committed receipt history must not depend on unchanged live save bytes')
        self.assertEqual(self.c.authenticate_committed_receipt(op), receipt)
        self.assertEqual(self.c.commit(op), receipt)
        self.assertEqual(self.c.apply(op), receipt)
        self.assertEqual(target.read_bytes(), b'legitimate postcommit gameplay progress')

    def test_historical_bridge_requires_committed_authentication_without_live_import(self):
        self.assertTrue(hasattr(self.c, 'verify_history_transition'),
                        'Typed historical bridge must authenticate accepted aliases separately')
        op = self.manifest['operationID']
        self.reserve(); receipt = self.c.apply(op)
        self.blocked(lambda: self.c.verify_history_transition(op))
        self.c.commit(op)
        target = self.c.namespace_data(op)/'slot.save'
        target.write_bytes(b'legitimate later save')
        before = target.stat()
        self.blocked(lambda: self.c.verify_transition(op))
        with patch('gameshelf_server.owner_migration.legacy_saves.apply_import',
                   side_effect=AssertionError('history must never reapply an import')), \
             patch.object(self.c, '_proof', side_effect=AssertionError('history must not inspect live sources')), \
             patch.object(self.c, '_current_inventory', side_effect=AssertionError('history must not inspect live saves')):
            verified = self.c.verify_history_transition(op)
        self.assertEqual(set(verified), {'payload', 'receiptSHA256', 'envelopeSHA256'})
        self.assertEqual(verified['payload'], receipt['payload'])
        self.assertEqual(verified['receiptSHA256'], receipt['receiptSHA256'])
        file = self.s.state/'owner-migrations'/op/'receipt.json'
        original = file.read_bytes()
        self.assertEqual(verified['envelopeSHA256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(target.read_bytes(), b'legitimate later save')
        self.assertEqual((target.stat().st_ino, target.stat().st_mtime_ns), (before.st_ino, before.st_mtime_ns))
        altered = json.loads(original)
        altered['payload']['targetInventorySHA256'] = 'f'*64
        file.write_text(json.dumps(altered))
        self.blocked(lambda: self.c.verify_history_transition(op))
        file.write_bytes(original)
        self.s.db.execute("UPDATE owner_migration_operations SET phase='applied' WHERE operation_id=?", (op,))
        self.blocked(lambda: self.c.verify_history_transition(op))

    def test_interrupted_normalized_clone_cannot_substitute_unrelated_save_content(self):
        op = self.manifest['operationID']
        self.reserve()
        normalize = self.c._normalize
        def stop_after_normalizing(*args):
            result = normalize(*args)
            (result/'data/slot.save').write_bytes(b'untrusted staged replacement')
            raise KeyboardInterrupt('simulated interruption before import planning')
        with patch.object(self.c, '_normalize', stop_after_normalizing):
            with self.assertRaises(KeyboardInterrupt): self.c.apply(op)
        self.blocked(lambda: self.c.apply(op))
        self.assertFalse((self.c.namespace_data(op)/'slot.save').exists())
