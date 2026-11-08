"""Private namespace storage only. No copying/adoption of existing saves."""
import json
import os
from pathlib import Path
import re
import stat
from .common import APIError
from .save_profiles import ADAPTER,RELATIVE


def _id(value):
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value): raise APIError(409,'Invalid namespace identity')
    return value


def validate_context(lease,context):
    if context['adapter']!=ADAPTER or context['relativePath']!=RELATIVE: raise APIError(409,'Unsupported private namespace adapter')
    for key in ('serverID','userID','canonicalID'): _id(context[key])
    if context['userID']!=lease['user_id'] or context['canonicalID']!=lease['canonical_id']: raise APIError(409,'Namespace lease identity mismatch')
    import hashlib
    suffix=hashlib.sha256(context['compatibilityKey'].encode()).hexdigest()
    expected='/'.join((context['serverID'],context['userID'],context['canonicalID'],suffix))
    if context['key']!=expected: raise APIError(409,'Namespace key mismatch')
    return suffix


def _empty(upper,path):
    from .reviewed_profiles import _check_path
    _check_path(upper,path,directory=True)
    target=upper/path
    if target.exists() and next(target.iterdir(),None) is not None:
        raise APIError(409,'Existing private saves require explicit backup/adoption before namespace launch')


def _check_data(data):
    count=0
    for directory,dirs,files in os.walk(data,followlinks=False):
        if len(Path(directory).relative_to(data).parts)>32: raise APIError(409,'Namespace depth exceeds limit')
        for name in dirs+files:
            count+=1; item=Path(directory)/name; info=item.lstat()
            if count>10000 or stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)) or (stat.S_ISREG(info.st_mode) and info.st_nlink!=1):
                raise APIError(409,'Unsafe namespace data; preserve it for explicit recovery')


def prepare_namespace(state,lease,user,context,upper_for_game,*,chown=os.chown):
    from .service import clean_path
    suffix=validate_context(lease,context)
    # Check ALL canonical editions, including unreviewed historical workspaces.
    # No new directory/manifest is created before this whole preflight succeeds.
    for edition in context['legacyVariants']:
        upper=clean_path(upper_for_game(edition['id']))
        if upper.exists():
            if not upper.is_dir(): raise APIError(409,'Unsafe legacy private workspace')
            if edition['unknown'] and next(upper.iterdir(),None) is not None:
                raise APIError(409,'Unknown legacy private workspace requires explicit save adoption')
            for rule in edition['legacySavePaths']: _empty(upper,rule['path'])
            _empty(upper,RELATIVE)
    base=clean_path(state/'save-namespaces'/context['serverID']/context['userID']/context['canonicalID']/suffix)
    expected={k:context[k] for k in ('serverID','userID','canonicalID','compatibilityKey','reviewedProfileID','revision','rulesSHA256','evidenceSHA256')}
    expected['schemaVersion']=1; data=base/'data'; manifest=base/'namespace.json'
    if base.exists():
        if not base.is_dir() or base.stat().st_uid!=os.geteuid() or stat.S_IMODE(base.stat().st_mode)!=0o700:
            raise APIError(409,'Unsafe namespace owner/directory')
        try: fd=os.open(manifest,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        except OSError: raise APIError(409,'Uninitialized namespace requires explicit recovery') from None
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>16384:
                raise APIError(409,'Unsafe namespace manifest')
            try: stored=json.loads(os.read(fd,16385))
            except (ValueError,UnicodeError): raise APIError(409,'Invalid namespace manifest') from None
            if stored!=expected: raise APIError(409,'Namespace manifest differs; explicit migration required')
        finally: os.close(fd)
        clean_path(data)
        if not data.is_dir() or data.stat().st_uid!=user['uid'] or stat.S_IMODE(data.stat().st_mode)!=0o700:
            raise APIError(409,'Unsafe namespace data owner/directory')
        _check_data(data)
    else:
        parent=state
        for name in ('save-namespaces',context['serverID'],context['userID'],context['canonicalID']):
            parent=parent/name; clean_path(parent); parent.mkdir(mode=0o700,exist_ok=True)
            info=parent.stat()
            if info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700: raise APIError(409,'Unsafe private namespace ancestor')
        base.mkdir(mode=0o700); data.mkdir(mode=0o700); chown(data,user['uid'],user['uid'])
        fd=os.open(manifest,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as out: json.dump(expected,out,sort_keys=True); out.flush(); os.fsync(out.fileno())
        fd=os.open(base,os.O_RDONLY|os.O_DIRECTORY)
        try: os.fsync(fd)
        finally: os.close(fd)
    for name in ('Auxiliary','MultiPersistent'):
        child=clean_path(data/name)
        if child.exists() and not child.is_dir(): raise APIError(409,'Unsafe fixed RenPy environment directory')
        child.mkdir(mode=0o700,exist_ok=True); chown(child,user['uid'],user['uid'])
    return data
