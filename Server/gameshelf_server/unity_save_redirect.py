"""Reviewed save redirection for the supported CYANBRAIN Mono release."""

import hashlib
import os
from pathlib import Path
import stat

from .common import APIError
from .reviewed_profiles import _check_path

ENTRY = 'CYANBRAIN.exe'
ASSEMBLY = 'CYANBRAIN_Data/Managed/Assembly-CSharp.dll'
ASSEMBLY_SIZE = 388608
ENTRY_HASH = '92810ee3c5efabda03ec4d98c65a775fc7d42a500497c265dbf47e0fd873d06f'
SOURCE_HASH = '98a6fcbe592f09c2ece50a35c7ec3cd3900cc56ddb00354140dd072722ae5901'
PATCHED_HASH = 'fb68691f9cd2703041ac504418ebde36f9d47ba6c896320fff77922f3a71b907'
SAVE_TARGETS = (('saves', True), ('GameConfig.cfg', False), ('GameCollection.clc', False))


def _verified_bytes(root: Path, relative: str, size: int, sha256: str) -> bytes:
    _check_path(root, relative, required=True)
    fd = os.open(root / relative, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != size:
            raise ValueError('Unexpected image size')
        data = file.read(size + 1)
    if hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError('Unexpected image fingerprint')
    return data


def prepare(lower: Path, view: Path) -> bytes:
    try:
        _verified_bytes(view, ENTRY, 666624, ENTRY_HASH)
        source = _verified_bytes(lower, ASSEMBLY, ASSEMBLY_SIZE, SOURCE_HASH)
        _verified_bytes(view, ASSEMBLY, ASSEMBLY_SIZE, SOURCE_HASH)
        for path, directory in SAVE_TARGETS:
            _check_path(lower, path, directory=directory)
            if (lower / path).exists():
                raise ValueError('Shared source contains a redirected save target')
            _check_path(view, path, directory=directory)
        data = bytearray(source)
        # Replace the five calls to Unity's persistentDataPath getter with the
        # assembly's existing empty-string literal. The broker starts this EXE
        # with the private game view as its working directory.
        original = bytes.fromhex('28 18 01 00 0a')
        replacement = bytes.fromhex('72 0d 0a 00 70')
        for offset in (23563, 25252, 25497, 125028, 147220):
            if data[offset:offset + 5] != original:
                raise ValueError('Unexpected save-path instruction')
            data[offset:offset + 5] = replacement
        if hashlib.sha256(data).hexdigest() != PATCHED_HASH:
            raise ValueError('Unexpected patched assembly fingerprint')
        return bytes(data)
    except (APIError, OSError, ValueError) as error:
        raise APIError(409, 'CYANBRAIN Windows 存档重定向与当前发行版或工作区不匹配。') from error
