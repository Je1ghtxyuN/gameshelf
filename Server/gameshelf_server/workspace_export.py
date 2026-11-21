"""Read-only preflight for reusing NAS SMB authentication on account exports."""

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess


def validate_exports(exports, accounts, export_root, setting, unix_uid):
    """Validate effective share identity and its match to stored workspace UID."""
    root = Path(export_root)
    if not root.is_absolute() or root == Path('/') or '..' in root.parts:
        raise ValueError('Use a dedicated absolute workspace export root')
    if not isinstance(exports, list) or not exports:
        raise ValueError('Provide at least one workspace account mapping')

    result = {}
    smb_users = set()
    shares = set()
    for item in exports:
        if not isinstance(item, dict) or set(item) != {'accountID', 'smbUser', 'share', 'unixUser'}:
            raise ValueError('Invalid workspace account mapping')
        account, smb_user, share, unix_user = (item[key] for key in ('accountID', 'smbUser', 'share', 'unixUser'))
        if not isinstance(account, str) or not re.fullmatch('[a-f0-9]{32}', account):
            raise ValueError('Invalid workspace account identifier')
        names = (smb_user, share, unix_user)
        if not all(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', name) for name in names):
            raise ValueError('Use explicit SMB and Unix names')
        expected_uid = accounts.get(account)
        if (type(expected_uid) is not int or not 20000 <= expected_uid <= 2000000000
                or unix_uid(unix_user) != expected_uid):
            raise ValueError('Export Unix identity must match the existing GameShelf workspace UID')
        if account in result or smb_user.lower() in smb_users or share.lower() in shares:
            raise ValueError('Each workspace needs its own account, authenticated SMB user and share')
        expected = {
            'path': str(root/account),
            'valid users': smb_user,
            'force user': unix_user,
            'guest ok': 'no',
            'read only': 'no',
            'wide links': 'no',
            'force group': '',
            'admin users': '',
        }
        for parameter, value in expected.items():
            actual = setting(share, parameter).strip()
            if parameter in ('guest ok', 'read only', 'wide links'):
                actual = actual.lower()
            if actual != value:
                raise ValueError('Unexpected effective Samba setting: '+parameter)
        result[account] = share
        smb_users.add(smb_user.lower())
        shares.add(share.lower())
    return result


def validate_share_boundaries(root, reviewed, share_paths, physical_overlap):
    """Reject another share that can reach the account export tree."""
    reviewed_paths = {share.casefold(): root/account for account, share in reviewed.items()}
    for share, value in share_paths.items():
        path = Path(value)
        if not path.is_absolute() or '%' in value or '..' in path.parts:
            raise ValueError('NAS shares must have static absolute paths for workspace preflight')
        if share.casefold() in reviewed_paths:
            if path != reviewed_paths[share.casefold()]:
                raise ValueError('Account share path changed during preflight')
            continue
        path = path.resolve()
        if path == root or path in root.parents or root in path.parents or physical_overlap(root, path):
            raise ValueError('Another NAS share overlaps the dedicated workspace export')


class WorkspaceExportMounts:
    """Bind only the active account view into a dedicated NAS export mount."""

    def __init__(self, root, shares, run, mount_probe):
        self.root = Path(root)
        self.shares = dict(shares)
        self.run = run
        self.mount_probe = mount_probe

    def _target(self, account, lease):
        if not all(isinstance(value, str) and re.fullmatch('[a-f0-9]{32}', value)
                   for value in (account, lease)):
            raise ValueError('Invalid mounted workspace identifier')
        target = self.root/account/lease
        if not target.is_absolute() or target.resolve() != target:
            raise ValueError('Mounted workspace paths cannot use symbolic links')
        return target

    def _account_directory(self, account, uid):
        directory = self.root/account
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != uid
                or stat.S_IMODE(info.st_mode) != 0o710):
            raise ValueError('Account export must be root-owned with workspace-group traversal')
        return directory

    def register(self, account, uid, share):
        if not re.fullmatch('[a-f0-9]{32}',account) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',share):
            raise ValueError('Invalid workspace share registration')
        existing = self.shares.get(account)
        if ((existing is not None and existing != share)
                or any(key != account and value.casefold() == share.casefold()
                       for key,value in self.shares.items())):
            raise ValueError('Conflicting workspace share registration')
        self._account_directory(account,uid)
        self.shares[account] = share

    def publish(self, account, uid, lease, source):
        share = self.shares[account]
        target = self._target(account, lease)
        self._account_directory(account, uid)
        if not self.mount_probe(source):
            raise RuntimeError('Account workspace is not mounted')
        target.mkdir(mode=0)
        self.run(['mount', '--rbind', str(source), str(target)])
        if not self.mount_probe(target):
            raise RuntimeError('Account workspace export mount could not be verified')
        return dict(share=share, relative=lease)

    def revoke(self, account, lease):
        target = self._target(account, lease)
        if self.mount_probe(target):
            self.run(['umount', '--recursive', str(target)])
            if self.mount_probe(target):
                raise RuntimeError('Account workspace export is still mounted')
        if target.exists():
            target.rmdir()

