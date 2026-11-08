"""Additive account library. Edition IDs remain the physical save workspace IDs.

No matching by title, no snapshot import, and no inferred save compatibility.
Only explicit administrator linking changes the logical grouping.
"""
import json
import re
from pathlib import PurePosixPath
from .common import APIError, locked, text
from .accounts import fields, finite, identifier, cover_response, utf8_text
from .discovery import auxiliary as is_auxiliary_executable


def entry_platform(entry,*,native_app_root=False):
    path=entry['path'].lower(); backend=entry['backend']
    if path=='.' and backend=='native' and native_app_root: return 'macos'
    if path.endswith('.exe'): return None if is_auxiliary_executable(entry['path']) else 'windows'
    if path.endswith('.app') and backend in ('native','renpy'): return 'macos'
    if path.rsplit('/',1)[-1]=='index.html' and backend in ('web','rpgmz'): return 'web'
    if path.endswith('.py') and backend=='renpy': return 'macos'
    return None


def entry_path(value,*,allow_native_root=False):
    value=text(value,4096)
    if value=='.' and allow_native_root: return value  # source/entry proof is required by caller
    path=PurePosixPath(value)
    if path.is_absolute() or value!=path.as_posix() or any(p in ('','.','..') for p in value.split('/')) or any(c in value for c in '\\:'):
        raise APIError(400,'Entry path must be confined and relative')
    return value


