"""MZ snapshot transport for an explicitly reviewed, canonical account lease."""

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from .mz_save_format import (
    commit_progress_snapshot, read_progress_snapshot, read_webkit_progress,
    read_windows_progress, webkit_progress_snapshot,
)
from .privacy import atomic_json

MZ_WINDOWS_ENTRY_SHA256 = "b2fe0f8cd0adb1d221d662e5f33b36b8245413178d8986b0d6dd3c42fc6d78de"
MZ_WINDOWS_SYSTEM_SHA256 = "3f0f873d9bced90b87578956b4a13da3ac954353b7a5317e125c8762f1291685"
MZ_WINDOWS_PROFILE = "gameshelf.mz-sakura-windows.v1"
MZ_WINDOWS_RUNTIME = {
    'd3dcompiler_47.dll': (4524696, '44422e6936dc72b7ac5ed16bb8bcae164b7554513e52efb66a3e942cec328a47'),
    'ffmpeg.dll': (1736704, '44917512b58d193b4361f2d07b723346feb7bc69f2b0ff51002b3c76f88d0f71'),
    'libEGL.dll': (386560, 'ba1098da107ae08e9f1dd4498af51693e25b35a8d8ee887f7c3939e766357604'),
    'libGLESv2.dll': (8192512, '2803555788f6895777984fac4f183267d4e88489e2bb731511f223f602e3117b'),
    'node.dll': (12323840, '5da238980ca8363592caeecb8beb5fc7d1d756738589e69e40e46e70fd953de2'),
    'nw.dll': (143216128, 'dbb533037434e1c4fef54a1e3d01b3499f652beb727864ac35eff33a03dd2a40'),
    'nw_elf.dll': (901120, 'aefb146d3578d50fde8fb889e90ee9afc904f41cf3e7c87851dc4e53e73e3d0c'),
}


