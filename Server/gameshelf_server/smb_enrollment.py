"""Gateway state for proving a host-resolved SMB identity."""

from contextlib import contextmanager
import hashlib
import hmac
import re
import secrets
import sqlite3
import time

from .common import APIError


class SMBEnrollment:
    """Use a gateway SQLite connection in autocommit mode under its request lock."""

    def __init__(self, database, *, clock=time.time):
        self.db = database
        self.clock = clock
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS smb_enrollment_challenges (
                account TEXT PRIMARY KEY,
                challenge TEXT NOT NULL UNIQUE,
                session_digest TEXT NOT NULL,
                principal TEXT NOT NULL,
                proof_digest TEXT NOT NULL,
                expires REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS smb_enrollment_reservations (
                account TEXT PRIMARY KEY,
                principal TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS smb_enrollment_workspaces (
                account TEXT PRIMARY KEY REFERENCES smb_enrollment_reservations(account),
                share TEXT NOT NULL COLLATE NOCASE UNIQUE
            );
        """)

    @staticmethod
    def _digest(value):
        return hashlib.sha256(value.encode('utf-8')).hexdigest()

    @contextmanager
    def _transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def issue(self, account, session, principal, deliver):
        """Deliver the proof privately before exposing the challenge ID to the client.

        The host supplies the canonical principal. It must clean up expired proof
        files and must never return their contents via the control channel.
        """
        if self.reserved_identity(account) is not None:
            raise APIError(409, 'An SMB identity is already reserved for this account')
        challenge = secrets.token_hex(16)
        proof = secrets.token_hex(32)
        expiry = self.clock() + 600
        deliver(principal, challenge, proof)
        with self._transaction():
            self.db.execute('DELETE FROM smb_enrollment_challenges WHERE expires<=?', (self.clock(),))
            self.db.execute("""
                INSERT INTO smb_enrollment_challenges VALUES (?,?,?,?,?,?)
                ON CONFLICT(account) DO UPDATE SET challenge=excluded.challenge,
                session_digest=excluded.session_digest, principal=excluded.principal,
                proof_digest=excluded.proof_digest, expires=excluded.expires
            """, (account, challenge, self._digest(session), principal, self._digest(proof), expiry))
        return challenge

    def verify(self, account, session, challenge, proof):
        if not isinstance(proof, str) or not re.fullmatch('[a-f0-9]{64}', proof):
            raise APIError(403, 'Invalid SMB enrollment proof')
        with self._transaction():
            row = self.db.execute("""
                SELECT session_digest, principal, proof_digest, expires
                FROM smb_enrollment_challenges WHERE account=? AND challenge=?
            """, (account, challenge)).fetchone()
            if (row is None or row[3] <= self.clock()
                    or not hmac.compare_digest(row[0], self._digest(session))
                    or not hmac.compare_digest(row[2], self._digest(proof))):
                raise APIError(403, 'Invalid SMB enrollment proof')
            principal = row[1]
            try:
                self.db.execute('INSERT INTO smb_enrollment_reservations VALUES (?,?)', (account, principal))
            except sqlite3.IntegrityError:
                raise APIError(409, 'An SMB identity is already reserved') from None
            self.db.execute('DELETE FROM smb_enrollment_challenges WHERE account=?', (account,))
        return principal

    def reserved_identity(self, account):
        row = self.db.execute('SELECT principal FROM smb_enrollment_reservations WHERE account=?',
                              (account,)).fetchone()
        return row[0] if row is not None else None

    def ready_share(self, account):
        row = self.db.execute('SELECT share FROM smb_enrollment_workspaces WHERE account=?',
                              (account,)).fetchone()
        return row[0] if row is not None else None

    def ready_workspaces(self):
        return list(self.db.execute('SELECT account,share FROM smb_enrollment_workspaces'))

    def provision(self, account, activate):
        """Activate a reserved identity. Host activation must be idempotent."""
        ready = self.ready_share(account)
        if ready is not None:
            return ready
        principal = self.reserved_identity(account)
        if principal is None:
            raise APIError(409, 'Verify SMB identity before preparing its workspace')
        share = activate(account, principal)
        if not isinstance(share, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', share):
            raise ValueError('Host returned an invalid workspace share')
        self.db.execute('INSERT INTO smb_enrollment_workspaces VALUES (?,?)', (account, share))
        return share


class SMBEnrollmentAPI:
    """Authenticated enrollment operations backed by a trusted NAS adapter."""

    def __init__(self, enrollment, host, verification_share, *, register_share=None, existing_share=None):
        if not isinstance(verification_share, str) or not re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', verification_share
        ):
            raise ValueError('Invalid SMB verification share')
        self.enrollment = enrollment
        self.host = host
        self.verification_share = verification_share
        self.register_share = register_share
        self.existing_share = existing_share

    def status(self, account):
        share = self.enrollment.ready_share(account)
        if share is not None and self.register_share is not None:
            self.register_share(account,share)
        if share is None and self.existing_share is not None:
            share = self.existing_share(account)
        if share is not None:
            return dict(state='ready', share=share)
        return dict(state='verified' if self.enrollment.reserved_identity(account) else 'unverified')

    def dispatch(self, method, operation, data, account, token):
        if method == 'GET' and operation == '':
            return self.status(account)
        if method == 'POST' and operation == '/challenge' and set(data) == {'username'}:
            if self.status(account)['state'] != 'unverified':
                raise APIError(409, 'SMB enrollment is already verified or configured')
            username = data['username']
            if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', username):
                raise APIError(400, 'Invalid SMB username')
            try:
                principal = self.host.resolve(username)
                challenge = self.enrollment.issue(account, token, principal, self.host.deliver)
            except OSError:
                raise APIError(503, 'SMB verification is temporarily unavailable') from None
            return dict(challengeID=challenge, share=self.verification_share, filename=challenge+'.proof')
        if method == 'POST' and operation == '/proof' and set(data) == {'challengeID', 'proof'}:
            challenge = data['challengeID']
            if not isinstance(challenge, str) or not re.fullmatch('[a-f0-9]{32}', challenge):
                raise APIError(400, 'Invalid SMB enrollment challenge')
            self.enrollment.verify(account, token, challenge, data['proof'])
            try:
                self.host.discard(challenge)
            except OSError:
                # Verification is committed. Host cleanup will remove expired files.
                pass
            return self.status(account)
        if method == 'POST' and operation == '/workspace' and not data:
            current = self.status(account)
            if current['state'] == 'ready':
                return current
            try:
                self.enrollment.provision(account, self.host.provision)
            except OSError:
                raise APIError(503, 'SMB workspace is temporarily unavailable') from None
            return self.status(account)
        raise APIError(400, 'Invalid SMB enrollment request')
