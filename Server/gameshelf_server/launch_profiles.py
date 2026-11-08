"""Explicit edition opt-in to an immutable, fingerprint-bound review record."""
import json
from .common import APIError, locked
from .accounts import fields
from .reviewed_profiles import get_profile, profiles, verify_source


class LaunchProfiles:
    def initialize_launch_profiles(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS reviewed_launch_optins(
            game_id TEXT PRIMARY KEY REFERENCES games(id), profile_id TEXT NOT NULL,
            source_signature TEXT NOT NULL, rules_sha256 TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL, reviewed_by TEXT REFERENCES users(id))''')

    def _profile_edit_idle(self,canonical):
        self._migration_edit_idle([canonical])
        if self.db.execute("""SELECT 1 FROM leases WHERE status!='closed' AND
            (canonical_id=? OR game_id IN (SELECT variant_id FROM library_variants WHERE canonical_id=?))""",
            (canonical,canonical)).fetchone():
            raise APIError(409,'Close all affected canonical sessions before editing a launch profile')

    @locked
    def reviewed_launch_profiles(self,user):
        self._require_library_admin(user)
        return dict(profiles=profiles())

    @locked
    def set_launch_profile(self,user,game_id,data,*,authorize=None):
        self._require_library_admin(user)
        fields(data,('reviewedProfileID','enabled'))
        if type(data['enabled']) is not bool: raise APIError(400,'enabled must be boolean')
        profile=get_profile(data['reviewedProfileID'])
        game=self.db.execute('SELECT * FROM games WHERE id=?',(game_id,)).fetchone()
        if not game: raise APIError(404,'Game not found')
        canonical=self._canonical(game_id); self._profile_edit_idle(canonical)
        if data['enabled']:
            def verify():
                lower=self._lower(game)
                verify_source(lower,profile,json.loads(game['save_paths']),json.loads(game['entries']))
            self._external(verify)
            self._require_library_admin(user)
            if authorize: authorize()
            current=self.db.execute('SELECT * FROM games WHERE id=?',(game_id,)).fetchone()
            if not current or dict(current)!=dict(game) or self._canonical(game_id)!=canonical:
                raise APIError(409,'Published edition changed during profile review')
            self._profile_edit_idle(canonical)
            self.db.execute('''INSERT INTO reviewed_launch_optins VALUES (?,?,?,?,?,?)
                ON CONFLICT(game_id) DO UPDATE SET profile_id=excluded.profile_id,
                source_signature=excluded.source_signature,rules_sha256=excluded.rules_sha256,
                evidence_sha256=excluded.evidence_sha256,reviewed_by=excluded.reviewed_by''',
                (game_id,profile['id'],game['signature'],profile['rulesSHA256'],profile['evidenceSHA256'],user['id']))
        else:
            self.db.execute('DELETE FROM reviewed_launch_optins WHERE game_id=?',(game_id,))
        return dict(gameID=game_id,enabled=data['enabled'],reviewedProfileID=profile['id'],profileRevision=profile['revision'],
                    entryPath=profile['entryPath'],rulesSHA256=profile['rulesSHA256'],evidenceSHA256=profile['evidenceSHA256'])

    def _launch_profile_for_game(self,game):
        record=self.db.execute('SELECT * FROM reviewed_launch_optins WHERE game_id=?',(game['id'],)).fetchone()
        if not record: return None
        profile=get_profile(record['profile_id'])
        if not any(e.get('path')==profile['entryPath'] for e in json.loads(game['entries'])):
            raise APIError(409,'Reviewed entry is no longer a published launcher')
        if (record['source_signature']!=game['signature'] or record['rules_sha256']!=profile['rulesSHA256']
                or record['evidence_sha256']!=profile['evidenceSHA256']):
            raise APIError(409,'Reviewed launch profile evidence is stale; administrator review required')
        profile['sourceSignature']=game['signature']
        return profile
