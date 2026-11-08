"""Internal owner coordinator gate, not an import API or an authorization substitute.

Callers must verify stored owner identity and freeze source/destination through
Bohr's copy/backup/recovery primitive. Reserve BEFORE grant; commit the verified
receipt only AFTER import or rollback is complete. No TTL or token auto-release.
"""
import json
import re
from .accounts import fields
from .common import APIError,locked


class MigrationBarriers:
    @locked
    def reserve_migration(self,user,canonical,operation_id):
        self._require_library_admin(user)  # initial scope is administrator-self
        if not isinstance(operation_id,str) or not re.fullmatch('[a-f0-9]{32}',operation_id): raise APIError(400,'Invalid migration operation ID')
        if self._canonical(canonical)!=canonical: raise APIError(409,'Migration requires canonical identity')
        receipt=self.db.execute('SELECT * FROM migration_receipts WHERE operation_id=?',(operation_id,)).fetchone()
        if receipt:
            if receipt['user_id']!=user['id'] or receipt['canonical_id']!=canonical: raise APIError(409,'Migration operation identity conflict')
            return dict(operationID=operation_id,canonicalID=canonical,status='complete')
        current=self.db.execute('SELECT operation_id FROM migration_barriers WHERE user_id=? AND canonical_id=?',(user['id'],canonical)).fetchone()
        if current:
            if current[0]!=operation_id: raise APIError(409,'Another owner migration/recovery is reserved')
            return dict(operationID=operation_id,canonicalID=canonical,status='reserved')
        if self.db.execute('SELECT 1 FROM migration_barriers WHERE operation_id=?',(operation_id,)).fetchone(): raise APIError(409,'Migration operation identity conflict')
        self._own_canonical_idle(user['id'],canonical)
        self.db.execute('INSERT INTO migration_barriers VALUES (?,?,?)',(user['id'],canonical,operation_id))
        return dict(operationID=operation_id,canonicalID=canonical,status='reserved')

    @locked
    def commit_migration_receipt(self,user,canonical,operation_id,receipt):
        self._require_library_admin(user)
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='owner_migration_operations'").fetchone() and self.db.execute('SELECT 1 FROM owner_migration_operations WHERE operation_id=?',(operation_id,)).fetchone():
            raise APIError(409,'Coordinator-owned operation requires verified atomic coordinator commit')
        fields(receipt,('status','digest'))
        if receipt['status'] not in ('applied','rolled-back') or not isinstance(receipt['digest'],str) or not re.fullmatch('[a-f0-9]{64}',receipt['digest']): raise APIError(400,'Verified import/rollback receipt required')
        encoded=json.dumps(receipt,sort_keys=True)
        old=self.db.execute('SELECT * FROM migration_receipts WHERE operation_id=?',(operation_id,)).fetchone()
        if old:
            if (old['user_id'],old['canonical_id'],old['receipt'])!=(user['id'],canonical,encoded): raise APIError(409,'Migration receipt conflict')
            return {}
        reserved=self.db.execute('SELECT operation_id FROM migration_barriers WHERE user_id=? AND canonical_id=?',(user['id'],canonical)).fetchone()
        if not reserved or reserved[0]!=operation_id: raise APIError(409,'Matching owner migration reservation required')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT INTO migration_receipts VALUES (?,?,?,?)',(operation_id,user['id'],canonical,encoded))
            self.db.execute('DELETE FROM migration_barriers WHERE user_id=? AND canonical_id=? AND operation_id=?',(user['id'],canonical,operation_id))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK'); raise
        return {}
