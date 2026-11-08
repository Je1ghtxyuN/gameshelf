"""Internal owner migration reservation outlives tokens and gates both APIs."""
import sqlite3
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import threading
import test_unified_library
from gameshelf_server.http_api import Application
from gameshelf_server.service import Service


class MigrationBarrierTests(unittest.TestCase):
    setUp=test_unified_library.UnifiedLibraryTests.setUp
    request=test_unified_library.UnifiedLibraryTests.request
    denied=test_unified_library.UnifiedLibraryTests.denied
    publish=test_unified_library.UnifiedLibraryTests.publish

    def test_reserve_before_grant_blocks_old_and_new_until_receipt(self):
        game=self.publish()['id']; operation=uuid.uuid4().hex
        self.s.reserve_migration(self.admin,game,operation)
        self.s.grant(self.admin['id'],game,True)
        self.denied(lambda:self.s.session(self.admin,game,'old'),409)
        self.denied(lambda:self.request('POST',f'/api/library/games/{game}/session',dict(variantID=game,deviceID='new'),self.at),409)
        self.assertEqual(self.driver.active,{})
        self.s.commit_migration_receipt(self.admin,game,operation,dict(status='applied',digest='a'*64))
        self.s.session(self.admin,game,'after receipt')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM migration_receipts').fetchone()[0],1)

    def test_restart_logout_and_time_do_not_reap_barrier(self):
        game=self.publish()['id']; operation=uuid.uuid4().hex
        self.s.reserve_migration(self.admin,game,operation); self.s.grant(self.admin['id'],game,True)
        self.s.logout(self.at); self.now+=1000; self.s.reap()
        reopened=Service(self.s.state,self.driver,[self.source],clock=lambda:self.now)
        try:
            self.denied(lambda:reopened.session(self.admin,game,'must not open blank view'),409)
            self.assertEqual(reopened.reserve_migration(self.admin,game,operation)['operationID'],operation)
        finally: reopened.close()

    def test_reserved_during_source_io_blocks_open_and_receipt_failure_retains_gate(self):
        game=self.publish()['id']; self.s.grant(self.admin['id'],game,True); operation=uuid.uuid4().hex
        entered=threading.Event(); release=threading.Event(); original=self.s._lower
        def paused(row):
            lower=original(row); entered.set(); release.wait(4); return lower
        with ThreadPoolExecutor(1) as pool,patch.object(self.s,'_lower',paused):
            launch=pool.submit(self.s.session,self.admin,game,'pending')
            try:
                self.assertTrue(entered.wait(1)); self.s.reserve_migration(self.admin,game,operation)
            finally: release.set()
            self.denied(lambda:launch.result(timeout=4),409)
        self.s.db.execute("CREATE TEMP TRIGGER fail_receipt BEFORE INSERT ON migration_receipts BEGIN SELECT RAISE(ABORT,'fixture receipt failure'); END")
        try:
            with self.assertRaises(sqlite3.IntegrityError): self.s.commit_migration_receipt(self.admin,game,operation,dict(status='rolled-back',digest='b'*64))
            self.denied(lambda:self.s.session(self.admin,game,'still reserved'),409)
        finally: self.s.db.execute('DROP TRIGGER fail_receipt')
        self.assertFalse(self.s.db.in_transaction)

    def test_other_account_independent_and_active_writer_prevents_reservation(self):
        game=self.publish()['id']; operation=uuid.uuid4().hex
        self.s.reserve_migration(self.admin,game,operation)
        self.s.grant(self.a['id'],game,True); active=self.s.session(self.a,game,'other account')
        self.s.grant(self.admin['id'],game,True)
        self.denied(lambda:self.request('DELETE',f'/api/library/games/{game}',token=self.at),409)
        self.denied(lambda:self.s.reserve_migration(self.a,game,operation+'x'),403)
        self.s.commit_migration_receipt(self.admin,game,operation,dict(status='rolled-back',digest='c'*64))
        own=self.s.session(self.admin,game,'active owner')
        self.denied(lambda:self.s.reserve_migration(self.admin,game,uuid.uuid4().hex),409)
