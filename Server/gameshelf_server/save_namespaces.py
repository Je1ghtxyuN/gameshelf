"""Reviewed canonical RenPy mappings; no public import or arbitrary argv API."""
import copy
import hashlib
import json
import re
from .common import APIError,locked
from .accounts import fields
from .save_profiles import ADAPTER,RELATIVE,load_catalog,verify_variant


class SaveNamespaces:
    def initialize_save_namespaces(self):
        self.save_profiles=load_catalog(self.state)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS canonical_save_namespaces(
            canonical_id TEXT PRIMARY KEY REFERENCES games(id),profile_id TEXT NOT NULL,
            compatibility_key TEXT NOT NULL,evidence_sha256 TEXT NOT NULL,rules_sha256 TEXT NOT NULL,revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS save_namespace_variants(
            variant_id TEXT PRIMARY KEY REFERENCES games(id),canonical_id TEXT REFERENCES games(id),
            profile_variant_id TEXT NOT NULL,source_signature TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS migration_barriers(
            user_id TEXT REFERENCES users(id),canonical_id TEXT REFERENCES games(id),operation_id TEXT NOT NULL UNIQUE,
            PRIMARY KEY(user_id,canonical_id));
        CREATE TABLE IF NOT EXISTS migration_receipts(
            operation_id TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),canonical_id TEXT REFERENCES games(id),receipt TEXT NOT NULL);
        ''')
        if 'save_namespace' not in {r[1] for r in self.db.execute('PRAGMA table_info(leases)')}:
            self.db.execute("ALTER TABLE leases ADD COLUMN save_namespace TEXT NOT NULL DEFAULT ''")

    def _assert_no_migration(self,user_id,canonical):
        if self.db.execute('SELECT 1 FROM migration_barriers WHERE user_id=? AND canonical_id=?',(user_id,canonical)).fetchone():
            raise APIError(409,'Canonical save migration/recovery is reserved; wait for committed receipt')

    def _migration_edit_idle(self,canonicals):
        if any(self.db.execute('SELECT 1 FROM migration_barriers WHERE canonical_id=?',(c,)).fetchone() for c in canonicals):
            raise APIError(409,'Canonical migration must finish before identity/profile changes')

    @locked
    def reviewed_save_profiles(self,user):
        self._require_library_admin(user)
        return dict(profiles=[{k:p[k] for k in ('id','revision','adapter','compatibilityKey','synthetic','rulesSHA256','evidenceSHA256')} for p in self.save_profiles.values()])

    @locked
    def set_save_compatibility(self,user,canonical,data,*,authorize=None):
        self._require_library_admin(user); fields(data,('variantIDs','compatibilityKey','reviewedProfileID'))
        if self._canonical(canonical)!=canonical: raise APIError(409,'Target must be canonical')
        ids=data['variantIDs']
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(i,str) or not re.fullmatch('[a-f0-9]{32}',i) for i in ids) or len(set(ids))!=len(ids): raise APIError(400,'Invalid variant IDs')
        pid=data['reviewedProfileID']
        if not isinstance(pid,str) or pid not in self.save_profiles: raise APIError(404,'Trusted reviewed RenPy profile not found')
        profile=self.save_profiles[pid]
        if data['compatibilityKey']!=profile['compatibilityKey']: raise APIError(409,'Compatibility key must match reviewed save format')
        self._profile_edit_idle(canonical); plans=[]
        for edition in ids:
            if self._canonical(edition)!=canonical: raise APIError(409,'Explicitly link reviewed editions before namespace activation')
            game=self.db.execute('SELECT * FROM games WHERE id=?',(edition,)).fetchone()
            if self.db.execute('SELECT 1 FROM reviewed_launch_optins WHERE game_id=?',(edition,)).fetchone(): raise APIError(409,'Disable standalone launch profile before namespace activation')
            def match(game=game):
                lower=self._lower(game); matches=[]
                for variant in profile['variants']:
                    if any(e['path']==variant['entryPath'] and e['backend']==variant['entryBackend'] for e in json.loads(game['entries'])):
                        verify_variant(lower,variant,save_paths=json.loads(game['save_paths']),entries=json.loads(game['entries']))
                        matches.append(variant)
                if len(matches)!=1: raise APIError(409,'Reviewed RenPy source match is missing or ambiguous')
                return matches[0]
            variant=self._external(match); plans.append((game,variant))
        self._require_library_admin(user)
        if authorize: authorize()
        self._profile_edit_idle(canonical)
        for game,_ in plans:
            current=self.db.execute('SELECT * FROM games WHERE id=?',(game['id'],)).fetchone()
            if not current or dict(current)!=dict(game) or self._canonical(game['id'])!=canonical: raise APIError(409,'Edition changed during namespace review')
        existing=self.db.execute('SELECT * FROM canonical_save_namespaces WHERE canonical_id=?',(canonical,)).fetchone()
        values=(canonical,pid,profile['compatibilityKey'],profile['evidenceSHA256'],profile['rulesSHA256'],profile['revision'])
        if existing and tuple(existing)!=values: raise APIError(409,'Namespace identity/review cannot change without explicit migration')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('INSERT OR IGNORE INTO canonical_save_namespaces VALUES (?,?,?,?,?,?)',values)
            for game,variant in plans:
                old=self.db.execute('SELECT * FROM save_namespace_variants WHERE variant_id=?',(game['id'],)).fetchone()
                if old and (old['canonical_id']!=canonical or old['profile_variant_id']!=variant['id'] or old['source_signature']!=game['signature']): raise APIError(409,'Existing namespace binding requires explicit review/migration')
                self.db.execute('INSERT OR IGNORE INTO save_namespace_variants VALUES (?,?,?,?)',(game['id'],canonical,variant['id'],game['signature']))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK'); raise
        return dict(canonicalID=canonical,saveCompatibilityKey=profile['compatibilityKey'],adapter=ADAPTER,relativeSavePath=RELATIVE)

    def _namespace_for_game(self,user,game,canonical):
        stored=self.db.execute('SELECT * FROM canonical_save_namespaces WHERE canonical_id=?',(canonical,)).fetchone()
        if not stored: return None
        profile=self.save_profiles.get(stored['profile_id'])
        if not profile or profile['evidenceSHA256']!=stored['evidence_sha256'] or profile['rulesSHA256']!=stored['rules_sha256'] or profile['revision']!=stored['revision']:
            raise APIError(409,'Trusted RenPy namespace review is missing or stale')
        binding=self.db.execute('SELECT * FROM save_namespace_variants WHERE variant_id=? AND canonical_id=?',(game['id'],canonical)).fetchone()
        if not binding or binding['source_signature']!=game['signature']: raise APIError(409,'Edition lacks current reviewed namespace binding; isolated fallback forbidden')
        variant=next((v for v in profile['variants'] if v['id']==binding['profile_variant_id']),None)
        if not variant: raise APIError(409,'Namespace adapter review is missing')
        if not any(e['path']==variant['entryPath'] and e['backend']==variant['entryBackend'] for e in json.loads(game['entries'])):
            raise APIError(409,'Reviewed RenPy entry is no longer published')
        key='/'.join((self.server_id,user['id'],canonical,hashlib.sha256(profile['compatibilityKey'].encode()).hexdigest()))
        legacy=[]
        for row in self.db.execute('SELECT variant_id FROM library_variants WHERE canonical_id=? ORDER BY variant_id',(canonical,)):
            bound=self.db.execute('SELECT profile_variant_id FROM save_namespace_variants WHERE variant_id=? AND canonical_id=?',(row[0],canonical)).fetchone()
            old=next((v for v in profile['variants'] if bound and v['id']==bound[0]),None)
            legacy.append(dict(id=row[0],unknown=old is None,legacySavePaths=copy.deepcopy(old['legacySavePaths']) if old else []))
        return dict(key=key,relativePath=RELATIVE,adapter=ADAPTER,reviewedProfileID=profile['id'],revision=profile['revision'],
            rulesSHA256=profile['rulesSHA256'],evidenceSHA256=profile['evidenceSHA256'],compatibilityKey=profile['compatibilityKey'],
            serverID=self.server_id,userID=user['id'],canonicalID=canonical,variant=copy.deepcopy(variant),legacyVariants=legacy,synthetic=profile['synthetic'])

    def _namespace_public(self,context):
        return {key:context[key] for key in ('key','relativePath','adapter','reviewedProfileID','revision','rulesSHA256')}

    def _namespace_renpy_proof(self,context,game):
        variant=context['variant']; fingerprints=[]
        for item in variant['fingerprints']:
            patch=next((p for p in variant.get('patches',[]) if p['path']==item['path']),None)
            fingerprints.append(dict(path=item['path'],role=item['role'],sourceSHA256=item['sha256'],
                exportSHA256=patch['targetSHA256'] if patch else item['sha256']))
        return dict(adapter=ADAPTER,platform=variant['platform'],entryPath=variant['entryPath'],relativeSavePath=RELATIVE,
            reviewedProfileID=context['reviewedProfileID'],revision=context['revision'],rulesSHA256=context['rulesSHA256'],
            evidenceSHA256=context['evidenceSHA256'],sourceSignature=game['signature'],fingerprints=fingerprints)

    def _namespace_windows_proof(self,context,game):
        variant=context['variant']
        return dict(entryPath=variant['entryPath'],isolation='reviewed-relative-saves',saveProfileVerified=True,
            reviewedProfileID=context['reviewedProfileID'],profileRevision=context['revision'],entrySHA256=variant['entrySHA256'],
            rulesSHA256=context['rulesSHA256'],evidenceSHA256=context['evidenceSHA256'],sourceSignature=game['signature'],
            savePaths=copy.deepcopy(variant['savePaths']),synthetic=context['synthetic'],adapter=ADAPTER,relativeSavePath=RELATIVE)
