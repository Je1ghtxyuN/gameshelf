"""Proof-file storage for a dedicated read-only Samba verification share."""

from contextlib import contextmanager
from configparser import ConfigParser
import fcntl
import os
from pathlib import Path
import re
import stat
import tempfile
import time


class ProofFiles:
    def __init__(self, root, *, owner=0):
        self.root = Path(root)
        self.owner = owner

    @contextmanager
    def _directory(self):
        if not self.root.is_absolute() or self.root.resolve(strict=True) != self.root:
            raise ValueError('Enrollment share must be a dedicated absolute directory')
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if info.st_uid != self.owner or stat.S_IMODE(info.st_mode) != 0o711:
                raise ValueError('Enrollment share must be a host-owned 0711 directory')
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def _filename(challenge):
        if not isinstance(challenge, str) or not re.fullmatch('[a-f0-9]{32}', challenge):
            raise ValueError('Invalid enrollment challenge')
        return challenge + '.proof'

    def publish(self, uid, challenge, proof):
        name = self._filename(challenge)
        if type(uid) is not int or not 0 < uid <= 2000000000 or uid == 65534:
            raise ValueError('Invalid enrollment identity')
        if not isinstance(proof, str) or not re.fullmatch('[a-f0-9]{64}', proof):
            raise ValueError('Invalid enrollment proof')
        with self._directory() as directory:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, 'w', encoding='ascii') as file:
                    file.write(proof)
                    file.flush()
                    os.fchmod(file.fileno(), 0o400)
                    os.fchown(file.fileno(), uid, -1)
            except BaseException:
                os.unlink(name, dir_fd=directory)
                raise
        return name

    def remove(self, challenge):
        name = self._filename(challenge)
        with self._directory() as directory:
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass

    def purge_expired(self, *, now=None):
        cutoff = (time.time() if now is None else now) - 600
        with self._directory() as directory, os.scandir(directory) as entries:
            for entry in entries:
                if not re.fullmatch(r'[a-f0-9]{32}[.]proof',entry.name):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode) and info.st_mtime <= cutoff:
                        os.unlink(entry.name,dir_fd=directory)
                except FileNotFoundError:
                    pass


