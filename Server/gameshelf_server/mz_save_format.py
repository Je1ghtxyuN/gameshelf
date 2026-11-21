"""Validate RPG Maker MZ progress shared by native files and WebKit strings."""

from base64 import b64decode, b64encode
import json
import os
import re
import stat
import zlib

_MAX_COMPRESSED = 4 * 1024 * 1024
_MAX_DECODED = 8 * 1024 * 1024
_MAX_TOTAL = 32 * 1024 * 1024
_SLOT = re.compile(r'file(0|[1-9][0-9]{0,2})')


def decode_progress(files):
    """Return decoded progress records after checking their index as a unit."""
    if not 2 <= len(files) <= 110 or 'global' not in files:
        raise ValueError('Incomplete MZ progress')
    decoded = {}
    total = 0
    for name, raw in files.items():
        if name != 'global' and not _SLOT.fullmatch(name):
            raise ValueError('Invalid MZ save slot')
        if not 0 < len(raw) <= _MAX_COMPRESSED:
            raise ValueError('Invalid MZ compressed size')
        stream = zlib.decompressobj()
        try:
            payload = stream.decompress(raw.decode('utf-8').encode('latin1'), _MAX_DECODED)
        except (UnicodeError, zlib.error) as error:
            raise ValueError('Invalid MZ compressed data') from error
        total += len(payload)
        if not stream.eof or stream.unused_data or total > _MAX_TOTAL:
            raise ValueError('Invalid MZ compression boundary or decoded size')
        decoded[name] = json.loads(payload)

    index = decoded['global']
    if not isinstance(index, list) or len(index) > 1000:
        raise ValueError('Invalid MZ save index')
    indexed = {f'file{slot}' for slot, metadata in enumerate(index) if metadata is not None}
    if indexed != decoded.keys() - {'global'} or any(
        not isinstance(metadata, dict) for metadata in index if metadata is not None
    ):
        raise ValueError('MZ slots and save index disagree')
    for name in indexed:
        record = decoded[name]
        if not isinstance(record, dict) or any(
            not isinstance(record.get(key), dict) for key in ('system', 'party', 'map', 'player')
        ):
            raise ValueError('Invalid MZ progress record')
    return decoded


def _prefix(game_id):
    if type(game_id) is not int or not 0 < game_id <= 2**53 - 1:
        raise ValueError('Invalid MZ game identity')
    return f'rmmzsave.{game_id}.'


def web_progress(game_id, files):
    """Project validated native save bytes to WebKit storage keys and strings."""
    prefix = _prefix(game_id)
    if files:
        decode_progress(files)
    return {prefix + name: raw.decode('utf-8') for name, raw in files.items()}


def native_progress(game_id, entries):
    """Project a WebKit progress-only snapshot back to native save bytes."""
    prefix = _prefix(game_id)
    if len(entries) > 110:
        raise ValueError('Too many MZ save entries')
    files = {}
    for key, value in entries.items():
        if not key.startswith(prefix):
            raise ValueError('MZ save belongs to a different game')
        if not isinstance(value, str) or len(value) > _MAX_COMPRESSED:
            raise ValueError('Invalid MZ save value')
        try:
            files[key[len(prefix):]] = value.encode('utf-8')
        except UnicodeError as error:
            raise ValueError('Invalid MZ save encoding') from error
    if files:
        decode_progress(files)
    return files


def merge_progress(current, incoming):
    """Adopt old slots without replacing the bytes of any established slot."""
    before = decode_progress(current) if current else {}
    candidate = decode_progress(incoming) if incoming else {}
    if not before or not candidate:
        return dict(current or incoming)

    result = dict(current)
    index = list(before['global'])
    occupied = {int(name[4:]) for name in before if name != 'global'}
    existing = [(before[f'file{slot}'], index[slot]) for slot in occupied]
    added = False
    incoming_slots = [slot for slot, metadata in enumerate(candidate['global']) if metadata is not None]
    # Keep original slots that are free before allocating any replacements.
    for slot in sorted(incoming_slots, key=occupied.__contains__):
        metadata = candidate['global'][slot]
        name = f'file{slot}'
        entry = (candidate[name], metadata)
        if entry in existing:
            continue
        target = slot
        if target in occupied:
            target = next((n for n in range(1, 1000) if n not in occupied), None)
            if target is None:
                raise ValueError('No free MZ save slot')
        if target >= len(index):
            index.extend([None] * (target + 1 - len(index)))
        index[target] = metadata
        result[f'file{target}'] = incoming[name]
        occupied.add(target)
        existing.append(entry)
        added = True
    if added:
        payload = json.dumps(index, ensure_ascii=False, allow_nan=False).encode('utf-8')
        result['global'] = zlib.compress(payload, level=1).decode('latin1').encode('utf-8')
        decode_progress(result)
    return result


