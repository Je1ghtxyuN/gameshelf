import sqlite3
import unittest

from gameshelf_server.common import APIError
from gameshelf_server.smb_enrollment import SMBEnrollment


class SMBEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:', isolation_level=None)
        self.addCleanup(self.db.close)
        self.proofs = {}
        self.now = 1000
        self.enrollment = SMBEnrollment(self.db, clock=lambda: self.now)

    def issue(self, account='account-a', session='session-a', principal='nas-a'):
        def deliver(identity, challenge, proof):
            self.proofs[challenge] = (identity, proof)
        return self.enrollment.issue(account, session, principal, deliver)

    def test_only_digest_is_stored_and_proof_is_single_use(self):
        challenge = self.issue()
        _, proof = self.proofs[challenge]
        self.assertNotIn(proof, '\n'.join(self.db.iterdump()))
        self.assertEqual(self.enrollment.verify('account-a', 'session-a', challenge, proof), 'nas-a')
        with self.assertRaises(APIError):
            self.enrollment.verify('account-a', 'session-a', challenge, proof)

    def test_invalid_context_or_expired_proof_cannot_reserve_identity(self):
        challenge = self.issue()
        _, proof = self.proofs[challenge]
        for account, session, value in [('account-b', 'session-a', proof),
                                        ('account-a', 'session-b', proof),
                                        ('account-a', 'session-a', '0'*64)]:
            with self.assertRaises(APIError):
                self.enrollment.verify(account, session, challenge, value)
        self.now += 601
        with self.assertRaises(APIError):
            self.enrollment.verify('account-a', 'session-a', challenge, proof)
        self.assertIsNone(self.enrollment.reserved_identity('account-a'))

    def test_a_verified_identity_can_only_be_reserved_by_one_account(self):
        first = self.issue()
        second = self.issue('account-b','session-b')
        self.enrollment.verify('account-a','session-a',first,self.proofs[first][1])
        with self.assertRaises(APIError):
            self.enrollment.verify('account-b','session-b',second,self.proofs[second][1])
        self.assertEqual(self.enrollment.reserved_identity('account-a'), 'nas-a')
        self.assertIsNone(self.enrollment.reserved_identity('account-b'))

    def test_failed_delivery_does_not_leave_a_verifiable_challenge(self):
        def fail(identity, challenge, proof):
            self.proofs[challenge] = (identity, proof)
            raise OSError('fixture')
        with self.assertRaises(OSError):
            self.enrollment.issue('account-a', 'session-a', 'nas-a', fail)
        challenge, (_, proof) = next(iter(self.proofs.items()))
        with self.assertRaises(APIError):
            self.enrollment.verify('account-a', 'session-a', challenge, proof)

    def test_provision_retries_reserved_identity_and_persists_ready_share(self):
        challenge = self.issue()
        self.enrollment.verify('account-a','session-a',challenge,self.proofs[challenge][1])
        self.assertIsNone(self.enrollment.ready_share('account-a'))
        calls = []

        def provision(account, principal):
            calls.append((account,principal))
            if len(calls) == 1:
                raise OSError('host temporarily unavailable')
            return 'fixture-workspace'

        with self.assertRaises(OSError):
            self.enrollment.provision('account-a',provision)
        self.assertIsNone(self.enrollment.ready_share('account-a'))
        restarted = SMBEnrollment(self.db,clock=lambda:self.now)
        self.assertEqual(restarted.provision('account-a',provision),'fixture-workspace')
        self.assertEqual(restarted.provision('account-a',provision),'fixture-workspace')
        self.assertEqual(calls,[('account-a','nas-a')]*2)

    def test_unverified_account_and_malformed_host_response_cannot_publish_share(self):
        with self.assertRaises(APIError):
            self.enrollment.provision('account-b',lambda *_:self.fail('unverified account provisioned'))
        challenge = self.issue()
        self.enrollment.verify('account-a','session-a',challenge,self.proofs[challenge][1])
        with self.assertRaises(ValueError):
            self.enrollment.provision('account-a',lambda *_:'../outside')
        self.assertIsNone(self.enrollment.ready_share('account-a'))
