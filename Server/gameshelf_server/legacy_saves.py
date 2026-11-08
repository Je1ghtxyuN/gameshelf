"""Bounded, owner-only save adoption; no networking, database or authorization.

The caller MUST verify exact owner/admin-self identity, reserve the canonical
writer, keep that reservation through rollback/recovery, and quiesce/freeze both
trees (including noncooperating host writers). An opaque identity and a profile
directory are binding/safety markers, NOT evidence of authentication, ownership
or a verified mount. No filesystem primitive can CAS against an uncooperative
writer between a final stat and rename. Never expose the view during import.

All three absolute roots must already exist, be disjoint, and have no symlink
components. backup_root must be private (0700, owned by this process's euid).
Selected rules must exist in source; only explicit relative files/directories
are read. Symlinks (even internal), hardlinked files, special nodes, overlapping
rules, and NFC/casefold sibling aliases fail closed. Bounds cover BOTH trees.

plan_import is read-only and returns immutable serialized stat/content/listing
snapshots. apply_import revalidates them, backs up selected source and target
bytes, journals each installation, then adopts absent/zero files only. Identical
files retain their inode. Exceptions roll back only this operation's own inodes;
unexpected progress is preserved and reported via RollbackError. Private backups
and ledger are retained. Reapplying an interrupted plan finishes rollback before
retrying; completed retries validate the committed snapshot and do not write.

This is not an OverlayFS mount verifier or an unlink/conversion endpoint. Keep
the legacy record until the caller verifies successful adoption. Filesystem
fsync/rename durability depends on the backing filesystem; test real overlays
separately. No source file is written, renamed, chmodded or removed here.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import unicodedata
import uuid


PROFILE = '.gameshelf-profile'
OWNER = PROFILE + '/legacy-save-import-owner.json'
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_READ = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_MAX_JSON = 32 * 1024 * 1024


class SaveImportError(ValueError):
    pass


class SnapshotChanged(SaveImportError):
    pass


class DestinationConflict(SaveImportError):
    def __init__(self, paths):
        self.paths = tuple(sorted(set(paths)))
        super().__init__('Existing progress differs; explicit conflict: ' + ', '.join(self.paths))


class RollbackError(SaveImportError):
    def __init__(self, operation_id, paths):
        self.operation_id, self.paths = operation_id, tuple(sorted(set(paths)))
        super().__init__('Rollback pending; preserve backup and canonical reservation: ' + operation_id)


@dataclass(frozen=True)
class Limits:
    max_files: int = 4096
    max_bytes: int = 256 * 1024 * 1024
    max_entries: int = 10000
    max_depth: int = 32

    def validate(self):
        for value, cap in ((self.max_files, 100000), (self.max_bytes, 8 * 1024**3),
                           (self.max_entries, 10000), (self.max_depth, 128)):
            if type(value) is not int or not 0 < value <= cap:
                raise SaveImportError('Invalid import limits')


@dataclass(frozen=True)
class ImportPlan:
    source_root: str
    target_private_view: str
    backup_root: str
    identity: str
    rules: tuple
    limits: Limits
    source_snapshot: str
    target_snapshot: str
    roots: tuple
    operation_id: str

    @property
    def source_files(self):
        return tuple(sorted(p for p, n in json.loads(self.source_snapshot).items() if n['kind'] == 'file'))

    @property
    def source_bytes(self):
        return sum(n['size'] for n in json.loads(self.source_snapshot).values() if n['kind'] == 'file')


@dataclass(frozen=True)
class ImportResult:
    """copied/identical describe the operation manifest, also on completed retries."""
    operation_id: str
    status: str
    copied: tuple
    identical: tuple
    backup_path: str


def _json(value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    if len(encoded) > _MAX_JSON:
        raise SaveImportError('Import manifest exceeds bound')
    return encoded


def _key(name):
    return unicodedata.normalize('NFC', name).casefold()


def _safe_name(name):
    try:
        name.encode('utf-8')
    except UnicodeError:
        raise SaveImportError('Non-UTF8 path component') from None
    if (not name or name in ('.', '..') or name.endswith(('.', ' ')) or
            any(ord(c) < 32 or c in '\\:' for c in name)):
        raise SaveImportError('Unsafe relative path')


def _relative(value):
    if not isinstance(value, str) or len(value.encode('utf-8')) > 4096:
        raise SaveImportError('Invalid relative path')
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value:
        raise SaveImportError('Noncanonical relative path')
    for part in value.split('/'):
        _safe_name(part)
    return value


def _names(fd):
    result = []
    with os.scandir(fd) as entries:
        for entry in entries:
            result.append(entry.name)
            if len(result) > 10000:
                raise SaveImportError('Directory entry bound exceeded')
    return result


def _checked_names(fd):
    names = _names(fd)
    keys = set()
    for name in names:
        _safe_name(name)
        key = _key(name)
        if key in keys:
            raise SaveImportError('Ambiguous case/Unicode path alias')
        keys.add(key)
    return sorted(names)


def _lookup(fd, name):
    names = _checked_names(fd)
    aliases = [n for n in names if _key(n) == _key(name)]
    if aliases and aliases != [name]:
        raise SaveImportError('Path spelling aliases an existing name')
    return name in names


def _attrs(st):
    return dict(dev=st.st_dev, ino=st.st_ino, mode=st.st_mode, uid=st.st_uid,
                gid=st.st_gid, nlink=st.st_nlink, size=st.st_size,
                mtime=st.st_mtime_ns, ctime=st.st_ctime_ns)


def _identity(st):
    return (st.st_dev, st.st_ino, stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid)


def _child(fd, name):
    try:
        return os.open(name, _DIR, dir_fd=fd)
    except OSError as error:
        raise SaveImportError('Directory component is missing, linked or special') from error


@contextmanager
def _open_root(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or len(path.parts) > 128:
        raise SaveImportError('Root must be an absolute nonlinked directory')
    fd = os.open('/', _DIR)
    try:
        try:
            for name in path.parts[1:]:
                _safe_name(name)
                if not _lookup(fd, name):
                    raise SaveImportError('Root is unavailable')
                child = _child(fd, name)
                os.close(fd)
                fd = child
        except OSError as error:
            raise SaveImportError('Root is unavailable, linked or not a directory') from error
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _parent(root, relative):
    fd = os.dup(root)
    try:
        parts = relative.split('/')
        for part in parts[:-1]:
            if not _lookup(fd, part):
                raise SnapshotChanged('Parent directory disappeared')
            child = _child(fd, part)
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    finally:
        os.close(fd)


def _file_node(fd, name, limit):
    descriptor = os.open(name, _READ, dir_fd=fd)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise SaveImportError('Special, linked or oversized file')
        digest, size = hashlib.sha256(), 0
        while chunk := os.read(descriptor, min(65536, limit + 1 - size)):
            size += len(chunk)
            if size > limit:
                raise SaveImportError('Byte bound exceeded')
            digest.update(chunk)
        if _attrs(before) != _attrs(os.fstat(descriptor)) or size != before.st_size:
            raise SnapshotChanged('File changed while fingerprinting')
        return dict(_attrs(before), kind='file', sha256=digest.hexdigest())
    finally:
        os.close(descriptor)


def _read_json(fd, name, limit=_MAX_JSON):
    node = _file_node(fd, name, limit)
    if stat.S_IMODE(node['mode']) & 0o077:
        raise SaveImportError('Private manifest permissions are unsafe')
    descriptor = os.open(name, _READ, dir_fd=fd)
    try:
        data = bytearray()
        while chunk := os.read(descriptor, min(65536, limit + 1 - len(data))):
            data.extend(chunk)
            if len(data) > limit:
                raise SaveImportError('Manifest too large')
        if hashlib.sha256(data).hexdigest() != node['sha256']:
            raise SnapshotChanged('Manifest changed')
        return json.loads(data)
    finally:
        os.close(descriptor)


def _capture(root, rules, limits, *, target=False, identity=None):
    nodes, files, size, entries = {}, 0, 0, 0

    def directory(fd, path):
        nonlocal entries
        if path not in nodes:
            before = os.fstat(fd)
            names = _checked_names(fd)
            # The fixed isolation/owner metadata has its own small hard bound.
            counted = [n for n in names if not (target and
                       ((path == '' and n == PROFILE) or (path == PROFILE and n == OWNER.split('/')[-1])))]
            entries += len(counted)
            if entries > limits.max_entries:
                raise SaveImportError('Aggregate directory entry bound exceeded')
            if _attrs(before) != _attrs(os.fstat(fd)):
                raise SnapshotChanged('Directory changed during scan')
            nodes[path] = dict(_attrs(before), kind='directory', names=names)

    def visit(fd, name, path, recurse):
        nonlocal files, size
        if len(path.split('/')) > limits.max_depth and not (target and path in (PROFILE, OWNER)):
            raise SaveImportError('Directory depth bound exceeded')
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = _child(fd, name)
            try:
                directory(child, path)
                if recurse:
                    for entry in nodes[path]['names']:
                        visit(child, entry, path + '/' + entry, True)
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode):
            if target and path == OWNER:
                node = _file_node(fd, name, 8192)  # Fixed bounded metadata, not a save.
            else:
                files += 1
                if files > limits.max_files:
                    raise SaveImportError('File count bound exceeded')
                node = _file_node(fd, name, max(0, limits.max_bytes - size))
                size += node['size']
            nodes[path] = node
        else:
            raise SaveImportError('Symlink or special save node')

    def selected(path, kind, required=True, recurse=True):
        fd = os.dup(root)
        try:
            prefix = ''
            for index, part in enumerate(path.split('/')):
                directory(fd, prefix)
                full = prefix + '/' + part if prefix else part
                if not _lookup(fd, part):
                    if required:
                        raise SaveImportError('Selected source rule is missing')
                    nodes[full] = dict(kind='missing')
                    return
                if index == len(path.split('/')) - 1:
                    visit(fd, part, full, recurse)
                    if nodes[full]['kind'] != kind:
                        raise SaveImportError('Save rule kind mismatch')
                else:
                    child = _child(fd, part)
                    os.close(fd)
                    fd, prefix = child, full
        finally:
            os.close(fd)

    directory(root, '')
    for path, kind in rules:
        selected(path, kind, required=not target)
    if target:
        selected(PROFILE, 'directory', recurse=False)
        selected(OWNER, 'file', required=False)
        if nodes.get(OWNER, {}).get('kind') == 'file':
            with _parent(root, OWNER) as (fd, name):
                if _read_json(fd, name, 8192) != dict(version=1, identity=identity):
                    raise SaveImportError('Private view belongs to a different import identity')
    return nodes, files, size


def _rules(value):
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 2048:
        raise SaveImportError('Explicit bounded save rules required')
    rules = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'path', 'kind'} or item['kind'] not in ('file', 'directory'):
            raise SaveImportError('Rule requires relative path and file/directory kind')
        path = _relative(item['path'])
        if _key(path.split('/')[0]) == _key(PROFILE):
            raise SaveImportError('Reserved isolation profile cannot be imported')
        for existing, _ in rules:
            a, b = _key(existing), _key(path)
            if a == b or a.startswith(b + '/') or b.startswith(a + '/'):
                raise SaveImportError('Overlapping or aliased save rules')
        rules.append((path, item['kind']))
    return tuple(sorted(rules))


def _validate_roots(paths, fds):
    for index, a in enumerate(paths):
        for j, b in enumerate(paths[:index]):
            if (a == b or a in b.parents or b in a.parents or
                    _identity(os.fstat(fds[index]))[:2] == _identity(os.fstat(fds[j]))[:2]):
                raise SaveImportError('Source, private view and backup roots must not overlap')
    backup = os.fstat(fds[2])
    if stat.S_IMODE(backup.st_mode) & 0o077 or backup.st_uid != os.geteuid():
        raise SaveImportError('Backup root must be private and owned by the gateway process')


def _binding(plan):
    return dict(source=plan.source_root, target=plan.target_private_view, backup=plan.backup_root,
                identity=plan.identity, rules=plan.rules, roots=plan.roots,
                source_snapshot=plan.source_snapshot)


def plan_import(source_root, target_private_view, rules, backup_root, identity, *, limits=Limits()):
    """Read-only whole-selection preflight. Raises DestinationConflict(paths).

    Bounds count regular files/bytes across both selected trees. Empty selected
    directories are retained; unrelated destination progress is never removed.
    """
    limits.validate()
    if not isinstance(identity, str) or not identity or len(identity.encode('utf-8')) > 4096:
        raise SaveImportError('Nonempty bounded opaque identity required')
    selected = _rules(rules)
    if any(len(path.split('/')) > limits.max_depth for path, _ in selected):
        raise SaveImportError('Rule depth bound exceeded')
    paths = tuple(Path(p) for p in (source_root, target_private_view, backup_root))
    with _open_root(paths[0]) as source, _open_root(paths[1]) as target, _open_root(paths[2]) as backup:
        _validate_roots(paths, (source, target, backup))
        before, count, size = _capture(source, selected, limits)
        after, count2, size2 = _capture(target, selected, limits, target=True, identity=identity)
        if count + count2 > limits.max_files or size + size2 > limits.max_bytes:
            raise SaveImportError('Aggregate file/byte bound exceeded')
        conflicts = []
        source_inodes = {(n['dev'], n['ino']) for n in before.values() if n['kind'] == 'file'}
        source_nodes = {(n['dev'], n['ino']) for n in before.values() if n['kind'] != 'missing'}
        target_nodes = {(n['dev'], n['ino']) for n in after.values() if n['kind'] != 'missing'}
        if source_nodes & target_nodes or _identity(os.fstat(backup))[:2] in source_nodes | target_nodes:
            raise SaveImportError('Selected trees or backup overlap physically')
        for path, node in before.items():
            old = after.get(path)
            if node['kind'] != 'file' or not old or old['kind'] == 'missing':
                continue
            if old['kind'] != 'file' or (old['dev'], old['ino']) in source_inodes:
                raise SaveImportError('Save paths overlap physically or have incompatible kinds')
            if old['size'] and (old['size'], old['sha256']) != (node['size'], node['sha256']):
                conflicts.append(path)
        if conflicts:
            raise DestinationConflict(conflicts)
        roots = tuple(_identity(os.fstat(fd)) for fd in (source, target, backup))
        plan = ImportPlan(*(str(p) for p in paths), identity, selected, limits,
                          _json(before), _json(after), roots, '')
        digest = hashlib.sha256(_json(_binding(plan)).encode()).hexdigest()
        return ImportPlan(plan.source_root, plan.target_private_view, plan.backup_root, identity,
                          selected, limits, plan.source_snapshot, plan.target_snapshot, roots, digest)


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError('Short filesystem write')
        view = view[written:]


def _save_ledger(operation, ledger):
    name = '.ledger-' + uuid.uuid4().hex
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=operation)
    try:
        try:
            _write_all(fd, _json(ledger).encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(name, 'ledger.json', src_dir_fd=operation, dst_dir_fd=operation)
        os.fsync(operation)
    finally:
        try:
            os.unlink(name, dir_fd=operation)
        except FileNotFoundError:
            pass


def _copy_file(source_fd, source_name, target_fd, target_name, expected):
    """Copy into an already-created empty staging file, checking content/stats."""
    src = os.open(source_name, _READ, dir_fd=source_fd)
    dst = None
    try:
        dst = os.open(target_name, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=target_fd)
        before, output = os.fstat(src), os.fstat(dst)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not stat.S_ISREG(output.st_mode) or output.st_size or output.st_nlink != 1:
            raise SaveImportError('Unsafe copy endpoint')
        if 'dev' in expected and _attrs(before) != {k: expected[k] for k in _attrs(before)}:
            raise SnapshotChanged('Copy source metadata changed')
        digest, size = hashlib.sha256(), 0
        while chunk := os.read(src, 65536):
            size += len(chunk)
            if size > expected['size']:
                raise SnapshotChanged('Copy source grew')
            digest.update(chunk)
            _write_all(dst, chunk)
        if (size != expected['size'] or digest.hexdigest() != expected['sha256'] or
                _attrs(before) != _attrs(os.fstat(src))):
            raise SnapshotChanged('Copy source fingerprint changed')
        os.fsync(dst)
    finally:
        os.close(src)
        if dst is not None:
            os.close(dst)


def _mkdirs(root, relative):
    fd = os.dup(root)
    try:
        for part in relative.split('/') if relative else ():
            if not _lookup(fd, part):
                os.mkdir(part, 0o700, dir_fd=fd)
                os.fsync(fd)
            child = _child(fd, part)
            os.close(fd)
            fd = child
    finally:
        os.close(fd)


def _backup_tree(root, operation, prefix, snapshot):
    _mkdirs(operation, prefix)
    for path, node in sorted(snapshot.items()):
        if not path or node['kind'] == 'missing':
            continue
        dest = prefix + '/' + path
        if node['kind'] == 'directory':
            _mkdirs(operation, dest)
            continue
        _mkdirs(operation, dest.rpartition('/')[0])
        with _parent(operation, dest) as (out, name), _parent(root, path) as (src, original):
            if _lookup(out, name):
                old = _file_node(out, name, node['size'])
                if (old['size'], old['sha256']) != (node['size'], node['sha256']):
                    raise SaveImportError('Existing private backup failed validation')
                continue
            temp = '.backup-stage-' + uuid.uuid4().hex
            fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=out)
            os.close(fd)
            try:
                _copy_file(src, original, out, temp, node)
                os.link(temp, name, src_dir_fd=out, dst_dir_fd=out, follow_symlinks=False)
                os.unlink(temp, dir_fd=out)
                os.fsync(out)
            finally:
                try:
                    os.unlink(temp, dir_fd=out)
                except FileNotFoundError:
                    pass


def _expect_file(fd, name, original):
    if not _lookup(fd, name):
        if not original or original['kind'] == 'missing':
            return
        raise SnapshotChanged('Destination disappeared')
    if not original or original['kind'] != 'file' or _file_node(fd, name, original['size']) != original:
        raise SnapshotChanged('Destination changed before installation')


def _check_parent_ids(root, relative, ledger):
    known = dict(ledger['original_target'])
    known.update({d['path']: d['node'] for d in ledger['directories']})
    fd = os.dup(root)
    try:
        prefix = ''
        for part in relative.split('/')[:-1]:
            prefix = prefix + '/' + part if prefix else part
            child = _child(fd, part)
            os.close(fd)
            fd = child
            current, old = os.fstat(fd), known.get(prefix)
            if not old or old.get('kind') != 'directory' or (current.st_dev, current.st_ino) != (old['dev'], old['ino']):
                raise SnapshotChanged('Destination parent was replaced')
    finally:
        os.close(fd)


def _install_file(target, operation, ledger, path, original, *, source=None, data=None):
    _check_parent_ids(target, path, ledger)
    with _parent(target, path) as (parent, name):
        _expect_file(parent, name, original)
        temp = '.legacy-save-stage-' + uuid.uuid4().hex
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            owner = os.fstat(parent)
            if (os.fstat(fd).st_uid, os.fstat(fd).st_gid) != (owner.st_uid, owner.st_gid):
                os.fchown(fd, owner.st_uid, owner.st_gid)
            info = os.fstat(fd)
        finally:
            os.close(fd)
        digest = source[2]['sha256'] if source else hashlib.sha256(data).hexdigest()
        entry = dict(path=path, original=original, stage=temp, dev=info.st_dev, ino=info.st_ino,
                     sha256=digest, size=source[2]['size'] if source else len(data))
        ledger['entries'].append(entry)
        _save_ledger(operation, ledger)  # Write-ahead intent includes staging inode.
        if source:
            _copy_file(source[0], source[1], parent, temp,
                       dict(size=entry['size'], sha256=digest))
        else:
            fd = os.open(temp, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                _write_all(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
        _expect_file(parent, name, original)
        if original and original['kind'] == 'file':
            if original['size']:
                raise DestinationConflict([path])
            os.rename(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
        else:
            os.link(temp, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            os.unlink(temp, dir_fd=parent)
        os.fsync(parent)


def _make_target_dirs(target, operation, ledger, source):
    needed = {p for p, n in source.items() if p and n['kind'] == 'directory'}
    for path in sorted(needed, key=lambda p: (p.count('/'), p)):
        _check_parent_ids(target, path, ledger)
        with _parent(target, path) as (fd, name):
            if not _lookup(fd, name):
                os.mkdir(name, 0o700, dir_fd=fd)
                child = os.open(name, _DIR, dir_fd=fd)
                try:
                    owner = os.fstat(fd)
                    if (os.fstat(child).st_uid, os.fstat(child).st_gid) != (owner.st_uid, owner.st_gid):
                        os.fchown(child, owner.st_uid, owner.st_gid)
                    ledger['directories'].append(dict(path=path, node=dict(_attrs(os.fstat(child)), kind='directory')))
                    _save_ledger(operation, ledger)
                    os.fsync(fd)
                finally:
                    os.close(child)


def _rollback_file(target, operation, ledger, entry):
    path = entry['path']
    # A previous rollback may have removed newly created parents before a later
    # marker/ledger operation failed. Their absent children are already undone.
    original = entry['original']
    if not original or original['kind'] == 'missing':
        fd = os.dup(target)
        try:
            prefix = ''
            created = {d['path'] for d in ledger['directories']}
            for part in path.split('/')[:-1]:
                prefix = prefix + '/' + part if prefix else part
                if not _lookup(fd, part):
                    if prefix in created:
                        return
                    raise SnapshotChanged('Unexpected missing rollback parent')
                child = _child(fd, part)
                os.close(fd)
                fd = child
        finally:
            os.close(fd)
    _check_parent_ids(target, path, ledger)
    with _parent(target, path) as (fd, name):
        if _lookup(fd, entry['stage']):
            info = os.stat(entry['stage'], dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (entry['dev'], entry['ino']) or info.st_nlink > 2:
                raise SnapshotChanged('Staging file no longer belongs to import')
            os.unlink(entry['stage'], dir_fd=fd)
        if not _lookup(fd, name):
            if original and original['kind'] == 'file':
                raise SnapshotChanged('Original destination disappeared during rollback')
            return
        current = _file_node(fd, name, max(entry['size'], (original or {}).get('size', 0)))
        if original and original['kind'] == 'file' and current['size'] == 0:
            return  # Already restored zero placeholder, including a recovery retry.
        if (current['dev'], current['ino'], current['sha256']) != (entry['dev'], entry['ino'], entry['sha256']):
            raise SnapshotChanged('Unexpected progress preserved during rollback')
        if original and original['kind'] == 'file':
            if original['size']:
                raise SaveImportError('Refusing nonempty rollback overwrite')
            temp = '.rollback-' + uuid.uuid4().hex
            out = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                os.fchmod(out, stat.S_IMODE(original['mode']))
                if (os.fstat(out).st_uid, os.fstat(out).st_gid) != (original['uid'], original['gid']):
                    os.fchown(out, original['uid'], original['gid'])
                os.fsync(out)
            finally:
                os.close(out)
            os.rename(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.utime(name, ns=(original['mtime'], original['mtime']), dir_fd=fd, follow_symlinks=False)
        else:
            os.unlink(name, dir_fd=fd)
        os.fsync(fd)


def _rollback(target, operation, ledger, plan):
    failed = []
    for entry in reversed(ledger['entries']):
        if entry['path'] == OWNER:
            continue
        try:
            _rollback_file(target, operation, ledger, entry)
        except Exception:
            failed.append(entry['path'])
    for directory in reversed(ledger['directories']):
        try:
            with _parent(target, directory['path']) as (fd, name):
                if not _lookup(fd, name):
                    continue
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                old = directory['node']
                if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (old['dev'], old['ino']):
                    raise SnapshotChanged('Created directory was replaced')
                os.rmdir(name, dir_fd=fd)
                os.fsync(fd)
        except Exception:
            failed.append(directory['path'])
    if not failed:
        try:
            intermediate = _capture(target, plan.rules, plan.limits, target=True, identity=plan.identity)[0]
            expected = dict(ledger['original_target'])
            for entry in ledger['entries']:
                old, now = entry['original'], intermediate.get(entry['path'])
                if old and old['kind'] == 'file' and old['size'] == 0 and now and now['kind'] == 'file' and now['size'] == 0:
                    if any(old[k] != now[k] for k in ('mode', 'uid', 'gid')):
                        raise SnapshotChanged('Restored placeholder permissions changed')
                    expected[entry['path']] = now  # A restored empty file has a new inode/ctime.
            marker_entries = [e for e in ledger['entries'] if e['path'] == OWNER
                              and intermediate.get(OWNER, {}).get('kind') == 'file']
            _postcheck(expected, intermediate, {}, dict(entries=marker_entries, directories=[]))
        except Exception:
            failed.append('target-snapshot')
    # Retain owner binding while any progress/rollback is unresolved.
    if not failed:
        for entry in ledger['entries']:
            if entry['path'] == OWNER:
                try:
                    _rollback_file(target, operation, ledger, entry)
                except Exception:
                    failed.append(OWNER)
    ledger['phase'] = 'rollback-failed' if failed else 'rolled-back'
    if not failed:
        ledger['expected_target'] = _capture(target, plan.rules, plan.limits, target=True, identity=plan.identity)[0]
        ledger['entries'], ledger['directories'] = [], []
    try:
        _save_ledger(operation, ledger)
    except Exception:
        failed.append('ledger.json')
    if failed:
        raise RollbackError(plan.operation_id, failed)


def _postcheck(before, after, source, ledger):
    changes = {e['path']: e for e in ledger['entries']}
    additions = {p for p in source if p} | set(changes)
    made = {d['path']: d['node'] for d in ledger['directories']}
    for path, node in after.items():
        old = before.get(path)
        if node['kind'] == 'directory':
            original = old if old and old['kind'] == 'directory' else made.get(path)
            if not original or any(node[k] != original[k] for k in ('dev', 'ino', 'mode', 'uid', 'gid')):
                raise SnapshotChanged('Destination directory changed')
            names = set(original.get('names', []))
            names.update(p.rpartition('/')[2] for p in additions if p.rpartition('/')[0] == path)
            if set(node['names']) != names:
                raise SnapshotChanged('Destination directory listing changed')
        elif path in changes:
            entry = changes[path]
            if (node.get('dev'), node.get('ino'), node.get('sha256'), node.get('size')) != (entry['dev'], entry['ino'], entry['sha256'], entry['size']):
                raise SnapshotChanged('Imported progress changed')
        elif old != node:
            raise SnapshotChanged('Unmodified destination progress changed')
    for path, node in before.items():
        if node['kind'] != 'missing' and path not in after:
            raise SnapshotChanged('Destination progress disappeared')


def apply_import(plan):
    """Apply/recover one reviewed plan under the caller's exclusive reservation.

    Returns ImportResult; exceptions retain private backups/ledger. RollbackError
    means do not expose/reassign the view: inspect its paths and retry recovery
    under the SAME identity/reservation. Never delete backups to force a retry.
    """
    if not isinstance(plan, ImportPlan) or hashlib.sha256(_json(_binding(plan)).encode()).hexdigest() != plan.operation_id:
        raise SaveImportError('Invalid immutable import plan')
    paths = tuple(Path(p) for p in (plan.source_root, plan.target_private_view, plan.backup_root))
    with _open_root(paths[0]) as source, _open_root(paths[1]) as target, _open_root(paths[2]) as backup:
        _validate_roots(paths, (source, target, backup))
        if tuple(_identity(os.fstat(fd)) for fd in (source, target, backup)) != plan.roots:
            raise SnapshotChanged('An import root was replaced or permissions changed')
        lock = os.open('.legacy-save-import.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=backup)
        try:
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077:
                raise SaveImportError('Unsafe import lock')
            fcntl.flock(lock, fcntl.LOCK_EX)
            before = json.loads(plan.source_snapshot)
            if not _lookup(backup, plan.operation_id):
                if _capture(source, plan.rules, plan.limits)[0] != before:
                    raise SnapshotChanged('Source snapshot changed after planning')
                current = _capture(target, plan.rules, plan.limits, target=True, identity=plan.identity)[0]
                if current != json.loads(plan.target_snapshot):
                    raise SnapshotChanged('Destination snapshot changed after planning')
                os.mkdir(plan.operation_id, 0o700, dir_fd=backup)
                os.fsync(backup)
            operation = os.open(plan.operation_id, _DIR, dir_fd=backup)
            try:
                op_info = os.fstat(operation)
                if stat.S_IMODE(op_info.st_mode) & 0o077 or op_info.st_uid != os.geteuid():
                    raise SaveImportError('Unsafe private backup operation')
                if _lookup(operation, 'ledger.json'):
                    ledger = _read_json(operation, 'ledger.json')
                    if not isinstance(ledger, dict) or ledger.get('version') != 1 or ledger.get('binding') != json.loads(_json(_binding(plan))):
                        raise SaveImportError('Backup/ledger identity mismatch')
                else:
                    current = _capture(target, plan.rules, plan.limits, target=True, identity=plan.identity)[0]
                    if current != json.loads(plan.target_snapshot):
                        raise SnapshotChanged('Destination snapshot changed')
                    ledger = dict(version=1, binding=_binding(plan), phase='backing-up',
                                  original_target=current, expected_target=current, entries=[], directories=[])
                    _save_ledger(operation, ledger)
                files = {p: n for p, n in before.items() if n['kind'] == 'file'}
                copied = tuple(sorted(p for p in files if ledger['original_target'].get(p, {}).get('sha256') != files[p]['sha256']))
                identical = tuple(sorted(set(files) - set(copied)))
                result = lambda status: ImportResult(plan.operation_id, status, copied, identical, str(paths[2] / plan.operation_id))
                # Recovery needs only private backups/ledger, never a still-valid
                # legacy source. Finish undoing an interrupted attempt first.
                if ledger['phase'] in ('applying', 'rollback-failed'):
                    _rollback(target, operation, ledger, plan)
                if _capture(source, plan.rules, plan.limits)[0] != before:
                    raise SnapshotChanged('Source snapshot changed after planning')
                current = _capture(target, plan.rules, plan.limits, target=True, identity=plan.identity)[0]
                if ledger['phase'] == 'committed':
                    if current != ledger['committed_target']:
                        raise SnapshotChanged('Committed destination changed; preserve its newer progress')
                    return result('already-applied')
                if ledger['phase'] not in ('backing-up', 'rolled-back'):
                    raise SaveImportError('Unknown import ledger phase')
                if _capture(target, plan.rules, plan.limits, target=True, identity=plan.identity)[0] != ledger['expected_target']:
                    raise SnapshotChanged('Destination snapshot changed before retry')
                _backup_tree(source, operation, 'source', before)
                _backup_tree(target, operation, 'target', ledger['original_target'])
                if _capture(source, plan.rules, plan.limits)[0] != before:
                    raise SnapshotChanged('Source changed during backup')
                baseline = _capture(target, plan.rules, plan.limits, target=True, identity=plan.identity)[0]
                if baseline != ledger['expected_target']:
                    raise SnapshotChanged('Destination changed during backup')
                ledger['original_target'] = baseline
                ledger['phase'] = 'applying'
                _save_ledger(operation, ledger)
                try:
                    if baseline.get(OWNER, {}).get('kind') == 'missing':
                        _install_file(target, operation, ledger, OWNER, None,
                                      data=_json(dict(version=1, identity=plan.identity)).encode())
                    _make_target_dirs(target, operation, ledger, before)
                    for path in copied:
                        with _parent(operation, 'source/' + path) as (fd, name):
                            _install_file(target, operation, ledger, path, baseline.get(path), source=(fd, name, files[path]))
                    if _capture(source, plan.rules, plan.limits)[0] != before:
                        raise SnapshotChanged('Source changed during import')
                    final = _capture(target, plan.rules, plan.limits, target=True, identity=plan.identity)[0]
                    _postcheck(baseline, final, before, ledger)
                    ledger['phase'], ledger['committed_target'] = 'committed', final
                    _save_ledger(operation, ledger)
                except Exception:
                    _rollback(target, operation, ledger, plan)
                    raise
                return result('applied')
            finally:
                os.close(operation)
        finally:
            os.close(lock)