def read_progress_snapshot(path, game_id):
    """Read a committed snapshot from a server-owned directory."""
    _prefix(game_id)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {}
    with os.fdopen(descriptor, 'rb') as handle:
        info = os.fstat(handle.fileno())
        # JSON escaping can expand one source byte into six.
        if not stat.S_ISREG(info.st_mode) or info.st_size > 6 * _MAX_TOTAL + 65536:
            raise ValueError('Invalid MZ snapshot file')
        raw = handle.read(info.st_size + 1)
    if len(raw) != info.st_size:
        raise ValueError('MZ snapshot changed while reading')
    entries = json.loads(raw)
    if not isinstance(entries, dict):
        raise ValueError('Invalid MZ snapshot entries')
    files = native_progress(game_id, entries)
    if not files or sum(map(len, files.values())) > _MAX_TOTAL:
        raise ValueError('Invalid MZ snapshot size')
    return files


def commit_progress_snapshot(path, game_id, files, *, expected):
    """Commit under the canonical writer lock into a server-owned directory."""
    from .privacy import atomic_json

    entries = web_progress(game_id, files)
    if sum(map(len, files.values())) > _MAX_TOTAL:
        raise ValueError('MZ snapshot too large')
    current = read_progress_snapshot(path, game_id)
    if current != files:
        if current != expected or not files:
            raise ValueError('Stale or empty MZ snapshot update')
        atomic_json(path, entries)
    if files:
        # A retry after rename but before directory sync still needs this barrier.
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)



def read_webkit_progress(raw, identity, game_id):
    """Decode the private Swift WebKit store after the account share closes."""
    if len(raw) > 48 * 1024 * 1024:
        raise ValueError('WebKit snapshot too large')
    snapshot = json.loads(raw)
    if (not isinstance(snapshot, dict) or type(snapshot.get('version')) is not int
            or snapshot['version'] != 1 or snapshot.get('identity') != identity):
        raise ValueError('Invalid WebKit snapshot identity')
    entries = snapshot.get('entries')
    if not isinstance(entries, list) or len(entries) > 512:
        raise ValueError('Invalid WebKit entry list')
    prefix = _prefix(game_id)
    ignored = {'rmmzsave.test', prefix+'config', prefix+'titleInfo'}
    progress = {}
    keys = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'key', 'value'}:
            raise ValueError('Invalid WebKit entry')
        if not all(isinstance(value, str) for value in entry.values()):
            raise ValueError('Invalid WebKit entry encoding')
        key = b64decode(entry['key'], validate=True).decode('utf-8')
        value = b64decode(entry['value'], validate=True)
        if key in keys or len(value) > _MAX_COMPRESSED:
            raise ValueError('Duplicate or oversized WebKit entry')
        keys.add(key)
        if key not in ignored:
            progress[key] = value.decode('utf-8')
    return native_progress(game_id, progress)


def webkit_progress_snapshot(identity, game_id, files):
    """Encode a validated progress snapshot in the Swift runtime format."""
    entries = [
        {'key': b64encode(key.encode()).decode(), 'value': b64encode(value.encode()).decode()}
        for key, value in sorted(web_progress(game_id, files).items())
    ]
    raw = json.dumps(dict(version=1, identity=identity, entries=entries)).encode()
    if len(raw) > 48 * 1024 * 1024:
        raise ValueError('WebKit snapshot too large')
    return raw


def read_windows_progress(path, *, validate=True):
    """Read progress files after the private Windows view stops accepting writes."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        directory = os.open(path, flags | os.O_DIRECTORY)
    except FileNotFoundError:
        return {}
    try:
        suffix = '.rmmzsave'
        settings = {'config.rmmzsave', 'titleInfo.rmmzsave'}
        names = [name for name in os.listdir(directory)
                 if name.lower().endswith(suffix) and name not in settings]
        if len(names) > 110:
            raise ValueError('Too many Windows MZ save files')
        files = {}
        total = 0
        for name in names:
            slot = name.removesuffix(suffix)
            if slot != 'global' and not _SLOT.fullmatch(slot):
                raise ValueError('Invalid Windows MZ save filename')
            with os.fdopen(os.open(name, flags, dir_fd=directory), 'rb') as handle:
                before = os.fstat(handle.fileno())
                total += before.st_size
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or not 0 < before.st_size <= _MAX_COMPRESSED or total > _MAX_TOTAL):
                    raise ValueError('Invalid Windows MZ save file')
                data = handle.read(before.st_size + 1)
                after = os.fstat(handle.fileno())
                if (len(data) != before.st_size or before.st_mtime_ns != after.st_mtime_ns
                        or before.st_ctime_ns != after.st_ctime_ns):
                    raise ValueError('Windows MZ save changed while reading')
                files[slot] = data
        if files and validate:
            decode_progress(files)
        return files
    finally:
        os.close(directory)
