import json
import os
import unittest
import zlib

from gameshelf_server.mz_save_format import decode_progress, native_progress, web_progress


def encode(value):
    return zlib.compress(json.dumps(value).encode()).decode('latin1').encode()


class MZSaveFormatTests(unittest.TestCase):
    def setUp(self):
        self.progress = {'system': {}, 'party': {}, 'map': {}, 'player': {}}
        self.files = {'file1': encode(self.progress), 'global': encode([None, {'timestamp': 1}])}

    def test_decode_preserves_contents(self):
        self.assertEqual(decode_progress(self.files)['file1'], self.progress)

    def test_invalid_or_incomplete_progress_is_rejected(self):
        for changes in (
            {'global': encode([None])},
            {'file1': encode({'party': {}})},
            {'file1': self.files['file1'] + b'extra'},
            {'file1': b'\xff'},
            {'file01': self.files['file1']},
        ):
            with self.subTest(keys=list(changes)), self.assertRaises(ValueError):
                decode_progress(self.files | changes)

    def test_round_trip_preserves_original_compressed_bytes(self):
        entries = web_progress(123456, self.files)
        self.assertEqual(set(entries), {'rmmzsave.123456.file1', 'rmmzsave.123456.global'})
        self.assertEqual(native_progress(123456, entries), self.files)

    def test_rejects_foreign_game_and_indexless_projection(self):
        entries = web_progress(123456, self.files)
        with self.assertRaises(ValueError):
            native_progress(654321, entries)
        entries.pop('rmmzsave.123456.global')
        with self.assertRaises(ValueError):
            native_progress(123456, entries)

    def test_empty_projection_does_not_create_save_records(self):
        self.assertEqual(web_progress(123456, {}), {})
        self.assertEqual(native_progress(123456, {}), {})
        with self.assertRaises(ValueError):
            web_progress(True, {})

    def test_merge_preserves_conflicting_progress_and_retries_without_duplicates(self):
        from gameshelf_server.mz_save_format import merge_progress

        later = dict(self.progress, party={'level': 17})
        incoming = {'file1': encode(later), 'global': encode([None, {'timestamp': 2}])}
        result = merge_progress(self.files, incoming)
        self.assertEqual(result['file1'], self.files['file1'])
        self.assertEqual(result['file2'], incoming['file1'])
        self.assertEqual(decode_progress(result)['global'][2]['timestamp'], 2)
        self.assertEqual(merge_progress(result, incoming), result)

    def test_merge_equivalent_progress_keeps_existing_bytes(self):
        from gameshelf_server.mz_save_format import merge_progress

        incoming = dict(self.files, file1=encode(dict(reversed(list(self.progress.items())))))
        self.assertEqual(merge_progress(self.files, incoming), self.files)

    def test_merge_keeps_an_uncontested_original_slot(self):
        from gameshelf_server.mz_save_format import merge_progress

        newer = dict(self.progress, party={'level': 17})
        second = dict(self.progress, party={'level': 7})
        incoming = {'file1': encode(newer), 'file2': encode(second),
                    'global': encode([None, {'timestamp': 2}, {'timestamp': 3}])}
        result = merge_progress(self.files, incoming)
        self.assertEqual(result['file1'], self.files['file1'])
        self.assertEqual(result['file2'], incoming['file2'])
        self.assertEqual(result['file3'], incoming['file1'])

    def test_snapshot_commit_rejects_stale_and_invalid_writes(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from gameshelf_server.mz_save_format import commit_progress_snapshot

        with TemporaryDirectory() as directory:
            path = Path(directory) / 'progress.json'
            commit_progress_snapshot(path, 123456, self.files, expected={})
            before = path.read_bytes()
            newer = dict(self.files, file1=encode(dict(self.progress, party={'level': 17})))
            with self.assertRaises(ValueError):
                commit_progress_snapshot(path, 123456, newer, expected={})
            with self.assertRaises(ValueError):
                commit_progress_snapshot(path, 123456, {}, expected=self.files)
            self.assertEqual(path.read_bytes(), before)
            commit_progress_snapshot(path, 123456, newer, expected=self.files)
            self.assertEqual(native_progress(123456, json.loads(path.read_bytes())), newer)
            commit_progress_snapshot(path, 123456, newer, expected=self.files)

    def test_snapshot_read_rejects_corruption_and_symlinks(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from gameshelf_server.mz_save_format import read_progress_snapshot

        with TemporaryDirectory() as directory:
            path = Path(directory) / 'progress.json'
            self.assertEqual(read_progress_snapshot(path, 123456), {})
            for malformed in (b'null', b'[]', b'{', b'{"foreign":"value"}'):
                path.write_bytes(malformed)
                with self.assertRaises(ValueError):
                    read_progress_snapshot(path, 123456)
                self.assertEqual(path.read_bytes(), malformed)
            link = path.with_name('link.json')
            link.symlink_to(path)
            with self.assertRaises(OSError):
                read_progress_snapshot(link, 123456)

    def test_webkit_runtime_snapshot_identity_and_projection(self):
        from base64 import b64encode
        from gameshelf_server.mz_save_format import read_webkit_progress

        def entry(key, value):
            return {'key': b64encode(key.encode()).decode(), 'value': b64encode(value.encode()).decode()}

        identity = 'a' * 64
        entries = [entry(key, value) for key, value in web_progress(123456, self.files).items()]
        entries.append(entry('rmmzsave.123456.config', 'local settings'))
        snapshot = dict(version=1, identity=identity, entries=entries)
        self.assertEqual(read_webkit_progress(json.dumps(snapshot).encode(), identity, 123456), self.files)
        with self.assertRaises(ValueError):
            read_webkit_progress(json.dumps(snapshot).encode(), 'b' * 64, 123456)
        snapshot['entries'].append(entries[0])
        with self.assertRaises(ValueError):
            read_webkit_progress(json.dumps(snapshot).encode(), identity, 123456)

    def test_windows_directory_rejects_links_and_incomplete_progress(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from gameshelf_server.mz_save_format import read_windows_progress

        with TemporaryDirectory() as directory:
            path = Path(directory) / 'save'
            self.assertEqual(read_windows_progress(path), {})
            path.mkdir()
            (path / 'config.rmmzsave').write_bytes(b'options')
            self.assertEqual(read_windows_progress(path), {})
            (path / 'file1.rmmzsave').write_bytes(self.files['file1'])
            with self.assertRaises(ValueError):
                read_windows_progress(path)
            (path / 'global.rmmzsave').write_bytes(self.files['global'])
            self.assertEqual(read_windows_progress(path), self.files)
            (path / 'file1.rmmzsave').rename(path / 'outside')
            (path / 'file1.rmmzsave').symlink_to(path / 'outside')
            with self.assertRaises(OSError):
                read_windows_progress(path)

    def test_lease_storage_projects_progress_between_private_views(self):
        import os
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from gameshelf_server.mz_lease_storage import MZLeaseStorage
        from gameshelf_server.mz_save_format import read_windows_progress

        with TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            user = dict(id='a'*32, uid=os.geteuid())
            common = dict(user_id=user['id'], canonical_id='b'*32)
            windows = dict(common, id='c'*32, game_id='d'*32)
            mac = dict(common, id='e'*32, game_id='f'*32)
            sides = {windows['game_id']: 'windows', mac['game_id']: 'web'}
            storage = MZLeaseStorage(base/'state', lambda lease, _: (sides[lease['game_id']], '1'*32, 123456),
                                     chown=lambda *_: None)
            windows_view = base/'windows'
            windows_view.mkdir()
            storage.prepare(windows, base/'source', user, windows_view)
            save = windows_view/'save'
            save.mkdir()
            for name, raw in self.files.items():
                (save/(name+'.rmmzsave')).write_bytes(raw)
            storage.commit(windows, windows_view)
            self.assertEqual(read_windows_progress(save), self.files)

            mac_view = base/'mac'
            (mac_view/'.gameshelf-profile').mkdir(parents=True)
            storage.prepare(mac, base/'source', user, mac_view)
            runtime = mac_view/'.gameshelf-profile/WebKitMZ'
            identity = json.loads((runtime/'identity.json').read_bytes())['identity']
            from gameshelf_server.mz_save_format import read_webkit_progress, webkit_progress_snapshot
            self.assertEqual(read_webkit_progress((runtime/'store.json').read_bytes(), identity, 123456), self.files)
            newer = dict(self.files, file1=encode(dict(self.progress, party={'level': 17})))
            (runtime/'store.json').write_bytes(webkit_progress_snapshot(identity, 123456, newer))
            storage.commit(mac, mac_view)
            storage.prepare(windows, base/'source', user, windows_view)
            self.assertEqual(read_windows_progress(save), newer)
            storage.commit(windows, windows_view)

    def test_gateway_requires_explicit_private_pair_review(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace
        from gameshelf_server.mz_lease_storage import load_reviewed_mz_storage

        with TemporaryDirectory() as directory:
            service = SimpleNamespace(state=Path(directory))
            self.assertIsNone(load_reviewed_mz_storage(service))
            path = service.state / 'reviewed-mz-pair.json'
            path.write_text('{}')
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, '0600'):
                load_reviewed_mz_storage(service)
            path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, 'Invalid MZ pair'):
                load_reviewed_mz_storage(service)

    def test_adoption_requires_matching_private_progress(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from gameshelf_server.mz_lease_storage import MZLeaseStorage
        from gameshelf_server.mz_save_format import commit_progress_snapshot, read_progress_snapshot, webkit_progress_snapshot

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            storage = MZLeaseStorage(root/'state', lambda *_: ('windows', 'e'*32, 123456), chown=lambda *_: None)
            variants = ('c'*32, 'd'*32)
            lease = dict(user_id='a'*32, canonical_id='b'*32)
            mac = root/'mac'
            windows = root/'windows'
            web = mac/'.gameshelf-profile/WebKitMZ'
            web.mkdir(parents=True)
            identity = '1'*64
            (web/'identity.json').write_text(json.dumps(dict(version=1, identity=identity, phase='ready')))
            web_bytes = webkit_progress_snapshot(identity, 123456, self.files)
            (web/'store.json').write_bytes(web_bytes)
            with self.assertRaisesRegex(ValueError, 'views differ'):
                storage.adopt_matching_views(lease, 123456, mac, windows, identity, variants)
            self.assertFalse(storage._root(lease).exists())
            save = windows/'save'
            save.mkdir(parents=True)
            for name, raw in self.files.items():
                (save/(name+'.rmmzsave')).write_bytes(raw)
            storage.adopt_matching_views(lease, 123456, mac, windows, identity, variants)
            self.assertEqual(read_progress_snapshot(storage._root(lease)/'progress.json',123456),self.files)
            # Mac progresses before Windows takes its first lease after adoption.
            canonical = storage._root(lease)/'progress.json'
            updated = dict(self.files, file1=encode(dict(self.progress, party={'level': 18})))
            commit_progress_snapshot(canonical, 123456, updated, expected=self.files)
            windows_lease = dict(lease, id='f'*32, game_id=variants[1])
            storage.prepare(windows_lease, None, dict(id=lease['user_id'], uid=os.getuid()), windows)
            self.assertEqual((save/'file1.rmmzsave').read_bytes(), updated['file1'])
            self.assertEqual((web/'store.json').read_bytes(),web_bytes)

    def test_interrupted_windows_projection_resumes_before_exposing_view(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from gameshelf_server.mz_lease_storage import MZLeaseStorage
        from gameshelf_server.mz_save_format import commit_progress_snapshot, read_windows_progress

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            storage = MZLeaseStorage(root/'state', lambda *_: ('windows', 'e'*32, 123456), chown=lambda *_: None)
            lease = dict(id='f'*32, user_id='a'*32, canonical_id='b'*32, game_id='d'*32)
            user = dict(id=lease['user_id'], uid=os.getuid())
            view = root/'view'
            view.mkdir()
            progress = storage._root(lease)
            progress.mkdir(parents=True, mode=0o700)
            commit_progress_snapshot(progress/'progress.json', 123456, self.files, expected={})
            publish = storage._publish

            def interrupt(path, data, owner):
                if path.name == 'global.rmmzsave':
                    raise OSError('Simulated interrupted projection')
                publish(path, data, owner)

            with patch.object(storage, '_publish', side_effect=interrupt):
                with self.assertRaisesRegex(OSError, 'Simulated interrupted projection'):
                    storage.prepare(lease, None, user, view)
            self.assertFalse((progress/f"lease-{lease['id']}.json").exists())
            self.assertEqual(set(read_windows_progress(view/'save', validate=False)), {'file1'})
            storage.prepare(lease, None, user, view)
            self.assertEqual(read_windows_progress(view/'save'), self.files)
            self.assertFalse((progress/f"{lease['game_id']}-projection.json").exists())

    def test_review_rejects_original_save_directory_without_isolation(self):
        import hashlib
        from pathlib import Path
        import sqlite3
        from contextlib import closing
        from tempfile import TemporaryDirectory
        from threading import RLock
        from types import SimpleNamespace
        from unittest.mock import patch
        from gameshelf_server.mz_lease_storage import load_reviewed_mz_storage

        with TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
            root = Path(directory).resolve()
            source = root/'source'
            (source/'data').mkdir(parents=True)
            (source/'save').mkdir()
            original = source/'save/file1.rmmzsave'
            original.write_bytes(self.files['file1'])
            payloads = {'index.html': b'<html>', 'Game.exe': b'MZ',
                        'data/System.json': b'{"advanced":{"gameId":123456}}'}
            for name, data in payloads.items():
                (source/name).write_bytes(data)
            account, canonical, web, windows, server = (c*32 for c in 'abcde')
            manifest = root/'reviewed-mz-pair.json'
            manifest.write_text(json.dumps(dict(
                accountID=account, canonicalID=canonical, webID=web, windowsID=windows, gameID=123456,
                fingerprints={name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()},
            )))
            manifest.chmod(0o600)
            db.row_factory = sqlite3.Row
            db.execute('CREATE TABLE users(id, disabled)')
            db.execute('INSERT INTO users VALUES (?, 0)', (account,))
            db.execute('CREATE TABLE games(id, root_id, relative, entries, save_paths)')
            db.execute('CREATE TABLE leases(user_id, canonical_id, status)')
            for variant, entry, backend in ((web, 'index.html', 'rpgmz'), (windows, 'Game.exe', 'wine')):
                db.execute('INSERT INTO games VALUES (?, ?, ?, ?, ?)',
                           (variant, 'root', 'source', json.dumps([dict(path=entry, backend=backend)]), '[]'))
            service = SimpleNamespace(
                state=root, db=db, lock=RLock(), server_id=server, _canonical=lambda _: canonical,
                _lower=lambda _: source,
                driver=SimpleNamespace(chown=lambda *_: None, paths=lambda lease: {'upper': root/lease['game_id']}),
            )
            with self.assertRaisesRegex(ValueError, 'source save directory is not isolated'):
                load_reviewed_mz_storage(service)
            rules = json.dumps([dict(path='save', kind='directory')])
            db.execute('UPDATE games SET save_paths=?', (rules,))
            with patch('gameshelf_server.discovery.matches_mz_web_profile', return_value=True):
                storage = load_reviewed_mz_storage(service)
                self.assertEqual(storage.edition_ids, {web, windows})
                lease = dict(user_id=account, canonical_id=canonical, game_id=windows)
                self.assertEqual(storage.reviewed_pair(lease, source), ('windows', server, 123456))
                guest = 'f'*32
                db.execute('INSERT INTO users VALUES (?, 0)', (guest,))
                owner_view, guest_view = root/'owner-view', root/'guest-view'
                for account_id, view, lease_id in ((account, owner_view, '1'*32),
                                                    (guest, guest_view, '2'*32)):
                    view.mkdir()
                    active = dict(lease, user_id=account_id, id=lease_id)
                    storage.prepare(active, source, dict(id=account_id, uid=os.getuid()), view)
                    self.assertFalse((view/'save/file1.rmmzsave').exists())
                    save = view/'save'
                    save.mkdir(exist_ok=True)
                    files = self.files if account_id == account else dict(
                        self.files, file1=encode(dict(self.progress, party={'level': 3})))
                    for name, raw in files.items():
                        (save/(name+'.rmmzsave')).write_bytes(raw)
                    storage.commit(active, view)
                active = dict(lease, id='3'*32)
                storage.prepare(active, source, dict(id=account, uid=os.getuid()), owner_view)
                self.assertEqual((owner_view/'save/file1.rmmzsave').read_bytes(), self.files['file1'])
                storage.commit(active, owner_view)
                db.execute('UPDATE games SET save_paths=? WHERE id=?', ('[]', windows))
                with self.assertRaisesRegex(ValueError, 'source save directory is not isolated'):
                    storage.reviewed_pair(lease, source)
            self.assertEqual(original.read_bytes(), self.files['file1'])

    def test_reviewed_windows_proof_requires_matching_private_executable(self):
        import hashlib
        from pathlib import Path
        import stat
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from gameshelf_server.mz_lease_storage import MZLeaseStorage, MZ_WINDOWS_PROFILE

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root/'source'
            source.mkdir()
            original = source/'Game.exe'
            original.write_bytes(b'MZ synthetic proof')
            original.chmod(0o444)
            view = root/'private'
            view.mkdir()
            executable = view/'Game.exe'
            executable.write_bytes(b'MZ changed')
            expected = hashlib.sha256(original.read_bytes()).hexdigest()
            lease = dict(id='f'*32, user_id='a'*32, canonical_id='b'*32, game_id='d'*32)
            user = dict(id=lease['user_id'], uid=os.getuid())
            storage = MZLeaseStorage(root/'state', lambda *_: ('windows', 'e'*32, 123456),
                                     windows_entry_sha256=expected)
            with self.assertRaisesRegex(ValueError, 'executable changed'):
                storage.prepare(lease, source, user, view)
            self.assertIsNone(storage.windows_launch(lease))
            executable.write_bytes(original.read_bytes())
            original_module = source/'nw.dll'
            original_module.write_bytes(b'approved runtime')
            original_module.chmod(0o444)
            module = view/'nw.dll'
            module.write_bytes(b'modified runtime')
            module.chmod(0o600)
            modules = {'nw.dll': (original_module.stat().st_size,
                                  hashlib.sha256(original_module.read_bytes()).hexdigest())}
            with patch('gameshelf_server.mz_lease_storage.MZ_WINDOWS_RUNTIME', modules):
                with self.assertRaisesRegex(ValueError, 'runtime module changed'):
                    storage.prepare(lease, source, user, view)
                self.assertIsNone(storage.windows_launch(lease))
                self.assertEqual(stat.S_IMODE(module.stat().st_mode), 0o600)
                module.write_bytes(original_module.read_bytes())
                storage.prepare(lease, source, user, view)
            self.assertEqual(stat.S_IMODE(module.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(original_module.stat().st_mode), 0o444)
            proof = storage.windows_launch(lease)
            self.assertEqual((proof['reviewedProfileID'], proof['entrySHA256']), (MZ_WINDOWS_PROFILE, expected))
            self.assertEqual(stat.S_IMODE(executable.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(original.stat().st_mode), 0o444)
            self.assertIsNone(storage.windows_launch(dict(lease, user_id='c'*32)))

    def test_explicit_web_adoption_projects_only_to_empty_windows_view(self):
        import hashlib
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from gameshelf_server.mz_lease_storage import MZLeaseStorage
        from gameshelf_server.mz_save_format import read_windows_progress, webkit_progress_snapshot

        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            mac, windows = root/'mac', root/'windows'
            profile = mac/'.gameshelf-profile/WebKitMZ'
            profile.mkdir(parents=True)
            windows.mkdir()
            identity = '1'*64
            (profile/'identity.json').write_text(json.dumps(dict(version=1, identity=identity, phase='ready')))
            raw = webkit_progress_snapshot(identity, 123456, self.files)
            store = profile/'store.json'
            store.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            lease = dict(id='f'*32, user_id='a'*32, canonical_id='b'*32, game_id='d'*32)
            user = dict(id=lease['user_id'], uid=os.getuid())
            storage = MZLeaseStorage(root/'state', lambda *_: ('windows', 'e'*32, 123456),
                                     chown=lambda *_: None)

            def adopt(expected):
                storage.adopt_reviewed_web_progress(lease, 123456, mac, windows, identity,
                                                    'c'*32, expected_store_sha256=expected)

            with self.assertRaisesRegex(ValueError, 'snapshot changed'):
                adopt('0'*64)
            self.assertFalse(storage._root(lease).exists())
            adopt(digest)
            adopt(digest)
            storage.prepare(lease, None, user, windows)
            self.assertEqual(read_windows_progress(windows/'save'), self.files)
            with self.assertRaisesRegex(ValueError, 'empty Windows view'):
                adopt(digest)
            self.assertEqual(store.read_bytes(), raw)