def workspace_share(root, account, smb_user, workspace_user):
    """Build a deterministic Samba share from two already-verified identities."""
    root = Path(root)
    if (root == Path('/') or not root.is_absolute() or '..' in root.parts
            or not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(root))):
        raise ValueError('Invalid Samba workspace root')
    if not isinstance(account, str) or not re.fullmatch('[a-f0-9]{32}', account):
        raise ValueError('Invalid GameShelf workspace account')
    if not all(isinstance(user, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', user)
               for user in (smb_user, workspace_user)):
        raise ValueError('Invalid Samba workspace identity')
    return 'gs-workspace-'+account, {
        'path': str(root/account),
        'valid users': smb_user,
        'force user': workspace_user,
        'guest ok': 'no',
        'read only': 'no',
        'browseable': 'no',
        'wide links': 'no',
        'admin users': '',
        'force group': '',
        'smb encrypt': 'required',
        'create mask': '0600',
        'force create mode': '0600',
        'directory mask': '0700',
        'force directory mode': '0700',
    }


@contextmanager
def _staged_config(directory, content):
    fd, name = tempfile.mkstemp(prefix='.gameshelf-samba-', dir=directory)
    path = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        yield path
    finally:
        path.unlink(missing_ok=True)


def install_workspace_share(include, root, account, smb_user, workspace_user, validate, reload, *, owner=0):
    """Update only the dedicated include, with host-supplied full-config preflight."""
    share, options = workspace_share(root, account, smb_user, workspace_user)
    include = Path(include)
    parent = include.parent
    if not include.is_absolute() or parent.resolve(strict=True) != parent:
        raise ValueError('Use an absolute, private Samba include directory')
    directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Samba include directory must be host-owned and private')
        fcntl.flock(directory, fcntl.LOCK_EX)
        fd = os.open(include.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, encoding='utf-8') as existing:
            info = os.fstat(existing.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('Samba include must be a host-owned private file')
            original = existing.read(1048577)
        if len(original) > 1048576:
            raise ValueError('Managed Samba include exceeds its size limit')
        parsed = ConfigParser(interpolation=None)
        parsed.read_string(original)
        for name in parsed.sections():
            if not re.fullmatch('gs-workspace-[a-f0-9]{32}', name):
                raise ValueError('Unexpected section in managed Samba include')
        if share in parsed:
            if dict(parsed[share]) != options:
                raise ValueError('Existing Samba share does not match the verified identity')
            validate(include)
            reload()
            return share
        stanza = '\n['+share+']\n'+''.join(f'{key} = {value}\n' for key, value in options.items())
        with _staged_config(parent, original+stanza) as candidate, _staged_config(parent, original) as backup:
            validate(candidate)
            os.replace(candidate, include)
            try:
                reload()
            except Exception:
                os.replace(backup, include)
                reload()
                raise
        return share
    finally:
        os.close(directory)


def validate_samba_candidate(config, include, candidate, export_root, query, *, legacy_mapping=None, accounts=None):
    """Compare effective Samba configuration with and without managed shares."""
    from .driver import mount_table, physical_overlap
    from .workspace_export import validate_exports, validate_share_boundaries

    config, include, candidate, export_root = map(Path,(config,include,candidate,export_root))
    main = config.read_text(encoding='utf-8')
    directive = re.compile(r'^[ \t]*include[ \t]*=[ \t]*'+re.escape(str(include))+r'[ \t]*$',
                           re.IGNORECASE | re.MULTILINE)
    baseline, count = directive.subn('',main)
    if count != 1:
        raise ValueError('Samba main configuration must include the managed file exactly once')
    proposed = directive.sub(lambda _: 'include = '+str(candidate),main)

    def sections(path):
        names = re.findall(r'^\[([^\]]+)\][ \t]*$',query(['testparm','-s',str(path)]),re.MULTILINE)
        return {name.casefold():name for name in names if name.casefold() != 'global'}

    def parameter(path, section, key):
        return query(['testparm','-s','--section-name='+section,'--parameter-name='+key,str(path)]).strip()

    intended = ConfigParser(interpolation=None)
    intended.read_string(candidate.read_text(encoding='utf-8'))
    managed = {name.casefold():name for name in intended.sections()}
    with _staged_config(include.parent,baseline) as original, _staged_config(include.parent,proposed) as staged:
        existing = sections(original)
        if existing.keys() & managed.keys():
            raise ValueError('Managed workspace conflicts with an existing Samba share')
        current = sections(staged)
        if current.keys() != existing.keys() | managed.keys():
            raise ValueError('Effective Samba share set changed unexpectedly')
        for key, name in managed.items():
            for option, expected in intended[name].items():
                actual = parameter(staged,current[key],option)
                if option in ('guest ok','read only','browseable','wide links','smb encrypt'):
                    actual = actual.casefold()
                if actual != expected:
                    raise ValueError('Unexpected effective Samba setting: '+option)
        shares = {name:parameter(staged,name,'path') for name in current.values()}
        reviewed = {name.removeprefix('gs-workspace-'):name for name in intended.sections()}
        if legacy_mapping:
            def unix_uid(name):
                fields = query(['getent','passwd',name]).strip().split(':')
                return int(fields[2]) if len(fields) == 7 and fields[0] == name and fields[2] == fields[3] else None

            legacy = validate_exports(
                legacy_mapping,accounts,export_root,
                lambda share, option: parameter(staged,share,option),unix_uid)
            legacy_users = {item['smbUser'].casefold() for item in legacy_mapping}
            managed_users = {intended[name]['valid users'].casefold() for name in managed.values()}
            if (legacy.keys() & reviewed.keys() or legacy_users & managed_users
                    or any(share.casefold() not in existing for share in legacy.values())):
                raise ValueError('Managed workspace conflicts with a legacy account or SMB identity')
            reviewed.update(legacy)
        table = mount_table()

        def overlaps_export(export, share):
            return physical_overlap(export,share,table) or any(
                share in Path(row['mount']).parents and physical_overlap(export,Path(row['mount']),table)
                for row in table)

        validate_share_boundaries(export_root,reviewed,shares,overlaps_export)


def resolve_samba_identity(username, eligible_gid, config, query):
    """Resolve a local passdb identity without reading password hashes."""
    name_pattern = r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}'
    if (not isinstance(username, str) or not re.fullmatch(name_pattern,username)
            or type(eligible_gid) is not int or not 0 < eligible_gid <= 2000000000):
        raise ValueError('Invalid Samba enrollment identity lookup')
    response = query(['pdbedit','-L','-v','-u',username,'--configfile='+str(config)])
    fields = {}
    expected = {'unix username','user sid','account flags'}
    for line in response.splitlines():
        key, separator, value = line.partition(':')
        key = key.strip().casefold()
        if separator and key in expected:
            if key in fields:
                raise ValueError('Ambiguous Samba account')
            fields[key] = value.strip()
    name = fields.get('unix username','')
    sid = fields.get('user sid','')
    flags = fields.get('account flags','')
    if (not re.fullmatch(name_pattern,name) or name.casefold() != username.casefold()
            or not re.fullmatch(r'S-1-5-21(?:-[0-9]{1,10}){4}',sid)
            or not re.fullmatch(r'\[[ A-Z]+\]',flags)
            or 'U' not in flags or set(flags) & set('DNLTWSI')):
        raise ValueError('Samba account is not eligible for enrollment')
    unix = query(['getent','passwd',name]).strip().split(':')
    groups = query(['id','-G',name]).split()
    if (len(unix) != 7 or unix[0] != name or not re.fullmatch('[0-9]{1,10}',unix[2])
            or not 1000 <= int(unix[2]) <= 2000000000 or int(unix[2]) == 65534
            or '0' in groups or str(eligible_gid) not in groups):
        raise ValueError('Unix account is not eligible for Samba enrollment')
    return name, int(unix[2]), sid


def prepare_workspace_directory(root, account, uid):
    """Create a traversal-only per-account export root without altering an existing one."""
    from .workspace_export import WorkspaceExportMounts

    root = Path(root)
    if (not root.is_absolute() or root.resolve(strict=True) != root
            or not isinstance(account,str) or not re.fullmatch('[a-f0-9]{32}',account)
            or type(uid) is not int or not 20000 <= uid <= 2000000000):
        raise ValueError('Invalid workspace directory identity')
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o711:
            raise ValueError('Workspace root must be a host-owned 0711 directory')
        try:
            os.mkdir(account, mode=0, dir_fd=directory)
        except FileExistsError:
            pass
        else:
            try:
                fd = os.open(account, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    os.fchown(fd, 0, uid)
                    os.fchmod(fd, 0o710)
                finally:
                    os.close(fd)
            except BaseException:
                os.rmdir(account, dir_fd=directory)
                raise
        WorkspaceExportMounts(root,{},None,None)._account_directory(account,uid)
    finally:
        os.close(directory)


class SambaEnrollmentHost:
    """Host-only enrollment operations with administrator-owned configuration."""

    def __init__(self, *, config, include, export_root, proof_root, eligible_gid, workspace_identity, query, legacy_exports=None):
        self.config = Path(config)
        self.include = Path(include)
        self.export_root = Path(export_root)
        self.proofs = ProofFiles(proof_root)
        self.eligible_gid = eligible_gid
        self.workspace_identity = workspace_identity
        self.query = query
        self.legacy_exports = legacy_exports

    def resolve(self, username):
        name, uid, sid = resolve_samba_identity(username,self.eligible_gid,self.config,self.query)
        return f'{name}:{uid}:{sid}'

    def _verify_current_identity(self, principal):
        if not isinstance(principal,str) or principal.count(':') != 2:
            raise ValueError('Invalid resolved SMB identity')
        name, uid, sid = principal.split(':')
        if self.resolve(name) != principal:
            raise ValueError('SMB identity changed after verification')
        return name,int(uid)

    def deliver(self, principal, challenge, proof):
        _, uid = self._verify_current_identity(principal)
        self.proofs.publish(uid,challenge,proof)

    def discard(self, challenge):
        self.proofs.remove(challenge)

    def purge_expired(self):
        self.proofs.purge_expired()

    def provision(self, account, principal):
        smb_user, _ = self._verify_current_identity(principal)
        if not isinstance(account,str) or not re.fullmatch('[a-f0-9]{32}',account):
            raise ValueError('Invalid GameShelf account')
        uid, workspace_user = self.workspace_identity(account)
        prepare_workspace_directory(self.export_root,account,uid)

        def validate(candidate):
            legacy_mapping, accounts = self.legacy_exports() if self.legacy_exports else (None,None)
            validate_samba_candidate(self.config,self.include,candidate,self.export_root,self.query,
                                     legacy_mapping=legacy_mapping,accounts=accounts)

        def reload():
            self.query(['smbcontrol','-s',str(self.config),'smbd','reload-config'])

        return install_workspace_share(self.include,self.export_root,account,smb_user,workspace_user,validate,reload)


def ensure_workspace_user(uid, run):
    """Ensure a non-login host identity with the gateway's existing UID and GID."""
    if type(uid) is not int or not 20000 <= uid <= 2000000000:
        raise ValueError('Invalid workspace UID')
    name = 'gsa'+str(uid)

    def lookup(database, key):
        result = run(['getent',database,str(key)],check=False)
        if result.returncode == 2:
            return None
        if result.returncode != 0 or len(result.stdout.splitlines()) != 1:
            raise OSError('Host identity lookup failed')
        return result.stdout.strip().split(':')

    def verify(database, expected, size, nologin=False):
        by_name = lookup(database,name)
        by_id = lookup(database,uid)
        if by_name is None and by_id is None:
            return False
        if (by_name is None or by_name != by_id or len(by_name) != size
                or by_name[0] != name or by_name[2:2+len(expected)] != expected
                or (nologin and by_name[5:] != ['/nonexistent','/usr/sbin/nologin'])):
            raise ValueError('Host identity conflict for GameShelf workspace')
        return True

    user_id = [str(uid),str(uid)]
    group_id = [str(uid)]
    user_exists = verify('passwd',user_id,7,nologin=True)
    if not verify('group',group_id,4):
        if user_exists:
            raise ValueError('Host group conflict for GameShelf workspace')
        run(['groupadd','--gid',str(uid),name])
    if not user_exists:
        run(['useradd','--no-create-home','--no-user-group','--no-log-init','--uid',str(uid),
             '--gid',name,'--home-dir','/nonexistent','--shell','/usr/sbin/nologin',name])
    if not verify('passwd',user_id,7,nologin=True) or not verify('group',group_id,4):
        raise OSError('Host workspace identity was not created')
    return name


def main():
    import argparse
    from contextlib import closing
    import sqlite3
    import subprocess

    from .smb_enrollment_channel import serve_host

    parser = argparse.ArgumentParser(description='Run the local GameShelf Samba enrollment service')
    for option in ('socket','database','config','include','export-root','proof-root'):
        parser.add_argument('--'+option,type=Path,required=True)
    parser.add_argument('--legacy-mapping',type=Path)
    parser.add_argument('--eligible-group',required=True)
    parser.add_argument('--verification-share',required=True)
    args = parser.parse_args()

    def run(command, *, check=True):
        try:
            return subprocess.run(command,capture_output=True,text=True,check=check,timeout=5,
                                  env={**os.environ,'LC_ALL':'C'})
        except subprocess.SubprocessError:
            raise OSError('Host enrollment operation failed') from None

    def query(command):
        return run(command).stdout

    if os.geteuid() != 0:
        parser.exit(1,'SMB enrollment requires a NAS administrator setup\n')
    if not all(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}',name)
               for name in (args.eligible_group,args.verification_share)):
        parser.exit(1,'Invalid verification group or share name\n')
    group = query(['getent','group',args.eligible_group]).strip().split(':')
    if len(group) != 4 or group[0] != args.eligible_group or not group[2].isdigit() or int(group[2]) <= 0:
        parser.exit(1,'Invalid eligible group\n')

    expected = {'path':str(args.proof_root),'valid users':'@'+args.eligible_group,
                'force user':'','force group':'','admin users':'','guest ok':'no',
                'read only':'yes','wide links':'no','follow symlinks':'no','smb encrypt':'required'}
    for option, value in expected.items():
        actual = query(['testparm','-s','--section-name='+args.verification_share,
                        '--parameter-name='+option,str(args.config)]).strip()
        if option in ('guest ok','read only','wide links','follow symlinks','smb encrypt'):
            actual = actual.casefold()
        if actual != value:
            parser.exit(1,'Unexpected effective verification share setting: '+option+'\n')

    from .driver import mount_table, physical_overlap
    from .workspace_export import validate_share_boundaries

    proof_root = args.proof_root.resolve(strict=True)
    table = mount_table()
    sections = re.findall(r'^\[([^\]]+)\][ \t]*$',query(['testparm','-s',str(args.config)]),re.MULTILINE)
    other_shares = {
        name:query(['testparm','-s','--section-name='+name,'--parameter-name=path',str(args.config)]).strip()
        for name in sections if name.casefold() not in ('global',args.verification_share.casefold())
    }

    def aliases_proof(export, share):
        return physical_overlap(export,share,table) or any(
            share in Path(row['mount']).parents and physical_overlap(export,Path(row['mount']),table)
            for row in table)

    validate_share_boundaries(proof_root,{},other_shares,aliases_proof)

    def workspace_identity(account):
        uri = args.database.resolve(strict=True).as_uri()+'?mode=ro'
        with closing(sqlite3.connect(uri,uri=True,timeout=2)) as db:
            row = db.execute('SELECT uid FROM users WHERE id=? AND disabled=0',(account,)).fetchone()
        if row is None:
            raise ValueError('No enabled GameShelf workspace account')
        return row[0], ensure_workspace_user(row[0],run)

    def legacy_exports():
        import json

        with os.fdopen(os.open(args.legacy_mapping,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK),
                       encoding='utf-8') as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
                raise ValueError('Legacy mapping must be a root-owned 0600 regular file')
            mapping = json.loads(file.read(65537))
        if not isinstance(mapping,list) or not mapping:
            raise ValueError('Legacy mapping must contain explicit account shares')
        uri = args.database.resolve(strict=True).as_uri()+'?mode=ro'
        with closing(sqlite3.connect(uri,uri=True,timeout=2)) as db:
            accounts = dict(db.execute('SELECT id,uid FROM users WHERE disabled=0'))
        return mapping, accounts

    host = SambaEnrollmentHost(config=args.config,include=args.include,export_root=args.export_root,
                              proof_root=args.proof_root,eligible_gid=int(group[2]),
                              workspace_identity=workspace_identity,query=query,
                              legacy_exports=legacy_exports if args.legacy_mapping else None)
    with host.proofs._directory():
        pass
    serve_host(args.socket,host)


if __name__ == '__main__':
    main()