def _read_file(path, limit):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= limit:
            raise ValueError('Invalid private MZ snapshot file')
        content = handle.read(info.st_size + 1)
        if len(content) != info.st_size:
            raise ValueError('Private MZ snapshot changed while reading')
        return content


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class MZLeaseStorage:
    def __init__(self, state, reviewed_pair, *, chown=os.chown, windows_entry_sha256=None, edition_ids=()):
        self.state = Path(state)
        self.reviewed_pair = reviewed_pair
        self.chown = chown
        self.windows_entry_sha256 = windows_entry_sha256
        self.edition_ids = frozenset(edition_ids)

    def _root(self, lease):
        from .service import clean_path
        from .driver import LinuxDriver

        account = LinuxDriver._id(lease['user_id'])
        canonical = LinuxDriver._id(lease['canonical_id'])
        return clean_path(self.state / 'mz-progress' / account / canonical)

    def _read_view(self, view, side, identity, game_id, *, expected_store_sha256=None):
        from .service import clean_path

        if side == 'windows':
            return read_windows_progress(clean_path(view / 'save'))
        directory = clean_path(view / '.gameshelf-profile' / 'WebKitMZ')
        if not directory.exists():
            return {}
        marker = clean_path(directory / 'identity.json')
        store = clean_path(directory / 'store.json')
        document = json.loads(_read_file(marker, 8192))
        if (document.get('version'), document.get('identity'), document.get('phase')) != (1, identity, 'ready'):
            raise ValueError('Invalid private MZ WebKit marker')
        raw = _read_file(store, 48 * 1024 * 1024)
        if expected_store_sha256 is not None and hashlib.sha256(raw).hexdigest() != expected_store_sha256:
            raise ValueError('Reviewed WebKit snapshot changed')
        return read_webkit_progress(raw, identity, game_id)

    def _publish(self, path, content, owner):
        fd, temporary = tempfile.mkstemp(prefix='.mz-progress-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                self.chown(temporary, owner, owner)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def adopt_matching_views(self, lease, game_id, web_upper, windows_upper, identity, variants):
        """Seed canonical progress only when both existing private views agree."""
        from .driver import LinuxDriver

        variant_ids = tuple(LinuxDriver._id(variant) for variant in variants)
        if len(variant_ids) != 2 or len(set(variant_ids)) != 2:
            raise ValueError('Invalid MZ adoption variants')
        root = self._root(lease)
        path = root / 'progress.json'
        if read_progress_snapshot(path, game_id):
            return
        mac = self._read_view(web_upper, 'web', identity, game_id)
        windows = self._read_view(windows_upper, 'windows', identity, game_id)
        if mac != windows:
            raise ValueError('Existing MZ views differ; explicit adoption is required')
        if mac:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            if root.stat().st_uid != os.geteuid() or stat.S_IMODE(root.stat().st_mode) != 0o700:
                raise ValueError('Invalid private MZ snapshot directory')
            # Publish canonical progress only after both private views have a baseline.
            for variant in variant_ids:
                commit_progress_snapshot(root / f'{variant}.json', game_id, mac, expected={})
            commit_progress_snapshot(path, game_id, mac, expected={})

    def adopt_reviewed_web_progress(self, lease, game_id, web_upper, windows_upper,
                                    identity, web_variant, *, expected_store_sha256):
        """Initialize a new Windows edition from an explicitly pinned WebKit export."""
        from .driver import LinuxDriver

        if (not isinstance(expected_store_sha256, str) or len(expected_store_sha256) != 64
                or any(c not in '0123456789abcdef' for c in expected_store_sha256)):
            raise ValueError('An exact reviewed WebKit snapshot digest is required')
        variant = LinuxDriver._id(web_variant)
        mac = self._read_view(web_upper, 'web', identity, game_id,
                              expected_store_sha256=expected_store_sha256)
        if not mac or self._read_view(windows_upper, 'windows', identity, game_id):
            raise ValueError('WebKit adoption requires progress and an empty Windows view')
        root = self._root(lease)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.stat().st_uid != os.geteuid() or stat.S_IMODE(root.stat().st_mode) != 0o700:
            raise ValueError('Invalid private MZ snapshot directory')
        canonical = root / 'progress.json'
        if read_progress_snapshot(canonical, game_id) not in ({}, mac):
            raise ValueError('Existing canonical MZ progress differs')
        commit_progress_snapshot(root / f'{variant}.json', game_id, mac, expected={})
        commit_progress_snapshot(canonical, game_id, mac, expected={})

    def _project_windows(self, directory, target, owner):
        from .service import clean_path

        directory.mkdir(mode=0o700, exist_ok=True)
        self.chown(directory, owner, owner)
        # Keep the global index last, after all slots have been written.
        for name in sorted(target, key=lambda name: name == 'global'):
            self._publish(clean_path(directory / (name + '.rmmzsave')), target[name], owner)
        if read_windows_progress(directory) != target:
            raise ValueError('MZ Windows projection verification failed')
        _sync_directory(directory)
        _sync_directory(directory.parent)

    def prepare(self, lease, lower, user, view):
        """Run before SMB publication under the service's canonical writer lock."""
        review = self.reviewed_pair(lease, lower)
        if review is None:
            return
        from .service import clean_path
        from .driver import LinuxDriver

        side, server_id, game_id = review
        if side not in ('web', 'windows') or user['id'] != lease['user_id']:
            raise ValueError('Invalid MZ lease selection')
        variant = LinuxDriver._id(lease['game_id'])
        lease_id = LinuxDriver._id(lease['id'])
        parts = (LinuxDriver._id(server_id), lease['user_id'], variant)
        identity = hashlib.sha256(''.join(f'{len(part)}:{part}' for part in parts).encode()).hexdigest()
        root = self._root(lease)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.stat().st_uid != os.geteuid() or stat.S_IMODE(root.stat().st_mode) != 0o700:
            raise ValueError('Invalid private MZ snapshot directory')
        canonical = read_progress_snapshot(root / 'progress.json', game_id)
        baseline_path = root / f'{variant}.json'
        baseline = read_progress_snapshot(baseline_path, game_id)
        pending_path = root / f'{variant}-projection.json'
        if side == 'windows':
            directory = clean_path(view / 'save')
            pending = read_progress_snapshot(pending_path, game_id)
            if pending:
                partial = read_windows_progress(directory, validate=False)
                if (baseline.keys() - partial.keys() or partial.keys() - pending.keys()
                        or any(data not in (baseline.get(name), pending.get(name))
                               for name, data in partial.items())):
                    raise ValueError('Interrupted MZ projection contains unrecognized progress')
                self._project_windows(directory, pending, user['uid'])
                commit_progress_snapshot(baseline_path, game_id, pending, expected=baseline)
                pending_path.unlink()
                _sync_directory(root)
                baseline = pending
        existing = self._read_view(view, side, identity, game_id)
        if existing != canonical and existing != baseline:
            raise ValueError('Existing MZ progress requires explicit adoption')
        if existing != canonical:
            if existing.keys() - canonical.keys():
                raise ValueError('MZ projection would remove existing progress')
            if side == 'windows':
                commit_progress_snapshot(pending_path, game_id, canonical, expected={})
                self._project_windows(directory, canonical, user['uid'])
                commit_progress_snapshot(baseline_path, game_id, canonical, expected=baseline)
                pending_path.unlink()
                _sync_directory(root)
            else:
                directory = clean_path(view / '.gameshelf-profile' / 'WebKitMZ')
                directory.mkdir(mode=0o700, exist_ok=True)
                self.chown(directory, user['uid'], user['uid'])
                # Existing WebKit stores may contain settings. Retain those bytes.
                store = clean_path(directory / 'store.json')
                prior = {'entries': []}
                marker = clean_path(directory / 'identity.json')
                if store.exists():
                    prior = json.loads(_read_file(store, 48 * 1024 * 1024))
                progress = json.loads(webkit_progress_snapshot(identity, game_id, canonical))
                from base64 import b64decode
                settings = {'rmmzsave.test', f'rmmzsave.{game_id}.config', f'rmmzsave.{game_id}.titleInfo'}
                progress['entries'] += [entry for entry in prior['entries'] if b64decode(entry['key']).decode() in settings]
                self._publish(store, json.dumps(progress).encode(), user['uid'])
                if not marker.exists():
                    marker_bytes = json.dumps(dict(version=1, identity=identity, phase='ready')).encode()
                    self._publish(marker, marker_bytes, user['uid'])
            if self._read_view(view, side, identity, game_id) != canonical:
                raise ValueError('MZ projection verification failed')
        context = dict(side=side, identity=identity, gameID=game_id, variant=variant)
        if side == 'windows' and self.windows_entry_sha256:
            executable = clean_path(view / 'Game.exe')
            if hashlib.sha256(_read_file(executable, 4 * 1024 * 1024)).hexdigest() != self.windows_entry_sha256:
                raise ValueError('Reviewed MZ Windows executable changed in private view')
            # Samba needs execute access for Windows image loads, including DLLs.
            # Validate all reviewed files before changing only the private overlay.
            runtime = []
            for name, (size, digest) in MZ_WINDOWS_RUNTIME.items():
                module = clean_path(view / name)
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                try:
                    with os.fdopen(os.open(module, flags), 'rb') as handle:
                        info = os.fstat(handle.fileno())
                        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                                or info.st_size != size or hashlib.file_digest(handle, 'sha256').hexdigest() != digest):
                            raise ValueError('Reviewed MZ Windows runtime module changed')
                except OSError as error:
                    raise ValueError('Reviewed MZ Windows runtime module unavailable') from error
                runtime.append(module)
            for image in (executable, *runtime):
                os.chmod(image, 0o700)
            context['entrySHA256'] = self.windows_entry_sha256
        atomic_json(root / f'lease-{lease_id}.json', context)

    def windows_launch(self, lease):
        from .driver import LinuxDriver
        from .service import clean_path

        if not self.windows_entry_sha256:
            return None
        receipt = clean_path(self._root(lease) / f"lease-{LinuxDriver._id(lease['id'])}.json")
        try:
            context = json.loads(_read_file(receipt, 8192))
        except FileNotFoundError:
            return None
        if context['side'] != 'windows':
            return None
        if (context['variant'] != lease['game_id']
                or context.get('entrySHA256') != self.windows_entry_sha256):
            raise ValueError('Invalid reviewed MZ Windows receipt')
        return dict(entryPath='Game.exe', isolation='reviewed-relative-saves', saveProfileVerified=True,
                    reviewedProfileID=MZ_WINDOWS_PROFILE, profileRevision=1,
                    entrySHA256=self.windows_entry_sha256,
                    savePaths=[dict(path='save', kind='directory')], synthetic=False)

    def commit(self, lease, upper):
        """Read the account's upper layer only after the SMB view has closed."""
        from .service import clean_path
        from .driver import LinuxDriver

        if 'canonical_id' not in lease:
            return
        root = self._root(lease)
        receipt = clean_path(root / f"lease-{LinuxDriver._id(lease['id'])}.json")
        if not receipt.exists():
            return
        context = json.loads(_read_file(receipt, 8192))
        variant = LinuxDriver._id(lease['game_id'])
        if context['variant'] != variant or context['side'] not in ('windows', 'web'):
            raise ValueError('MZ close receipt does not match lease')
        game_id = context['gameID']
        current = read_progress_snapshot(root / 'progress.json', game_id)
        files = self._read_view(upper, context['side'], context['identity'], game_id)
        commit_progress_snapshot(root / 'progress.json', game_id, files, expected=current)
        baseline = root / f'{variant}.json'
        commit_progress_snapshot(baseline, game_id, files, expected=read_progress_snapshot(baseline, game_id))
        receipt.unlink()