class UnifiedLibrary:
    def initialize_unified_library(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS library_variants(
            variant_id TEXT PRIMARY KEY REFERENCES games(id),
            canonical_id TEXT NOT NULL REFERENCES games(id));
        CREATE INDEX IF NOT EXISTS library_canonical ON library_variants(canonical_id);
        CREATE TABLE IF NOT EXISTS library_metadata(
            user_id TEXT REFERENCES users(id), canonical_id TEXT REFERENCES games(id),
            revision INTEGER NOT NULL DEFAULT 0, title TEXT, favorite INTEGER NOT NULL DEFAULT 0,
            collection_ids TEXT NOT NULL DEFAULT '[]', PRIMARY KEY(user_id,canonical_id));
        CREATE TABLE IF NOT EXISTS library_launch_preferences(
            user_id TEXT REFERENCES users(id), canonical_id TEXT REFERENCES games(id),
            platform TEXT NOT NULL, variant_id TEXT REFERENCES games(id), entry_path TEXT NOT NULL,
            PRIMARY KEY(user_id,canonical_id,platform));
        CREATE TABLE IF NOT EXISTS library_collections(
            user_id TEXT REFERENCES users(id), id TEXT, name TEXT NOT NULL, PRIMARY KEY(user_id,id));
        CREATE TABLE IF NOT EXISTS library_candidate_ignores(
            user_id TEXT REFERENCES users(id), candidate_id TEXT REFERENCES candidates(id),
            PRIMARY KEY(user_id,candidate_id));
        CREATE TABLE IF NOT EXISTS library_time_events(
            user_id TEXT REFERENCES users(id), event_id TEXT, canonical_id TEXT REFERENCES games(id),
            payload TEXT NOT NULL, elapsed REAL NOT NULL, ended REAL NOT NULL,
            PRIMARY KEY(user_id,event_id));
        ''')
        if 'edition_label' not in {r[1] for r in self.db.execute('PRAGMA table_info(games)')}:
            self.db.execute("ALTER TABLE games ADD COLUMN edition_label TEXT NOT NULL DEFAULT ''")
        for row in list(self.db.execute("SELECT * FROM games WHERE edition_label=''")):
            self.db.execute('UPDATE games SET edition_label=? WHERE id=?',(self._edition_label(row),row['id']))
        if 'canonical_id' not in {r[1] for r in self.db.execute('PRAGMA table_info(leases)')}:
            self.db.execute('ALTER TABLE leases ADD COLUMN canonical_id TEXT')
        self._sync_variants()
        self.db.execute('UPDATE leases SET canonical_id=(SELECT canonical_id FROM library_variants WHERE variant_id=game_id) WHERE canonical_id IS NULL')
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS canonical_writer ON leases(user_id,canonical_id) WHERE status!='closed'")

    def _sync_variants(self):
        self.db.execute('INSERT OR IGNORE INTO library_variants SELECT id,id FROM games')

    def _canonical(self, edition):
        if not isinstance(edition,str) or not re.fullmatch('[a-f0-9]{32}',edition):
            raise APIError(400,'Edition ID must be 32 lowercase hexadecimal characters')
        self._sync_variants()
        row=self.db.execute('SELECT canonical_id FROM library_variants WHERE variant_id=?',(edition,)).fetchone()
        if not row: raise APIError(404,'Game not found')
        return row[0]

    def _library_permitted(self,user,canonical):
        self._sync_variants()
        if not self.db.execute('''SELECT 1 FROM library_variants v JOIN grants g ON g.game_id=v.variant_id
            JOIN users u ON u.id=g.user_id WHERE v.canonical_id=? AND g.user_id=? AND u.disabled=0''',
            (canonical,user['id'])).fetchone():
            raise APIError(403,'Game not granted')

    def _library_game(self,user,canonical):
        self._library_permitted(user,canonical)
        base=self.db.execute('SELECT * FROM games WHERE id=?',(canonical,)).fetchone()
        meta=self.db.execute('SELECT * FROM library_metadata WHERE user_id=? AND canonical_id=?',(user['id'],canonical)).fetchone()
        total,last=self.db.execute('SELECT COALESCE(SUM(elapsed),0),MAX(ended) FROM library_time_events WHERE user_id=? AND canonical_id=?',(user['id'],canonical)).fetchone()
        variants=[]
        for row in self.db.execute('''SELECT games.* FROM games JOIN library_variants v ON v.variant_id=games.id
            JOIN grants g ON g.game_id=games.id WHERE v.canonical_id=? AND g.user_id=? ORDER BY games.id''',(canonical,user['id'])):
            entries=json.loads(row['entries'])
            scanned=self.db.execute('SELECT available,signature FROM candidates WHERE id=?',(row['candidate_id'],)).fetchone()
            available=bool(scanned and scanned['available'] and scanned['signature']==row['signature'])
            # One published edition can contain multiple platform entrances.
            # Repeated variant ID denotes the same immutable physical edition.
            groups={}
            for entry in entries:
                platform=entry_platform(entry,native_app_root=self._native_app_root(row))
                if platform is None: continue
                groups.setdefault(platform,[]).append(entry)
            for platform,items in groups.items():
                variant=dict(id=row['id'],platform=platform,entries=items,available=available)
                if row['edition_label']: variant['label']=row['edition_label']
                binding=self.db.execute('SELECT canonical_id,source_signature FROM save_namespace_variants WHERE variant_id=?',(row['id'],)).fetchone()
                if binding and binding['canonical_id']==canonical and binding['source_signature']==row['signature']:
                    namespace=self.db.execute('SELECT compatibility_key FROM canonical_save_namespaces WHERE canonical_id=?',(canonical,)).fetchone()
                    if namespace: variant['saveCompatibilityKey']=namespace[0]
                variants.append(variant)
        result=dict(id=canonical,title=meta['title'] if meta and meta['title'] is not None else base['title'],
                    revision=meta['revision'] if meta else 0,favorite=bool(meta['favorite']) if meta else False,
                    collectionIDs=json.loads(meta['collection_ids']) if meta else [],playtimeSeconds=total,variants=variants)
        preferences={}
        for pref in self.db.execute('SELECT platform,variant_id,entry_path FROM library_launch_preferences WHERE user_id=? AND canonical_id=?',(user['id'],canonical)):
            if any(v['id']==pref['variant_id'] and v['platform']==pref['platform'] and
                   any(e['path']==pref['entry_path'] for e in v['entries']) for v in variants):
                preferences[pref['platform']]=dict(variantID=pref['variant_id'],entryPath=pref['entry_path'])
        if preferences: result['preferredEntries']=preferences
        if last is not None: result['lastPlayed']=last
        cover=self._game(base).get('coverSHA256')
        if cover: result['coverSHA256']=cover
        return result

    def _own_canonical_idle(self,user_id,canonical):
        self._assert_no_migration(user_id,canonical)
        if self.db.execute("""SELECT 1 FROM leases WHERE user_id=? AND status!='closed'
            AND (canonical_id=? OR game_id IN
                (SELECT variant_id FROM library_variants WHERE canonical_id=?))""",
                (user_id,canonical,canonical)).fetchone():
            raise APIError(409,'Close your canonical game sessions before changing entry preferences')

    def _edition_label(self,row):
        relative=PurePosixPath(row['relative'])
        root=self.db.execute('SELECT path FROM roots WHERE id=?',(row['root_id'],)).fetchone()
        root_name=PurePosixPath(root['path']).name if root else ''
        name=relative.name if str(relative)!='.' else root_name
        if name.lower() in {'windows','win','win32','win64','pc','mac','macos','osx'}:
            name=relative.parent.name or root_name
        return name

    def _native_app_root(self,row):
        # Source identity was confined/validated at publication/registration.
        # Read stored identity only, so a temporary offline source stays visible.
        relative=PurePosixPath(row['relative'])
        if str(relative)!='.': return relative.suffix.lower()=='.app'
        root=self.db.execute('SELECT path FROM roots WHERE id=?',(row['root_id'],)).fetchone()
        return bool(root and PurePosixPath(root['path']).suffix.lower()=='.app')

    def _validate_entry_choice(self,row,lower,path,platform=None):
        path=entry_path(path,allow_native_root=True)
        entries=json.loads(row['entries']); native_root=lower.suffix.lower()=='.app'
        if path=='.' and (not native_root or not any(e['path']=='.' and e['backend']=='native' for e in entries)):
            raise APIError(400,'Dot selector requires a registered native app root')
        matching=[e for e in entries if e['path']==path and entry_platform(e,native_app_root=native_root) is not None]
        if not matching or (platform is not None and not any(entry_platform(e,native_app_root=native_root)==platform for e in matching)):
            raise APIError(409,'Selected entry is stale or does not match this platform')
        from .workflow import _canonical_relative
        actual=_canonical_relative(lower,path,directory=path=='.' or path.lower().endswith('.app'))
        if actual!=path: raise APIError(409,'Selected entry spelling changed')
        return platform or entry_platform(matching[0],native_app_root=native_root)

    def _store_launch_preference(self,user_id,canonical,platform,variant,path):
        self.db.execute("""INSERT INTO library_launch_preferences VALUES (?,?,?,?,?)
            ON CONFLICT(user_id,canonical_id,platform) DO UPDATE SET
            variant_id=excluded.variant_id,entry_path=excluded.entry_path""",(user_id,canonical,platform,variant,path))

    @locked
    def library_launch_preference(self,user,canonical,data,*,authorize=None):
        fields(data,('platform','variantID','entryPath'))
        platform=data['platform']; variant=data['variantID']; path=data['entryPath']
        if platform not in ('macos','windows','web'): raise APIError(400,'Unknown platform')
        self._library_permitted(user,canonical); self._own_canonical_idle(user['id'],canonical)
        if variant is None and path is None:
            self.db.execute('DELETE FROM library_launch_preferences WHERE user_id=? AND canonical_id=? AND platform=?',(user['id'],canonical,platform))
        else:
            path=entry_path(path,allow_native_root=True)
            if not isinstance(variant,str) or not re.fullmatch('[a-f0-9]{32}',variant): raise APIError(400,'Invalid variant ID')
            self._permitted(user,variant)
            if self._canonical(variant)!=canonical: raise APIError(403,'Variant does not belong to game')
            game=self.db.execute('SELECT * FROM games WHERE id=?',(variant,)).fetchone()
            def validate():
                self._validate_entry_choice(game,self._lower(game),path,platform)
            self._external(validate)
            if authorize: authorize()
            self._permitted(user,variant)
            self._own_canonical_idle(user['id'],canonical)
            current=self.db.execute('SELECT * FROM games WHERE id=?',(variant,)).fetchone()
            if self._canonical(variant)!=canonical or not current or dict(current)!=dict(game):
                raise APIError(409,'Edition changed during preference validation')
            self._store_launch_preference(user['id'],canonical,platform,variant,path)
        return dict(game=self._library_game(user,canonical))

    @locked
    def library_cover(self,user,canonical):
        self._library_permitted(user,canonical)
        row=self.db.execute('SELECT data FROM game_covers WHERE game_id=?',(canonical,)).fetchone()
        if not row: raise APIError(404,'Cover not found')
        return cover_response(row[0])

    def _collections(self,user):
        return [dict(r) for r in self.db.execute('SELECT id,name FROM library_collections WHERE user_id=? ORDER BY id',(user['id'],))]

    @locked
    def library(self,user):
        self._sync_variants()
        ids=[r[0] for r in self.db.execute('SELECT DISTINCT canonical_id FROM library_variants JOIN grants ON game_id=variant_id WHERE user_id=? ORDER BY canonical_id',(user['id'],))]
        return dict(schemaVersion=2,serverID=self.server_id,user={k:user[k] for k in ('id','username','admin')},
                    games=[self._library_game(user,g) for g in ids],collections=self._collections(user))

    @locked
    def library_remove(self,user,canonical,data):
        fields(data,())
        if self._canonical(canonical)!=canonical:
            raise APIError(409,'Remove requires the canonical game ID')
        self._assert_no_migration(user['id'],canonical)
        # Do not reap, revoke or touch a view here. Even expired/opening/revoking
        # writers must be closed explicitly before removing this account card.
        if self.db.execute("""SELECT 1 FROM leases WHERE user_id=? AND status!='closed'
            AND (canonical_id=? OR game_id IN
                (SELECT variant_id FROM library_variants WHERE canonical_id=?))""",
                (user['id'],canonical,canonical)).fetchone():
            raise APIError(409,'Close your game sessions before removing this game')
        # One SQL mutation: another account's grants and every private data row
        # remain intact. No-grants retries are successful for known canonical IDs.
        self.db.execute("""DELETE FROM grants WHERE user_id=? AND game_id IN
            (SELECT variant_id FROM library_variants WHERE canonical_id=?)""",(user['id'],canonical))
        return {}

    @locked
    def library_roots(self):
        return dict(roots=[dict(r) for r in self.db.execute('SELECT id,title FROM roots ORDER BY id')])

    @locked
    def library_collection(self,user,data):
        fields(data,('id','name'),('expectedName',)); cid=identifier(data['id'])
        if not isinstance(data['name'],str): raise APIError(400,'Invalid collection name')
        name=text(data['name'].strip(),80)
        utf8_text(name,320)  # 80 Unicode codepoints, including 4-byte characters
        current=self.db.execute('SELECT name FROM library_collections WHERE user_id=? AND id=?',(user['id'],cid)).fetchone()
        if 'expectedName' in data:
            expected=data['expectedName']
            if expected is not None and not isinstance(expected,str):
                raise APIError(400,'expectedName must be a string or null')
            if (expected is None and current) or (expected is not None and (not current or expected!=current['name'])):
                raise APIError(409,'Collection name conflict')
        if any(row['name'].strip().casefold()==name.casefold() for row in self.db.execute(
                'SELECT name FROM library_collections WHERE user_id=? AND id!=?',(user['id'],cid))):
            raise APIError(409,'Collection name already exists')
        self.db.execute('INSERT INTO library_collections VALUES (?,?,?) ON CONFLICT(user_id,id) DO UPDATE SET name=excluded.name',(user['id'],cid,name))
        return dict(collections=self._collections(user))

    @locked
    def library_remove_collection(self,user,collection,data):
        fields(data,()); collection=identifier(collection)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute('DELETE FROM library_collections WHERE user_id=? AND id=?',(user['id'],collection))
            for row in list(self.db.execute('SELECT canonical_id,collection_ids FROM library_metadata WHERE user_id=?',(user['id'],))):
                ids=json.loads(row['collection_ids'])
                if collection in ids:
                    self.db.execute("""UPDATE library_metadata SET collection_ids=?,revision=revision+1
                        WHERE user_id=? AND canonical_id=?""",
                        (json.dumps([i for i in ids if i!=collection]),user['id'],row['canonical_id']))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise
        return {}

    @locked
    def library_metadata(self,user,canonical,data):
        self._library_permitted(user,canonical)
        fields(data,('revision',),('favorite','title','collectionIDs'))
        if type(data['revision']) is not int or data['revision']<0: raise APIError(400,'Invalid revision')
        game=self._library_game(user,canonical)
        if data['revision']!=game['revision']: raise APIError(409,'Metadata revision conflict')
        favorite=data.get('favorite',game['favorite'])
        if type(favorite) is not bool: raise APIError(400,'favorite must be boolean')
        title=text(data['title'],1024) if 'title' in data else None
        ids=data.get('collectionIDs',game['collectionIDs'])
        if not isinstance(ids,list) or len(ids)>200: raise APIError(400,'Invalid collections')
        ids=[identifier(i) for i in ids]
        if len(ids)!=len(set(ids)) or not set(ids)<={c['id'] for c in self._collections(user)}: raise APIError(400,'Unknown or duplicate collection')
        self.db.execute('''INSERT INTO library_metadata VALUES (?,?,?,?,?,?) ON CONFLICT(user_id,canonical_id)
            DO UPDATE SET revision=excluded.revision,title=COALESCE(excluded.title,library_metadata.title),favorite=excluded.favorite,collection_ids=excluded.collection_ids''',
            (user['id'],canonical,game['revision']+1,title,int(favorite),json.dumps(ids)))
        return dict(game=self._library_game(user,canonical))

    @locked
    def library_playtime(self,user,canonical,data):
        self._library_permitted(user,canonical)
        fields(data,('eventID','deviceID','elapsedSeconds','endedAt'))
        event=identifier(data['eventID']); text(data['deviceID'],128)
        elapsed=finite(data['elapsedSeconds'],86400); ended=finite(data['endedAt'],253402300799)
        payload=json.dumps(dict(canonicalID=canonical,deviceID=data['deviceID'],elapsedSeconds=float(elapsed),endedAt=float(ended)),sort_keys=True)
        old=self.db.execute('SELECT payload FROM library_time_events WHERE user_id=? AND event_id=?',(user['id'],event)).fetchone()
        if old and old[0]!=payload: raise APIError(409,'Playtime event ID payload conflict')
        if not old:
            self.db.execute('INSERT INTO library_time_events VALUES (?,?,?,?,?,?)',(user['id'],event,canonical,payload,elapsed,ended))
        total,last=self.db.execute('SELECT SUM(elapsed),MAX(ended) FROM library_time_events WHERE user_id=? AND canonical_id=?',(user['id'],canonical)).fetchone()
        return dict(playtimeSeconds=total,lastPlayed=last)

    def _require_library_admin(self,user):
        row=self.db.execute('SELECT admin,disabled FROM users WHERE id=?',(user['id'],)).fetchone()
        if not row or not row['admin'] or row['disabled']: raise APIError(403,'Administrator required')

    @locked
    def library_editions(self,user):
        self._require_library_admin(user)
        # Historical published identity only. Never inspect current source bytes
        # or create default mappings as a side effect of this read-only listing.
        rows=self.db.execute("""SELECT g.id,COALESCE(v.canonical_id,g.id) AS canonical,
            g.candidate_id,g.root_id,g.relative,g.signature,g.entries,g.save_paths
            FROM games g LEFT JOIN library_variants v ON v.variant_id=g.id
            ORDER BY g.root_id,g.relative,g.id""")
        return dict(editions=[dict(id=r['id'],canonicalID=r['canonical'],
            candidateID=r['candidate_id'],rootID=r['root_id'],relative=r['relative'],
            signature=r['signature'],entries=json.loads(r['entries']),savePaths=json.loads(r['save_paths']))
            for r in rows])

    @locked
    def library_candidates(self,user):
        self._require_library_admin(user)
        added={r[0] for r in self.db.execute('''SELECT DISTINCT c.id FROM candidates c
            JOIN games e ON e.candidate_id=c.id AND e.signature=c.signature
            JOIN grants g ON g.game_id=e.id WHERE g.user_id=?''',(user['id'],))}
        ignored={r[0] for r in self.db.execute(
            'SELECT candidate_id FROM library_candidate_ignores WHERE user_id=?',(user['id'],))}
        return dict(candidates=[dict(c,added=c['id'] in added,ignored=c['id'] in ignored)
                               for c in self.candidates()])

    @locked
    def library_ignore_candidates(self,user,data):
        self._require_library_admin(user)
        fields(data,('candidateIDs','ignored'))
        ids=data['candidateIDs']
        if type(data['ignored']) is not bool: raise APIError(400,'ignored must be boolean')
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(i,str) or not re.fullmatch('[a-f0-9]{32}',i) for i in ids) or len(ids)!=len(set(ids)):
            raise APIError(400,'Invalid candidate IDs')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            # Validate all selectors before changing any per-account state.
            for cid in ids:
                if not self.db.execute('SELECT 1 FROM candidates WHERE id=?',(cid,)).fetchone():
                    raise APIError(404,'Candidate not found')
            for cid in ids:
                if data['ignored']:
                    self.db.execute('INSERT OR IGNORE INTO library_candidate_ignores VALUES (?,?)',(user['id'],cid))
                else:
                    self.db.execute('DELETE FROM library_candidate_ignores WHERE user_id=? AND candidate_id=?',(user['id'],cid))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise
        return self.library_candidates(user)

    @locked
    def library_link(self,user,canonical,data):
        self._require_library_admin(user); fields(data,('variantIDs',))
        ids=data['variantIDs']
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(i,str) or not re.fullmatch('[a-f0-9]{32}',i) for i in ids): raise APIError(400,'Invalid variant IDs')
        if self._canonical(canonical)!=canonical: raise APIError(409,'Target must be a canonical game')
        # Move entire explicit groups, never orphan an existing canonical game.
        sources={self._canonical(i) for i in ids}|{canonical}
        self._migration_edit_idle(sources)
        if any(self.db.execute('SELECT 1 FROM canonical_save_namespaces WHERE canonical_id=?',(c,)).fetchone() for c in sources-{canonical}):
            raise APIError(409,'Moving an existing canonical save namespace requires explicit migration')
        placeholders=','.join('?' for _ in sources)
        if self.db.execute("SELECT 1 FROM leases WHERE status!='closed' AND canonical_id IN ("+placeholders+')',tuple(sources)).fetchone():
            raise APIError(409,'Release all affected game leases before linking')
        # Conflicting account records cannot be guessed or silently discarded.
        foreign=sources-{canonical}
        for source in foreign:
            if self.db.execute('SELECT 1 FROM library_metadata WHERE canonical_id=? UNION ALL SELECT 1 FROM library_time_events WHERE canonical_id=? UNION ALL SELECT 1 FROM library_launch_preferences WHERE canonical_id=?',(source,source,source)).fetchone():
                raise APIError(409,'Variant has account metadata/time; explicit migration required before linking')
        self.db.execute('UPDATE library_variants SET canonical_id=? WHERE canonical_id IN ('+placeholders+')',(canonical,*sources))
        return dict(canonicalID=canonical,variantIDs=[r[0] for r in self.db.execute('SELECT variant_id FROM library_variants WHERE canonical_id=? ORDER BY variant_id',(canonical,))])

    @locked
    def library_add(self,user,data,*,authorize=None):
        self._require_library_admin(user); fields(data,('candidateIDs','userID'),('entryPaths',))
        ids=data['candidateIDs']; target=data['userID']
        if not isinstance(target,str) or not re.fullmatch('[a-f0-9]{32}',target): raise APIError(400,'Invalid account ID')
        if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(i,str) or not re.fullmatch('[a-f0-9]{32}',i) for i in ids) or len(ids)!=len(set(ids)): raise APIError(400,'Invalid candidate IDs')
        choices=data.get('entryPaths',{})
        if not isinstance(choices,dict) or choices.keys()-set(ids): raise APIError(400,'entryPaths must reference only this batch candidate IDs')
        choices={cid:entry_path(path,allow_native_root=True) for cid,path in choices.items()}
        account=self.db.execute('SELECT * FROM users WHERE id=? AND disabled=0',(target,)).fetchone()
        if not account: raise APIError(404,'User not found')
        # Read-only source validation may release the authority lock. Retain
        # snapshots and reject a changed selection before planning any writes.
        validated=[]; selected_platforms={}
        for cid in ids:
            row=self.db.execute('SELECT * FROM candidates WHERE id=? AND available=1',(cid,)).fetchone()
            if not row: raise APIError(404,'Candidate not found')
            def validate(row=row,cid=cid):
                lower=self._lower(row)
                if cid in choices: selected_platforms[cid]=self._validate_entry_choice(row,lower,choices[cid])
                return lower
            lower=self._external(validate)
            validated.append((row,lower))
        self._require_library_admin(user)
        if authorize: authorize()
        account=self.db.execute('SELECT * FROM users WHERE id=? AND disabled=0',(target,)).fetchone()
        if not account: raise APIError(404,'User not found')
        plans=[]
        for row,lower in validated:
            current=self.db.execute('SELECT * FROM candidates WHERE id=?',(row['id'],)).fetchone()
            if not current or dict(current)!=dict(row):
                raise APIError(409,'Candidate changed during add; retry selection')
            # Share all v1 save-rule normalization, retained-boundary and
            # edition-ambiguity checks. No earlier edition is updated if any
            # later candidate fails these predictable publication checks.
            encoded,existing=self._publication_plan(row,lower)
            if row['id'] in choices and existing:
                # Account selection may append newly validated launchers but must
                # never reorder another account's existing published entrances.
                ordered=json.loads(existing['entries'])
                ordered.extend(e for e in json.loads(row['entries']) if e not in ordered)
                row=dict(row,entries=json.dumps(ordered))
                mapped=self.db.execute('SELECT canonical_id FROM library_variants WHERE variant_id=?',(existing['id'],)).fetchone()
                self._own_canonical_idle(target,mapped[0] if mapped else existing['id'])
            plans.append((row,encoded,existing))
        # No external I/O or lock release between complete preflight and commit.
        # Publication, grants and ignored-state clearing succeed as one batch.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            editions=[self._commit_publication(*plan)['id'] for plan in plans]
            for edition in editions: self.grant(target,edition,True)
            for (row,_,_),edition in zip(plans,editions):
                if row['id'] in choices:
                    self._store_launch_preference(target,self._canonical(edition),selected_platforms[row['id']],edition,choices[row['id']])
            self.db.executemany('DELETE FROM library_candidate_ignores WHERE user_id=? AND candidate_id=?',
                                [(target,cid) for cid in ids])
            account=self._user(account)
            result=dict(games=[self._library_game(account,c) for c in sorted({self._canonical(e) for e in editions})])
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise
        return result

    @locked
    def library_session(self,user,canonical,data,*,token=None):
        fields(data,('variantID','deviceID'),('saveCompatibilityKey','adapter'))
        self._library_permitted(user,canonical)
        if self._canonical(data['variantID'])!=canonical: raise APIError(403,'Variant does not belong to game')
        if 'saveCompatibilityKey' in data:
            game=self.db.execute('SELECT * FROM games WHERE id=?',(data['variantID'],)).fetchone()
            namespace=self._namespace_for_game(user,game,canonical)
            if not namespace or data['saveCompatibilityKey']!=namespace['compatibilityKey']:
                raise APIError(409,'Cross-variant save sharing unavailable or incompatible: reviewed namespace key required')
        return self.session(user,data['variantID'],data['deviceID'],token=token,adapter=data.get('adapter'))
