"""Trusted local, single reviewed RenPy migration. No network or public import API.

An OS-owner manifest is authority, not a user-supplied receipt or verified flag.
Callers MUST freeze noncooperating writers. Originals/backups are never removed.
See Server/owner-migration-coordinator.md for the deliberately bounded protocol.
"""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import uuid

from .common import APIError
from .accounts import fields
from .service import clean_path, overlaps
from .save_profiles import digest, verify_variant
from .namespace_storage import prepare_namespace
from . import legacy_saves


def _fail(message):
    raise APIError(409, message)


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def _private(path, limit=65536, *, binary=False):
    path = clean_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600 or
                    before.st_size > limit):
                _fail('Owner migration file must be private, regular and gateway-owned')
            data = bytearray()
            while len(data) <= limit:
                block = os.read(fd, min(65536, limit + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
            after = os.fstat(fd)
            stable = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid,
                                s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if stable(before) != stable(after) or len(data) != before.st_size:
                _fail('Owner migration file changed while reading')
        finally:
            os.close(fd)
    except OSError:
        _fail('Owner migration private file unavailable')
    if binary:
        return bytes(data)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail('Duplicate owner migration field')
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError):
        _fail('Invalid owner migration JSON')


def _directory(path):
    path = clean_path(path)
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        _fail('Unsafe owner migration directory')
    return path


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path, value):
    temporary = path.with_name('.preparing-' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(_encoded(value)); output.flush(); os.fsync(output.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _plan(value):
    value = dict(value)
    value['limits'] = legacy_saves.Limits(**value['limits'])
    value['rules'] = tuple(tuple(v) for v in value['rules'])
    value['roots'] = tuple(tuple(v) for v in value['roots'])
    return legacy_saves.ImportPlan(**value)


class OwnerMigrationCoordinator:
    def __init__(self, service):
        self.s = service
        with service.lock:
            self._owner()
            service.db.execute('''CREATE TABLE IF NOT EXISTS owner_migration_operations(
                operation_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                canonical_id TEXT NOT NULL REFERENCES games(id), manifest TEXT NOT NULL,
                manifest_digest TEXT NOT NULL, owner_digest TEXT NOT NULL, phase TEXT NOT NULL,
                plans TEXT NOT NULL DEFAULT '{}', receipt_digest TEXT NOT NULL DEFAULT '')''')
            service.db.execute('PRAGMA synchronous=FULL')
            self.base = _directory(service.state/'owner-migrations')
            keyfile = service.state/'owner-migration-signing.key'
            if not keyfile.exists():
                if service.db.execute('SELECT 1 FROM owner_migration_operations LIMIT 1').fetchone():
                    _fail('Signing key missing; preserve migration journals for recovery')
                try:
                    fd = os.open(keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(fd, 'wb') as output:
                        output.write(secrets.token_bytes(32)); output.flush(); os.fsync(output.fileno())
                    _sync_directory(service.state)
            self.key = _private(keyfile, 32, binary=True)
            if len(self.key) != 32:
                _fail('Invalid private owner migration signing key')

    def _owner(self):
        owner = _private(self.s.state/'owner-migration-owner.json')
        fields(owner, ('serverID', 'accountID'))
        if owner['serverID'] != self.s.server_id:
            _fail('Original owner server identity mismatch')
        row = self.s.db.execute('SELECT * FROM users WHERE id=?', (owner['accountID'],)).fetchone()
        if not row or not row['admin'] or row['disabled']:
            raise APIError(403, 'Enabled original administrator required')
        return owner, self.s._user(row)

    def _row(self, operation):
        if not isinstance(operation, str) or not re.fullmatch('[a-f0-9]{32}', operation):
            _fail('Invalid stable migration operation')
        row = self.s.db.execute('SELECT * FROM owner_migration_operations WHERE operation_id=?',
                                (operation,)).fetchone()
        if not row:
            raise APIError(404, 'Owner migration operation not found')
        row = dict(row)
        owner, user = self._owner()
        if row['user_id'] != user['id'] or row['owner_digest'] != digest(owner):
            _fail('Migration original-owner binding changed')
        m = json.loads(row['manifest'])
        if digest(m) != row['manifest_digest']:
            _fail('Migration journal binding changed')
        row['selection'] = m
        row['user'] = user
        return row

    def _proof(self, m):
        owner, user = self._owner()
        names = ('schemaVersion', 'operationID', 'serverID', 'accountID', 'localID', 'selectionID',
                 'canonicalID', 'variantID', 'candidateID', 'rootID', 'relative', 'sourceSignature',
                 'reviewedProfileID', 'profileVariantID', 'evidenceSHA256', 'sourceRoot',
                 'sourceIdentity', 'mapping')
        fields(m, names)
        if type(m['schemaVersion']) is not int or m['schemaVersion'] != 1 or any(m[k] != owner[k] for k in owner):
            _fail('Migration original-owner selection mismatch')
        for name in ('operationID', 'serverID', 'accountID', 'canonicalID', 'variantID', 'candidateID', 'rootID'):
            if not isinstance(m[name], str) or not re.fullmatch('[a-f0-9]{32}', m[name]):
                _fail('Invalid migration identity')
        try:
            valid_local = str(uuid.UUID(m['localID'])).upper() == m['localID']
        except (ValueError, TypeError, AttributeError):
            valid_local = False
        if not valid_local or not isinstance(m['selectionID'], str) or not re.fullmatch('[a-f0-9]{64}', m['selectionID']):
            _fail('Invalid local migration identity')
        if self.s._canonical(m['variantID']) != m['canonicalID'] or self.s._canonical(m['canonicalID']) != m['canonicalID']:
            _fail('Edition must already belong to the exact canonical')
        game = self.s.db.execute('SELECT * FROM games WHERE id=?', (m['variantID'],)).fetchone()
        for field, column in [('candidateID', 'candidate_id'), ('rootID', 'root_id'),
                              ('relative', 'relative'), ('sourceSignature', 'signature')]:
            if m[field] != game[column]:
                _fail('Published source identity differs from owner selection')
        profile = self.s.save_profiles.get(m['reviewedProfileID'])
        if not profile or profile['evidenceSHA256'] != m['evidenceSHA256']:
            _fail('Trusted reviewed RenPy evidence required')
        variant = next((v for v in profile['variants'] if v['id'] == m['profileVariantID']), None)
        if not variant:
            _fail('Exact reviewed RenPy variant required')
        fields(m['mapping'], ('source', 'target'))
        if m['mapping']['target'] != '.' or m['mapping']['source'] not in {
                r['path'] for r in variant['legacySavePaths'] if r['kind'] == 'directory'}:
            _fail('Only an explicitly reviewed primary save mapping is supported')
        source = clean_path(m['sourceRoot'])
        info = source.stat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022 or
                m['sourceIdentity'] != dict(device=info.st_dev, inode=info.st_ino, uid=info.st_uid) or
                overlaps(source, self.s.state) or any(overlaps(source, p) for p in self.s.allowed)):
            _fail('Legacy source ownership, root identity or isolation mismatch')
        lower = self.s._lower(game)
        verify_variant(lower, variant, save_paths=json.loads(game['save_paths']), entries=json.loads(game['entries']))
        return user, game, profile, variant

    def _authority(self, row):
        if row['phase'] == 'committed':
            _fail('Committed migration cannot stage again')
        barrier = self.s.db.execute('SELECT operation_id FROM migration_barriers WHERE user_id=? AND canonical_id=?',
                                    (row['user_id'], row['canonical_id'])).fetchone()
        if not barrier or barrier[0] != row['operation_id']:
            _fail('Exact migration operation reservation required')
        if self.s.db.execute("SELECT 1 FROM leases WHERE user_id=? AND canonical_id=? AND status!='closed'",
                             (row['user_id'], row['canonical_id'])).fetchone():
            _fail('Original owner canonical writer must be closed')

    def reserve(self, manifest_path):
        m = _private(manifest_path)
        with self.s.lock:
            user, game, profile, variant = self._proof(m)
            prior = self.s.db.execute('SELECT * FROM owner_migration_operations WHERE operation_id=?',
                                      (m['operationID'],)).fetchone()
            if prior:
                row = self._row(m['operationID'])
                if row['manifest_digest'] != digest(m):
                    _fail('Stable operation cannot be rebound')
                return dict(operationID=m['operationID'], phase=row['phase'])
            self.s._profile_edit_idle(m['canonicalID'])
            if self.s.db.execute('SELECT 1 FROM migration_receipts WHERE operation_id=?', (m['operationID'],)).fetchone():
                _fail('Operation already belongs to a different completed migration')
            owner, _ = self._owner()
            self.s.db.execute('BEGIN IMMEDIATE')
            try:
                self.s.reserve_migration(user, m['canonicalID'], m['operationID'])
                self.s.db.execute('''INSERT INTO owner_migration_operations
                    (operation_id,user_id,canonical_id,manifest,manifest_digest,owner_digest,phase)
                    VALUES (?,?,?,?,?,?,?)''', (m['operationID'], user['id'], m['canonicalID'],
                    _encoded(m).decode(), digest(m), digest(owner), 'reserved'))
                self.s.db.execute('COMMIT')
            except BaseException:
                self.s.db.execute('ROLLBACK'); raise
        return dict(operationID=m['operationID'], phase='reserved')

    @contextmanager
    def _operation(self, operation):
        with self.s.lock:
            row = self._row(operation)
            folder = _directory(self.base/operation)
        fd = os.open(folder/'operation.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                _fail('Unsafe owner migration operation lock')
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield folder
        finally:
            os.close(fd)

    def _stage(self, operation):
        with self.s.lock:
            row = self._row(operation)
            self._authority(row)
            m = row['selection']
            user, game, profile, variant = self._proof(m)
            canonical = row['canonical_id']
            if self.s.db.execute("SELECT 1 FROM leases WHERE canonical_id=? AND status!='closed'", (canonical,)).fetchone():
                _fail('Close all affected canonical writers before internal profile staging')
            if self.s.db.execute('SELECT 1 FROM migration_barriers WHERE canonical_id=? AND operation_id!=?',
                                 (canonical, operation)).fetchone():
                _fail('Other account migration prevents profile staging')
            if self.s.db.execute('SELECT 1 FROM reviewed_launch_optins WHERE game_id=?', (game['id'],)).fetchone():
                _fail('Standalone launch profile cannot be silently replaced')
            values = (canonical, profile['id'], profile['compatibilityKey'], profile['evidenceSHA256'], profile['rulesSHA256'], profile['revision'])
            stored = self.s.db.execute('SELECT * FROM canonical_save_namespaces WHERE canonical_id=?', (canonical,)).fetchone()
            if stored and tuple(stored) != values:
                _fail('Existing namespace identity cannot change')
            binding = (game['id'], canonical, variant['id'], game['signature'])
            old = self.s.db.execute('SELECT * FROM save_namespace_variants WHERE variant_id=?', (game['id'],)).fetchone()
            if old and tuple(old) != binding:
                _fail('Existing reviewed variant binding cannot change')
            self.s.db.execute('BEGIN IMMEDIATE')
            try:
                self.s.db.execute('INSERT OR IGNORE INTO canonical_save_namespaces VALUES (?,?,?,?,?,?)', values)
                self.s.db.execute('INSERT OR IGNORE INTO save_namespace_variants VALUES (?,?,?,?)', binding)
                self.s.grant(user['id'], game['id'], True)
                self.s.db.execute("UPDATE owner_migration_operations SET phase='staged' WHERE operation_id=? AND phase='reserved'", (operation,))
                self.s.db.execute('COMMIT')
            except BaseException:
                self.s.db.execute('ROLLBACK'); raise
            context = self.s._namespace_for_game(user, game, canonical)
            lease = dict(user_id=user['id'], canonical_id=canonical)
            def chown_changed(path, uid, gid):
                info = path.stat(follow_symlinks=False)
                if (info.st_uid, info.st_gid) != (uid, gid):
                    getattr(self.s.driver, 'chown', os.chown)(path, uid, gid)
            data = prepare_namespace(self.s.state, lease, user, context,
                lambda edition: self.s.state/'workspaces'/user['id']/edition/'upper',
                chown=chown_changed)
            return context, data

    def _save_plan(self, operation, name, plan, phase):
        with self.s.lock:
            row = self._row(operation); self._authority(row)
            plans = json.loads(row['plans']); plans[name] = asdict(plan)
            self.s.db.execute('UPDATE owner_migration_operations SET plans=?,phase=? WHERE operation_id=?',
                              (_encoded(plans).decode(), phase, operation))

    def _phase(self, operation, phase, receipt_digest=None):
        with self.s.lock:
            row = self._row(operation); self._authority(row)
            self.s.db.execute('UPDATE owner_migration_operations SET phase=?,receipt_digest=? WHERE operation_id=?',
                              (phase, receipt_digest or row['receipt_digest'], operation))

    def _get_plan(self, operation, name):
        with self.s.lock:
            row = self._row(operation)
            value = json.loads(row['plans']).get(name)
            return _plan(value) if value else None

    def _current_inventory(self, plan):
        current = legacy_saves.plan_import(plan.source_root, plan.target_private_view,
                     [dict(path=p, kind=k) for p, k in plan.rules], plan.backup_root,
                     plan.identity, limits=plan.limits)
        if current.source_snapshot != plan.source_snapshot or current.roots != plan.roots:
            _fail('Migration source or private root changed')
        return digest(json.loads(current.target_snapshot))

    def _verify_sources(self, operation):
        clone = self._get_plan(operation, 'clone')
        self._current_inventory(clone)

    def _normalize(self, folder, manifest):
        mapped = folder/'mapped'
        if mapped.exists():
            return mapped
        temporary = _directory(folder/('mapped-preparing-' + uuid.uuid4().hex))
        # clone is private and bounded by the primitive; original source is never
        # renamed. A crash leaves this temporary tree retained for inspection.
        shutil.copytree(folder/'clone'/manifest['mapping']['source'], temporary/'data', symlinks=False)
        for directory, _, files in os.walk(temporary):
            for name in files:
                fd = os.open(Path(directory)/name, os.O_RDONLY | os.O_NOFOLLOW)
                try: os.fsync(fd)
                finally: os.close(fd)
            _sync_directory(directory)
        os.rename(temporary, mapped)
        _sync_directory(folder)
        return mapped

    def _verify_mapping(self, clone_plan, import_plan, mapping):
        prefix = mapping['source']
        def content(node):
            return {k: node[k] for k in ('kind', 'sha256', 'size') if k in node}
        expected = {'data'+p[len(prefix):]: content(node)
                    for p, node in json.loads(clone_plan.source_snapshot).items()
                    if p == prefix or p.startswith(prefix+'/')}
        actual = {p: content(node) for p, node in json.loads(import_plan.source_snapshot).items()
                  if p == 'data' or p.startswith('data/')}
        if actual != expected:
            _fail('Normalized source content differs from reviewed original mapping')

    def apply(self, operation):
        with self._operation(operation) as folder:
            with self.s.lock:
                row = self._row(operation)
                if row['phase'] == 'committed':
                    return self._verified(operation, committed=True, live=False)
            context, data = self._stage(operation)
            m = row['selection']
            clone = _directory(folder/'clone'); _directory(clone/'.gameshelf-profile')
            backup_clone = _directory(folder/'backup-clone')
            clone_plan = self._get_plan(operation, 'clone')
            if clone_plan is None:
                clone_plan = legacy_saves.plan_import(m['sourceRoot'], clone,
                    [dict(path=m['mapping']['source'], kind='directory')], backup_clone,
                    context['key'] + '/' + operation + '/clone')
                self._save_plan(operation, 'clone', clone_plan, 'staged')
            legacy_saves.apply_import(clone_plan)
            self._verify_sources(operation)
            mapped = self._normalize(folder, m)
            _directory(data.parent/'.gameshelf-profile')
            backup_import = _directory(folder/'backup-import')
            import_plan = self._get_plan(operation, 'import')
            if import_plan is None:
                import_plan = legacy_saves.plan_import(mapped, data.parent,
                    [dict(path='data', kind='directory')], backup_import, context['key'])
                self._verify_mapping(clone_plan, import_plan, m['mapping'])
                self._save_plan(operation, 'import', import_plan, 'planned')
            self._verify_mapping(clone_plan, import_plan, m['mapping'])
            legacy_saves.apply_import(import_plan)
            self._verify_sources(operation)
            target_digest = self._current_inventory(import_plan)
            payload = {k: m[k] for k in ('operationID', 'serverID', 'accountID', 'localID',
                'selectionID', 'canonicalID', 'variantID', 'candidateID', 'rootID', 'relative',
                'sourceSignature', 'reviewedProfileID', 'profileVariantID')}
            profile = self.s.save_profiles[m['reviewedProfileID']]
            payload.update(schemaVersion=1, kind='owner-save-migration', status='applied',
                profileRevision=profile['revision'], rulesSHA256=profile['rulesSHA256'],
                evidenceSHA256=profile['evidenceSHA256'],
                sourceInventorySHA256=digest(json.loads(clone_plan.source_snapshot)),
                targetInventorySHA256=target_digest, mappingSHA256=digest(m['mapping']),
                importOperationID=import_plan.operation_id, namespaceKey=context['key'])
            body = dict(payload=payload, receiptSHA256=digest(payload))
            receipt = dict(body, signature=dict(algorithm='hmac-sha256',
                keyID=hashlib.sha256(self.key).hexdigest()[:16],
                mac=hmac.new(self.key, _encoded(body), hashlib.sha256).hexdigest()))
            _write(folder/'receipt.json', receipt)
            self._phase(operation, 'applied', receipt['receiptSHA256'])
            return receipt

    def _verified(self, operation, *, committed, live=True):
        row = self._row(operation)
        if row['phase'] != ('committed' if committed else 'applied'):
            _fail('Prepared or incomplete receipt cannot authorize owner transition')
        if not committed:
            self._authority(row)
        m = row['selection']
        receipt = _private(self.base/operation/'receipt.json')
        fields(receipt, ('payload', 'receiptSHA256', 'signature'))
        body = {k: receipt[k] for k in ('payload', 'receiptSHA256')}
        expected = dict(algorithm='hmac-sha256', keyID=hashlib.sha256(self.key).hexdigest()[:16],
            mac=hmac.new(self.key, _encoded(body), hashlib.sha256).hexdigest())
        if (not hmac.compare_digest(_encoded(receipt['signature']), _encoded(expected)) or
                digest(receipt['payload']) != receipt['receiptSHA256'] or
                receipt['receiptSHA256'] != row['receipt_digest']):
            _fail('Owner migration receipt signature or journal digest mismatch')
        for key in ('operationID', 'serverID', 'accountID', 'localID', 'selectionID', 'canonicalID',
                    'variantID', 'candidateID', 'rootID', 'relative', 'sourceSignature', 'reviewedProfileID',
                    'profileVariantID', 'evidenceSHA256'):
            if receipt['payload'].get(key) != m[key]:
                _fail('Receipt source/owner/canonical/profile binding differs')
        plan = self._get_plan(operation, 'import')
        clone = self._get_plan(operation, 'clone')
        if (receipt['payload']['importOperationID'] != plan.operation_id or
                receipt['payload']['mappingSHA256'] != digest(m['mapping']) or
                receipt['payload']['sourceInventorySHA256'] != digest(json.loads(clone.source_snapshot))):
            _fail('Receipt differs from immutable reviewed import plans')
        if live:
            user, game, profile, variant = self._proof(m)
            context = self.s._namespace_for_game(user, game, m['canonicalID'])
            if context is None:
                _fail('Reviewed canonical namespace binding missing')
            expected_namespace = {k: context[k] for k in ('serverID', 'userID', 'canonicalID',
                'compatibilityKey', 'reviewedProfileID', 'revision', 'rulesSHA256', 'evidenceSHA256')}
            expected_namespace['schemaVersion'] = 1
            target = self.namespace_data(operation).parent
            if _private(target/'namespace.json') != expected_namespace:
                _fail('Actual target namespace identity differs from reviewed canonical')
            if (plan.target_private_view != str(target) or plan.identity != context['key'] or
                    receipt['payload']['namespaceKey'] != context['key']):
                _fail('Import plan is not bound to the exact canonical namespace target')
            self._verify_sources(operation)
            # Committed primitive checks are read-only. A changed save refuses
            # first activation; it never rolls back legitimate newer progress.
            result = legacy_saves.apply_import(plan)
            if result.status != 'already-applied' or self._current_inventory(plan) != receipt['payload']['targetInventorySHA256']:
                _fail('Actual canonical target inventory differs from verified import')
        elif not committed:
            _fail('Incomplete migration requires live source/target verification')
        if committed:
            stored = self.s.db.execute('SELECT * FROM migration_receipts WHERE operation_id=?', (operation,)).fetchone()
            if not stored or (stored['user_id'], stored['canonical_id'], json.loads(stored['receipt'])) != (
                    row['user_id'], row['canonical_id'], dict(status='applied', digest=receipt['receiptSHA256'])):
                _fail('Atomic committed receipt evidence missing')
        return receipt

    def commit(self, operation):
        with self._operation(operation):
            with self.s.lock:
                row = self._row(operation)
                if row['phase'] == 'committed':
                    return self._verified(operation, committed=True, live=False)
                receipt = self._verified(operation, committed=False)
                encoded = json.dumps(dict(status='applied', digest=receipt['receiptSHA256']), sort_keys=True)
                self.s.db.execute('BEGIN IMMEDIATE')
                try:
                    self.s.db.execute('INSERT INTO migration_receipts VALUES (?,?,?,?)',
                        (operation, row['user_id'], row['canonical_id'], encoded))
                    self.s.db.execute("UPDATE owner_migration_operations SET phase='committed' WHERE operation_id=?", (operation,))
                    deleted = self.s.db.execute('DELETE FROM migration_barriers WHERE user_id=? AND canonical_id=? AND operation_id=?',
                        (row['user_id'], row['canonical_id'], operation))
                    if deleted.rowcount != 1:
                        _fail('Exact reserved operation was lost')
                    self.s.db.execute('COMMIT')
                except BaseException:
                    self.s.db.execute('ROLLBACK'); raise
                return receipt

    def verify_committed_receipt(self, operation):
        with self._operation(operation):
            with self.s.lock:
                return self._verified(operation, committed=True)

    def authenticate_committed_receipt(self, operation):
        """Immutable history ONLY, for an alias already accepted under freeze.

        This authenticates a past committed operation, not current save content
        or permission to create a new alias. Never substitute it for transition.
        """
        with self._operation(operation):
            with self.s.lock:
                return self._verified(operation, committed=True, live=False)

    def verify_transition(self, operation):
        """Trusted local bridge result, never a self-asserted client flag.

        Bind these exact three fields to the caller's local expected selection;
        envelopeSHA256 hashes the exact private receipt bytes verified here.
        """
        with self._operation(operation):
            with self.s.lock:
                receipt = self._verified(operation, committed=True)
                return self._confirmation(operation, receipt)

    def verify_history_transition(self, operation):
        """Historical confirmation for already accepted aliases ONLY.

        Same result shape as initial verification, distinct authority boundary:
        authenticates committed history without live IO or primitive replay.
        Never use this to issue a permit for a previously unaccepted alias.
        """
        with self._operation(operation):
            with self.s.lock:
                receipt = self._verified(operation, committed=True, live=False)
                return self._confirmation(operation, receipt)

    @classmethod
    def read_account_history(cls, service, token, operation):
        """Authenticate and read a committed receipt without initializing migration state."""
        with service.lock:
            caller = service.authenticate(token)
            if not caller['admin']:
                raise APIError(403, 'Original administrator required')
            if not service.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='owner_migration_operations'"
            ).fetchone():
                raise APIError(404, 'Owner migration operation not found')
            # A committed operation is immutable. Do not acquire its file lock
            # under service.lock, or run the coordinator's write-side initializer.
            reader = cls.__new__(cls)
            reader.s = service
            _, owner = reader._owner()
            if caller['id'] != owner['id']:
                raise APIError(403, 'Original administrator required')
            reader.base = service.state/'owner-migrations'
            reader.key = _private(service.state/'owner-migration-signing.key', 32, binary=True)
            if len(reader.key) != 32:
                _fail('Invalid private owner migration signing key')
            receipt = reader._verified(operation, committed=True, live=False)
            if receipt['payload']['accountID'] != caller['id']:
                raise APIError(403, 'Original administrator required')
            return reader._confirmation(operation, receipt)

    def _confirmation(self, operation, receipt):
        raw = _private(self.base/operation/'receipt.json', binary=True)
        if raw != _encoded(receipt):
            _fail('Exact verified receipt envelope bytes changed')
        return dict(payload=receipt['payload'], receiptSHA256=receipt['receiptSHA256'],
                    envelopeSHA256=hashlib.sha256(raw).hexdigest())

    def status(self, operation):
        with self.s.lock:
            row = self._row(operation)
            return dict(operationID=operation, phase=row['phase'])

    def namespace_data(self, operation):
        with self.s.lock:
            row = self._row(operation); m = row['selection']
            profile = self.s.save_profiles[m['reviewedProfileID']]
            suffix = hashlib.sha256(profile['compatibilityKey'].encode()).hexdigest()
            return self.s.state/'save-namespaces'/m['serverID']/m['accountID']/m['canonicalID']/suffix/'data'