def load_reviewed_mz_storage(service):
    """Load a private edition review with a designated legacy migration account."""
    from .discovery import matches_mz_web_profile
    from .driver import LinuxDriver
    from .privacy import normalize_rules

    manifest = service.state / 'reviewed-mz-pair.json'
    try:
        raw = _read_file(manifest, 16384)
    except FileNotFoundError:
        return None
    info = manifest.lstat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('MZ pair review requires a gateway-owned 0600 file')
    review = json.loads(raw)
    keys = {'accountID', 'canonicalID', 'webID', 'windowsID', 'gameID', 'fingerprints'}
    if not isinstance(review, dict) or set(review) != keys:
        raise ValueError('Invalid MZ pair review')
    for key in ('accountID', 'canonicalID', 'webID', 'windowsID'):
        LinuxDriver._id(review[key])
    game_id = review['gameID']
    if type(game_id) is not int or not 0 < game_id <= 2**53 - 1:
        raise ValueError('Invalid reviewed MZ game identity')
    fingerprints = review['fingerprints']
    filenames = {'index.html', 'Game.exe', 'data/System.json'}
    if not isinstance(fingerprints, dict) or set(fingerprints) != filenames or any(
        not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value)
        for value in fingerprints.values()
    ):
        raise ValueError('Invalid MZ pair fingerprints')
    if review['webID'] == review['windowsID']:
        raise ValueError('MZ pair requires distinct editions')
    sides = {
        review['webID']: ('web', 'index.html', 'rpgmz'),
        review['windowsID']: ('windows', 'Game.exe', 'wine'),
    }

    def verify_pair(lower=None):
        if not service.db.execute('SELECT 1 FROM users WHERE id=? AND disabled=0', (review['accountID'],)).fetchone():
            raise ValueError('Reviewed MZ account is unavailable')
        sources = set()
        rules_by_variant = []
        for variant, (_, entry, backend) in sides.items():
            row = service.db.execute('SELECT * FROM games WHERE id=?', (variant,)).fetchone()
            if row is None or service._canonical(variant) != review['canonicalID']:
                raise ValueError('Reviewed MZ edition is no longer paired')
            entries = json.loads(row['entries'])
            if len(entries) != 1 or (entries[0]['path'], entries[0]['backend']) != (entry, backend):
                raise ValueError('Reviewed MZ edition entrance changed')
            sources.add((row['root_id'], row['relative']))
            rules_by_variant.append(json.loads(row['save_paths']))
        if len(sources) != 1:
            raise ValueError('MZ editions must use the same source')
        if lower is None:
            lower = service._lower(row)
        save = lower / 'save'
        if save.exists() or save.is_symlink():
            for rules in rules_by_variant:
                if dict(path='save', kind='directory') not in normalize_rules(rules, lower):
                    raise ValueError('Reviewed MZ source save directory is not isolated')

    verify_pair()

    def reviewed_pair(lease, lower):
        if lease.get('canonical_id') != review['canonicalID']:
            return None
        if lease['game_id'] not in sides:
            raise ValueError('Edition does not participate in the reviewed MZ pair')
        with service.lock:
            verify_pair(lower)
        if not matches_mz_web_profile(lower):
            raise ValueError('Reviewed MZ engine files changed')
        from .service import clean_path
        for name, expected in fingerprints.items():
            data = _read_file(clean_path(lower / name), 4 * 1024 * 1024)
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('Reviewed MZ file changed')
            if name == 'data/System.json' and json.loads(data)['advanced']['gameId'] != game_id:
                raise ValueError('Reviewed MZ game identity changed')
        return sides[lease['game_id']][0], service.server_id, game_id

    windows_hash = None
    if (fingerprints['Game.exe'] == MZ_WINDOWS_ENTRY_SHA256
            and fingerprints['data/System.json'] == MZ_WINDOWS_SYSTEM_SHA256):
        windows_hash = MZ_WINDOWS_ENTRY_SHA256
    storage = MZLeaseStorage(service.state, reviewed_pair, chown=service.driver.chown,
                             windows_entry_sha256=windows_hash, edition_ids=sides)
    lease = dict(user_id=review['accountID'], canonical_id=review['canonicalID'])
    if not read_progress_snapshot(storage._root(lease) / 'progress.json', game_id):
        if service.db.execute(
            "SELECT 1 FROM leases WHERE user_id=? AND canonical_id=? AND status!='closed'",
            (lease['user_id'], lease['canonical_id']),
        ).fetchone():
            raise ValueError('Close the existing MZ writer before enrolling progress')
        variants = {
            side: dict(lease, game_id=variant)
            for variant, (side, _, _) in sides.items()
        }
        web = variants['web']
        row = service.db.execute('SELECT * FROM games WHERE id=?', (web['game_id'],)).fetchone()
        reviewed_pair(web, service._lower(row))
        parts = (service.server_id, web['user_id'], web['game_id'])
        identity = hashlib.sha256(''.join(f'{len(part)}:{part}' for part in parts).encode()).hexdigest()
        storage.adopt_matching_views(
            lease, game_id, service.driver.paths(web)['upper'],
            service.driver.paths(variants['windows'])['upper'], identity, tuple(sides),
        )
    return storage
