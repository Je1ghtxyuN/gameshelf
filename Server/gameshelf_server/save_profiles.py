"""Operator-reviewed RenPy evidence. No network, inference, API-issued proof or execution."""
import base64
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid
from .common import APIError
from .accounts import fields

ADAPTER='renpy-savedir-v1'
RELATIVE='.gameshelf-profile/CanonicalSaves/renpy'
CATALOG='reviewed-renpy-profiles.json'
LOCATION_PATCHES = {
    # Ren'Py 8.1.3
    '2fcd8f8dd88e85fd689a91db1f98c7ab7c5790d3b871b1f89420e27db3bba8f2':
        '178aaa36fba90afcaed683e31048200328653fbbf4bc7c6c7e9d5ac65f721c24',
    # Ren'Py 8.3.7
    '7a84aea6ddccab67c7e004d94cd38092e4a910c15ccb6fabb44aecbde1108427':
        'ba48a4c044ec8d557bbc2f42aab441caaf52480ebeb29f2a9208594b6dc09596',
    # Ren'Py 8.3.2
    '3ed845b5c9d13dcf581a9143b5fc933fc1dfad8f03c93bccc2ef0955adb7e884':
        '36e6fe2778ad3d2ca6afe0325eba33661e05e7703b264cf7c0a34dcdf8991365',
    # Ren'Py 8.5.2
    'e0b32aab4127325a307a8396b6a9b636781161fd23d595127e668d784342b85c':
        '1b4b59ac6e0359d99da69378a18a446eecb0c08038e7d06452e56a19bbca46d4',
}
ROLES={'entry','bootstrap','arguments','main','locations','persistent','tokens','config'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()


def relative(value,*,dot=False,reserved=False):
    if dot and value=='.': return value
    if not isinstance(value,str) or not value or len(value)>4096 or any(ord(c)<32 for c in value): raise APIError(400,'Invalid reviewed relative path')
    p=PurePosixPath(value)
    if p.is_absolute() or p.as_posix()!=value or any(x in ('','.','..') for x in value.split('/')) or any(c in value for c in '\\:'):
        raise APIError(400,'Unsafe reviewed relative path')
    if not reserved and p.parts[0]=='.gameshelf-profile': raise APIError(400,'Code/source rules cannot use the reserved profile')
    return value


def _tag(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}',value): raise APIError(400,'Invalid reviewed profile identifier')
    return value


def _sha(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{64}',value): raise APIError(400,'Invalid reviewed SHA256')
    return value


def _rules(values,*,reserved=False):
    if not isinstance(values,list) or len(values)>32: raise APIError(400,'Invalid reviewed directory rules')
    result=[]
    for rule in values:
        fields(rule,('path','kind')); path=relative(rule['path'],reserved=reserved)
        if rule['kind']!='directory': raise APIError(400,'RenPy namespace requires directory rules')
        if reserved and (path==RELATIVE or RELATIVE.startswith(path+'/') or path.startswith(RELATIVE+'/')):
            raise APIError(400,'Legacy rules cannot cover the canonical namespace target')
        if any(path==r['path'] or path.startswith(r['path']+'/') or r['path'].startswith(path+'/') for r in result):
            raise APIError(400,'Overlapping reviewed legacy rules')
        result.append(dict(path=path,kind='directory'))
    return sorted(result,key=lambda r:r['path'])


def validate_catalog(payload):
    fields(payload,('schemaVersion','profiles'))
    if type(payload['schemaVersion']) is not int or payload['schemaVersion']!=1 or not isinstance(payload['profiles'],list) or len(payload['profiles'])>32:
        raise APIError(400,'Unsupported reviewed catalog')
    catalog={}
    for original in payload['profiles']:
        profile=copy.deepcopy(original)
        fields(profile,('id','revision','adapter','compatibilityKey','synthetic','isolationPolicy','review','variants'),('scope',))
        _tag(profile['id']); _tag(profile['compatibilityKey'])
        if profile['id'] in catalog or type(profile['revision']) is not int or profile['revision']<1 or type(profile['synthetic']) is not bool:
            raise APIError(400,'Invalid reviewed profile version')
        if profile['adapter']!=ADAPTER or profile['isolationPolicy']!='single-primary-location-and-fixed-env-v1':
            raise APIError(400,'RenPy isolation policy requires primary-only locations and fixed environment')
        fields(profile['review'],('by','basis'))
        if any(not isinstance(v,str) or not v.strip() or len(v)>2048 for v in profile['review'].values()): raise APIError(400,'Missing review provenance')
        variants=profile['variants']
        if not isinstance(variants,list) or not 1<=len(variants)<=32: raise APIError(400,'Invalid reviewed variants')
        if 'scope' in profile and (profile['scope']!='edition' or len(variants)!=1):
            raise APIError(400,'Edition save scope requires exactly one reviewed variant')
        seen=set()
        for variant in variants:
            fields(variant,('id','platform','entryPath','entryBackend','savePaths','legacySavePaths','fingerprints'),('patches',))
            _tag(variant['id'])
            if variant['id'] in seen: raise APIError(400,'Duplicate reviewed variant')
            seen.add(variant['id']); platform=variant['platform']; path=relative(variant['entryPath'],dot=platform=='macos')
            if platform=='macos':
                if variant['entryBackend']!='native' or not (path=='.' or path.lower().endswith('.app')): raise APIError(400,'Mac adapter needs a reviewed native app')
            elif platform=='windows':
                if variant['entryBackend']!='renpy' or not path.lower().endswith('.exe'): raise APIError(400,'Windows adapter needs exact RenPy EXE')
            else: raise APIError(400,'Unsupported RenPy platform')
            variant['savePaths']=_rules(variant['savePaths']); variant['legacySavePaths']=_rules(variant['legacySavePaths'],reserved=True)
            fingerprints=variant['fingerprints']
            if not isinstance(fingerprints,list) or not 8<=len(fingerprints)<=128: raise APIError(400,'Incomplete runtime/configuration proof')
            names=set(); roles=set()
            for item in fingerprints:
                fields(item,('path','role','sha256')); relative(item['path']); _sha(item['sha256'])
                if item['path'].casefold() in names or item['role'] not in ROLES|{'metadata'}: raise APIError(400,'Ambiguous reviewed code proof')
                names.add(item['path'].casefold()); roles.add(item['role'])
            if not ROLES<=roles or sum(f['role']=='entry' for f in fingerprints)!=1: raise APIError(400,'Missing privacy-critical runtime/config proofs')
            if platform=='macos':
                info=('' if path=='.' else path+'/')+'Contents/Info.plist'
                if not any(f['path']==info and f['role']=='metadata' for f in fingerprints): raise APIError(400,'Native app Info.plist proof required')
            elif not any(f['path']==path and f['role']=='entry' for f in fingerprints): raise APIError(400,'EXE fingerprint must match entryPath')
            patched=set()
            for patch in variant.get('patches',[]):
                fields(patch,('path','sourceSHA256','targetSHA256','replacementBase64'),('compiledCaches',))
                relative(patch['path']); _sha(patch['sourceSHA256']); _sha(patch['targetSHA256'])
                source=next((f for f in fingerprints if f['path']==patch['path']),None)
                if patch['path'] in patched or not source or source['role'] not in ROLES-{'entry'} or not patch['path'].endswith('.py') or source['sha256']!=patch['sourceSHA256']:
                    raise APIError(400,'Patch must bind a reviewed non-entry Python runtime file')
                try: data=base64.b64decode(patch['replacementBase64'],validate=True)
                except (ValueError,TypeError): raise APIError(400,'Invalid reviewed patch encoding') from None
                if len(data)>512*1024 or hashlib.sha256(data).hexdigest()!=patch['targetSHA256']: raise APIError(400,'Reviewed patch target digest mismatch')
                caches = patch.get('compiledCaches', [])
                if not isinstance(caches, list) or len(caches) > 16:
                    raise APIError(400, 'Invalid reviewed compiled caches')
                cache_paths = set()
                module = PurePosixPath(patch['path'])
                for cache in caches:
                    fields(cache, ('path', 'sha256'))
                    path = PurePosixPath(relative(cache['path']))
                    _sha(cache['sha256'])
                    if path.parent != module.parent / '__pycache__' or not re.fullmatch(
                        re.escape(module.stem) + r'\.cpython-[0-9]+(?:\.opt-[12])?\.pyc', path.name
                    ) or path.as_posix().casefold() in names | cache_paths:
                        raise APIError(400, 'Compiled cache must belong to its reviewed module')
                    cache_paths.add(path.as_posix().casefold())
                patched.add(patch['path'])
            for item in fingerprints:
                required_patch = LOCATION_PATCHES.get(item['sha256']) if item['role'] == 'locations' else None
                if required_patch and not any(
                    p['path'] == item['path'] and p['targetSHA256'] == required_patch
                    for p in variant.get('patches', [])
                ):
                    raise APIError(400, 'RenPy secondary save locations require the approved runtime patch')
            variant['entrySHA256']=next(f['sha256'] for f in fingerprints if f['role']=='entry')
        profile['rulesSHA256']=digest(dict(adapter=ADAPTER,relativePath=RELATIVE,compatibilityKey=profile['compatibilityKey'],
            variants=[dict(id=v['id'],savePaths=v['savePaths'],legacySavePaths=v['legacySavePaths']) for v in variants]))
        profile['evidenceSHA256']=digest(profile)
        catalog[profile['id']]=profile
    return catalog


def load_catalog(state):
    path=state/CATALOG
    try: fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError: return {}
    except OSError: raise APIError(503,'Unsafe trusted RenPy review manifest') from None
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_uid!=os.geteuid() or stat.S_IMODE(before.st_mode)!=0o600 or before.st_size>2*1024*1024:
            raise APIError(503,'Trusted RenPy review manifest requires server ownership and mode 0600')
        raw=os.read(fd,2*1024*1024+1); after=os.fstat(fd)
        if len(raw)!=before.st_size or (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns): raise APIError(503,'Trusted review manifest changed while reading')
        def unique(pairs):
            out={}
            for k,v in pairs:
                if k in out: raise ValueError('Duplicate key')
                out[k]=v
            return out
        try: value=json.loads(raw,object_pairs_hook=unique)
        except (ValueError,UnicodeError): raise APIError(400,'Invalid trusted RenPy review manifest') from None
        return validate_catalog(value)
    finally: os.close(fd)


def verify_variant(folder,variant,*,save_paths=None,entries=None,export=False):
    from .workflow import _canonical_relative,_entry_sha256
    from .reviewed_profiles import _check_path
    if entries is not None and not any(e['path']==variant['entryPath'] and e['backend']==variant['entryBackend'] for e in entries): raise APIError(409,'Reviewed RenPy entry is not published')
    if variant['entryPath']=='.' and not export and folder.suffix.lower()!='.app': raise APIError(409,'Dot RenPy entry requires a native app root')
    _canonical_relative(folder,variant['entryPath'],directory=variant['platform']=='macos')
    if save_paths is not None:
        from .privacy import normalize_rules
        reviewed = {rule['path'] for rule in variant['savePaths']}
        published = normalize_rules(save_paths,folder)
        # Discovery also records individual seed files. A file under a reviewed
        # directory adds no coverage, but the directory rule itself is required.
        effective = [rule for rule in published if not (
            rule['kind']=='file' and any(parent.as_posix() in reviewed
                                        for parent in PurePosixPath(rule['path']).parents)
        )]
        if effective!=variant['savePaths']: raise APIError(409,'Publish explicitly reviewed save exclusions before activating namespace')
    for item in variant['fingerprints']:
        expected=item['sha256']
        if export:
            patch=next((p for p in variant.get('patches',[]) if p['path']==item['path']),None)
            if patch:
                _reviewed_bytecode(folder,patch['path'])
                expected=patch['targetSHA256']
        _canonical_relative(folder,item['path']); _check_path(folder,item['path'],required=True)
        if (folder/item['path']).stat().st_size>(128*1024*1024 if item['role']=='entry' else 8*1024*1024): raise APIError(409,'Reviewed code exceeds bound')
        actual=_entry_sha256(folder,dict(path=item['path'],backend='wine'))
        if not hmac.compare_digest(actual,expected): raise APIError(409,'RenPy bootstrap/runtime/config fingerprint changed')


def _reviewed_bytecode(folder,path,approved=(),*,discard_regenerated=False):
    from .reviewed_profiles import _check_path
    from .workflow import _entry_sha256
    allowed = {item['path']: item['sha256'] for item in approved}
    matched = []
    relative_path=PurePosixPath(path); parent=folder/relative_path.parent; stem=relative_path.stem
    for suffix in ('.pyc','.pyo'):
        candidate=parent/(stem+suffix)
        if candidate.exists() or candidate.is_symlink(): raise APIError(409,'Unreviewed compiled runtime cache requires explicit private-cache review')
    cache_relative=str(relative_path.parent/'__pycache__')
    _check_path(folder,cache_relative,directory=True)
    cache=folder/cache_relative
    if cache.exists():
        for count,item in enumerate(cache.iterdir(),1):
            if count>10000: raise APIError(409,'Private runtime cache exceeds bound')
            name=item.name.casefold()
            if name.startswith(stem.casefold()+'.') and name.endswith(('.pyc','.pyo')):
                relative = item.relative_to(folder).as_posix()
                expected = allowed.get(relative)
                if expected is None:
                    raise APIError(409,'Unreviewed compiled runtime cache requires explicit private-cache review')
                _check_path(folder,relative,required=True)
                if item.stat(follow_symlinks=False).st_size > 1024 * 1024 or (
                    not discard_regenerated and not hmac.compare_digest(
                        _entry_sha256(folder,dict(path=relative,backend='wine')),expected)
                ):
                    raise APIError(409,'Reviewed compiled runtime cache changed')
                matched.append(item)
    return matched


def apply_export_patches(folder,variant,user,*,chown=os.chown):
    from .workflow import _canonical_relative,_entry_sha256
    for patch in variant.get('patches',[]):
        _canonical_relative(folder,patch['path']); target=folder/patch['path']
        before=target.stat(follow_symlinks=False)
        actual=_entry_sha256(folder,dict(path=patch['path'],backend='wine'))
        if actual not in (patch['sourceSHA256'],patch['targetSHA256']) or not stat.S_ISREG(before.st_mode):
            raise APIError(409,'Private runtime differs; preserve it for explicit review')
        # A successful game run may regenerate bytecode despite launcher flags.
        # Discard only declared derived caches after exact private source validation;
        # never execute them or relax verification of the original NAS source.
        caches = _reviewed_bytecode(folder,patch['path'],patch.get('compiledCaches', ()),
            discard_regenerated=actual==patch['targetSHA256'])
        if actual==patch['targetSHA256']:
            for cache in caches:
                cache.unlink()
            continue
        if actual!=patch['sourceSHA256'] or not stat.S_ISREG(before.st_mode): raise APIError(409,'Private runtime differs; preserve it for explicit review')
        data=base64.b64decode(patch['replacementBase64'],validate=True)
        temporary=target.with_name('.gameshelf-reviewed-'+uuid.uuid4().hex)
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,'wb') as out: out.write(data); out.flush(); os.fsync(out.fileno())
            chown(temporary,user['uid'],user['uid']); os.chmod(temporary,stat.S_IMODE(before.st_mode))
            current=target.stat(follow_symlinks=False)
            if (current.st_ino,current.st_size,current.st_mtime_ns)!=(before.st_ino,before.st_size,before.st_mtime_ns): raise APIError(409,'Private runtime changed during reviewed patch')
            os.replace(temporary,target)
            for cache in caches:
                cache.unlink()
        finally:
            temporary.unlink(missing_ok=True)


def _allow_windows_image(path, *, entry=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as image:
        info = os.fstat(image.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise APIError(409, 'Invalid Windows runtime image in account view')
        if not entry:
            header = image.read(64)
            if len(header) != 64 or header[:2] != b'MZ':
                return
            offset = int.from_bytes(header[60:64], 'little')
            if not 64 <= offset <= info.st_size - 4:
                return
            image.seek(offset)
            if image.read(4) != b'PE\0\0':
                return
        if not info.st_mode & stat.S_IXUSR:
            os.fchmod(image.fileno(), stat.S_IMODE(info.st_mode) | stat.S_IXUSR)


def prepare_windows_entry(entry):
    """Allow the launcher and its runtime PE modules in the private account view."""
    _allow_windows_image(entry, entry=True)
    directories = [entry.parent]
    player = entry.parent / 'UnityPlayer.dll'
    mono = entry.parent / 'MonoBleedingEdge'
    runtime = mono / 'EmbedRuntime'
    if not player.is_symlink() and player.is_file() and not mono.is_symlink():
        if not runtime.is_symlink() and runtime.is_dir():
            directories.append(runtime)
    for directory in directories:
        for module in directory.iterdir():
            if module.suffix.lower() in {'.dll', '.pyd'} and not module.is_symlink() and module.is_file():
                _allow_windows_image(module)


def prepare_windows_images(folder, variant):
    """Prepare Windows images in the private Ren'Py view."""
    if variant['platform'] != 'windows':
        return

    def raise_walk_error(error):
        raise error

    entry = folder / variant['entryPath']
    prepare_windows_entry(entry)

    library = entry.parent / 'lib'
    if library.is_symlink() or not library.is_dir():
        return
    for runtime in library.glob('py3-windows-*'):
        if runtime.is_symlink() or not runtime.is_dir():
            continue
        for directory, _, files in os.walk(runtime, followlinks=False, onerror=raise_walk_error):
            for name in files:
                path = Path(directory) / name
                if path.suffix.lower() in {'.exe', '.dll', '.pyd'} and not path.is_symlink():
                    _allow_windows_image(path)