def load_runtime_exports(root, manifest, accounts, protected_paths, run, mount_probe, *, allow_empty=False):
    from .driver import mount_table, physical_overlap
    from .service import overlaps

    root = Path(root)
    table = mount_table()
    if (not root.is_absolute() or root == Path('/') or root.resolve(strict=True) != root
            or any(overlaps(root, path) or physical_overlap(root, path, table) for path in protected_paths)):
        raise ValueError('Workspace export must be separate from gateway state and game sources')
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o711:
        raise ValueError('Workspace export root must be a root-owned 0711 directory')
    propagation = run(['findmnt', '-n', '-o', 'PROPAGATION', '--mountpoint', str(root)]).stdout.strip()
    if 'shared' not in propagation.split(',') or not mount_probe(root):
        raise ValueError('Workspace export root must be a shared mount')

    with os.fdopen(os.open(manifest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), encoding='utf-8') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('Workspace manifest must be a root-owned 0600 regular file')
        data = file.read(65537)
    if len(data) > 65536:
        raise ValueError('Workspace manifest exceeds maximum size')
    shares = json.loads(data)
    if (not isinstance(shares, dict) or (not shares and not allow_empty)
            or any(account not in accounts or not re.fullmatch('[a-f0-9]{32}', account)
                   or not isinstance(share, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', share)
                   for account, share in shares.items())
            or len({share.lower() for share in shares.values()}) != len(shares)):
        raise ValueError('Invalid workspace export manifest')
    mounts = WorkspaceExportMounts(root, shares, run, mount_probe)
    for account in shares:
        mounts._account_directory(account, accounts[account])
    return mounts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--mapping', type=Path, required=True)
    parser.add_argument('--export-root', type=Path, required=True)
    parser.add_argument('--smb-conf', type=Path, default=Path('/etc/samba/smb.conf'))
    parser.add_argument('--emit-manifest', action='store_true')
    args = parser.parse_args()

    def query(command):
        try:
            return subprocess.run(command, capture_output=True, text=True,
                                  check=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError('Could not inspect the effective NAS configuration') from error

    def setting(share, parameter):
        return query(['testparm', '-s', '--section-name='+share,
                      '--parameter-name='+parameter, str(args.smb_conf.resolve())])

    def unix_uid(name):
        account = query(['getent', 'passwd', name]).split(':')
        return int(account[2]) if len(account) == 7 and account[0] == name and account[2] == account[3] else None

    try:
        with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro', uri=True) as database:
            accounts = dict(database.execute('SELECT id,uid FROM users WHERE disabled=0'))
        exports = json.loads(args.mapping.read_text(encoding='utf-8'))
        result = validate_exports(exports, accounts, args.export_root, setting, unix_uid)
        root = args.export_root
        if root != root.resolve(strict=True):
            raise ValueError('Workspace export root cannot use a symlink')
        from .driver import mount_table, physical_overlap
        table = mount_table()
        config = query(['testparm', '-s', str(args.smb_conf.resolve())])
        sections = re.findall(r'^\[([^\]]+)\]\s*$', config, re.MULTILINE)
        share_paths = {name: setting(name, 'path') for name in sections if name.casefold() != 'global'}

        def overlaps_export(export, share):
            candidates = [share, *(Path(row['mount']) for row in table if share in Path(row['mount']).parents)]
            return any(physical_overlap(export, candidate, table) for candidate in candidates)

        validate_share_boundaries(root, result, share_paths, overlaps_export)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o711:
            raise ValueError('Workspace export root must be a root-owned 0711 directory')
        propagation = query(['findmnt', '-n', '-o', 'PROPAGATION', '--mountpoint', str(root)])
        if 'shared' not in propagation.split(','):
            raise ValueError('The dedicated workspace export mount must be shared with the gateway')
        mounts = WorkspaceExportMounts(root, result, query, None)
        for account in result:
            mounts._account_directory(account, accounts[account])
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f'Workspace identity preflight failed: {error}\n')
    if args.emit_manifest:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f'Workspace identity preflight passed for {len(result)} account(s)')


if __name__ == '__main__':
    main()
