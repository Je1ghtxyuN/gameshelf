"""Pinned review evidence, not a save-path detector or an admin assertion.

Only the reproducible, dependency-free synthetic PE is currently reviewed.
This does not certify arbitrary RenPy exports or external OS-profile writes.
"""
import copy
import hashlib
import json
import os
import stat
import unicodedata
from .common import APIError

PROFILE_ID='gameshelf.synthetic-relative-windows.v1'
_PROFILE=dict(id=PROFILE_ID,revision=1,entryPath='Fixture.exe',entrySize=1536,
    entrySHA256='23da1a8de2ea9c3aaf4b484b39357c4f41e8c5255f3bddcf5f45610b9003942b',
    savePaths=[dict(path='savedata',kind='directory')],writeTargets=['savedata/progress.txt'],
    platform='windows',scope='game-relative',synthetic=True,crossVariantSharing=False,
    evidence=dict(kind='pinned-synthetic-pe-review',
        generator='Contracts/Fixtures/reviewed-relative-windows/build_fixture.py',
        generatorSHA256='8e66e82ca032adf2b2f1cf6079246534bcf2a9e397e9d0e5f5448b19db90b86a',
        imports=['CreateDirectoryW','CreateFileW','WriteFile','CloseHandle','Sleep','ExitProcess'],
        behavior='Fixed relative savedata/progress.txt write; no runtime/config/helper loading or OS-profile lookup'))
_PROFILE['rulesSHA256']=hashlib.sha256(json.dumps(_PROFILE['savePaths'],sort_keys=True).encode()).hexdigest()
_PROFILE['evidenceSHA256']=hashlib.sha256(json.dumps(_PROFILE,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def get_profile(profile_id):
    if not isinstance(profile_id,str): raise APIError(400,'reviewedProfileID must be a string')
    if profile_id!=PROFILE_ID: raise APIError(404,'Reviewed launch profile not found')
    return copy.deepcopy(_PROFILE)


def profiles():
    return [get_profile(PROFILE_ID)]


def _check_path(root,relative,*,directory=False,required=False):
    """Static reviewed paths only; reject links, special files and Windows aliases."""
    current=root
    parts=relative.split('/')
    for index,part in enumerate(parts):
        try:
            names=[]
            with os.scandir(current) as items:
                for count,item in enumerate(items,1):
                    if count>10000: raise APIError(409,'Reviewed path directory exceeds entry limit')
                    if unicodedata.normalize('NFC',item.name).casefold()==part.casefold(): names.append(item.name)
            if len(names)>1 or (names and names[0]!=part):
                raise APIError(409,'Reviewed path has a case or Unicode alias')
            current=current/part
            if not names:
                if required: raise APIError(409,'Reviewed path is missing from account view')
                return
            info=current.lstat()
            if stat.S_ISLNK(info.st_mode): raise APIError(409,'Reviewed save/entry path cannot be a symlink')
            want_directory=index<len(parts)-1 or directory
            if want_directory and not stat.S_ISDIR(info.st_mode): raise APIError(409,'Reviewed directory has wrong type')
            if not want_directory and not stat.S_ISREG(info.st_mode): raise APIError(409,'Reviewed file has wrong type')
        except OSError:
            raise APIError(409,'Reviewed path is unavailable') from None


def _verify_paths_and_entry(root,profile,*,export):
    from .workflow import _canonical_relative, _entry_sha256
    # Descriptor-relative existing workflow helper validates the root ancestors
    # and the exact entry without following links, with bounded hash reads.
    if _canonical_relative(root,profile['entryPath'])!=profile['entryPath']:
        raise APIError(409,'Reviewed entry spelling changed')
    _check_path(root,profile['entryPath'],required=True)
    if (root/profile['entryPath']).stat().st_size!=profile['entrySize']:
        raise APIError(409,'Reviewed entry size changed')
    actual=_entry_sha256(root,dict(path=profile['entryPath'],backend='wine'))
    if actual!=profile['entrySHA256']: raise APIError(409,'Reviewed entry fingerprint mismatch')
    for rule in profile['savePaths']:
        _check_path(root,rule['path'],directory=True,required=export)
    for target in profile['writeTargets']:
        _check_path(root,target)


def verify_source(root,profile,rules,entries):
    from .privacy import normalize_rules
    if not any(entry.get('path')==profile['entryPath'] for entry in entries):
        raise APIError(409,'Reviewed entry is not a published launcher')
    if normalize_rules(rules,root)!=profile['savePaths']:
        raise APIError(409,'Published save rules do not match the reviewed profile')
    _verify_paths_and_entry(root,profile,export=False)


def verify_export(root,profile):
    _verify_paths_and_entry(root,profile,export=True)


def launch_evidence(profile):
    return dict(entryPath=profile['entryPath'],isolation='reviewed-relative-saves',saveProfileVerified=True,
        reviewedProfileID=profile['id'],profileRevision=profile['revision'],entrySHA256=profile['entrySHA256'],
        rulesSHA256=profile['rulesSHA256'],evidenceSHA256=profile['evidenceSHA256'],
        sourceSignature=profile['sourceSignature'],savePaths=copy.deepcopy(profile['savePaths']),synthetic=profile['synthetic'])
