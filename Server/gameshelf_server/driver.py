"""Linux-only OverlayFS and a dedicated container Samba. Never use on the NAS host.

The runner and view preparation are injectable boundaries for non-privileged tests.
No command string, shell, password argument, lazy unmount or host config is used.
"""
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import sys
import subprocess
import tempfile
import time

from .service import clean_path, overlaps, tree_signature, link_target

def run(args, *, input=None, check=True):
    # Never include subprocess diagnostics in HTTP responses or logs.
    return subprocess.run(args, input=input, text=True, capture_output=True, check=check, timeout=20)

def mount_table(data=None):
    if data is None:
        if sys.platform!='linux':
            return []
        data=Path('/proc/self/mountinfo').read_text()
    def unescape(value):
        return re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),value)
    result=[]
    for line in data.splitlines():
        fields=line.split()
        if len(fields)>=6:
            result.append(dict(device=fields[2],root=unescape(fields[3]),mount=unescape(fields[4])))
    return result

def is_mountpoint(path):
    # os.path.ismount does not reliably recognize same-device bind mounts.
    absolute=os.path.abspath(path)
    if sys.platform=='linux':
        return any(row['mount']==absolute for row in mount_table())
    return os.path.ismount(path)

def physical_overlap(a,b,table):
    def physical(path):
        candidates=[row for row in table if Path(row['mount'])==path or Path(row['mount']) in path.parents]
        if not candidates:
            return None
        row=max(candidates,key=lambda r:len(Path(r['mount']).parts))
        return row['device'],Path(row['root'])/path.relative_to(row['mount'])
    left,right=physical(a),physical(b)
    return bool(left and right and left[0]==right[0] and overlaps(left[1],right[1]))

def prepare_view(path, unix_uid, source_root):
    """Metadata copy-up with metacopy=on; game contents copy only when modified.

    Lower ownership/mode commonly belongs to a NAS identity. Normalize permissions
    in the private view so the gateway's unprivileged uid can save to those dirs.
    This MUST run only after a successful overlay mount.
    """
    tree_signature(path, source_root=source_root)
    for directory, dirs, files in os.walk(path, followlinks=False):
        for target in [Path(directory)] + [Path(directory)/n for n in dirs + files]:
            if target.is_symlink():
                destination = link_target(target,path,source_root)
                relative = os.path.relpath(destination,target.parent)
                if os.readlink(target) != relative:
                    target.unlink()  # Overlay whiteout/replacement only; lower is read-only.
                    target.symlink_to(relative)
                os.chown(target,unix_uid,unix_uid,follow_symlinks=False)
                continue
            mode = target.stat(follow_symlinks=False).st_mode
            os.chown(target, unix_uid, unix_uid, follow_symlinks=False)
            os.chmod(target, 0o700 if target.is_dir() else 0o600 | (mode & 0o111), follow_symlinks=False)
    profile = path/'.gameshelf-profile'
    profile.mkdir(mode=0o700, exist_ok=True)
    os.chown(profile, unix_uid, unix_uid)

class LinuxDriver:
    def __init__(self, state, host, port=445, *, runner=run, mount_probe=is_mountpoint, prepare_view=prepare_view, chown=os.chown, mz_sync=None, workspace_exports=None):
        if not isinstance(host, str) or not host or any(c.isspace() or c in '/\\@?#' for c in host):
            raise ValueError('Configure an SMB hostname without URL or credentials')
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('Invalid SMB port')
        self.state = clean_path(state)
        self.state.mkdir(parents=True, exist_ok=True, mode=0o711)
        # Searchable but not listable; databases/config remain root-only (0600).
        os.chmod(self.state, 0o711)
        self.host, self.port, self.run = host, port, runner
        self.mount_probe, self.prepare_view, self.chown = mount_probe, prepare_view, chown
        self.mz_sync = mz_sync
        self.workspace_exports = workspace_exports
        self.runtime = self.state/'samba'
        for name in ('private', 'state', 'cache', 'lock', 'pid', 'ncalrpc'):
            (self.runtime/name).mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(self.runtime, 0o700)
        self.config = self.runtime/'smb.conf'
        self.shares = {}
        self.process = None
        self._write_config()
    @staticmethod
    def _id(value):
        if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
            raise ValueError('Invalid internal ID')
        return value
    def names(self, lease):
        lease_id = self._id(lease['id'])
        return 'gs'+lease_id, 'gs'+lease_id[:24]
    def paths(self, lease):
        account = self._id(lease['user_id'])
        game = self._id(lease['game_id'])
        base = self.state/'workspaces'/account/game
        # State is root-managed. Refuse pre-existing links on every operation.
        clean_path(base)
        return {key: base/key for key in ('lower', 'upper', 'work', 'merged')}
    def validate(self, sources):
        fs = self.run(['findmnt', '-n', '-o', 'FSTYPE', '--target', str(self.state)]).stdout.strip()
        if fs != 'ext4':
            raise RuntimeError('Writable state must be a dedicated ext4 bind mount')
        for source in sources:
            source = clean_path(source)
            if overlaps(source, self.state) or physical_overlap(source,self.state,mount_table()):
                raise RuntimeError('Source/state overlap')
            options = self.run(['findmnt', '-n', '-o', 'OPTIONS', '--target', str(source)]).stdout.strip().split(',')
            if 'ro' not in options:
                raise RuntimeError('Source must be mounted read-only')
    def _write_config(self):
        cfg = f'''[global]
server role = standalone server
security = user
server min protocol = SMB3_00
server smb encrypt = required
server multi channel support = no
smb ports = 445
disable netbios = yes
map to guest = Never
restrict anonymous = 2
ntlm auth = ntlmv2-only
load printers = no
printing = bsd
printcap name = /dev/null
usershare max shares = 0
usershare allow guests = no
unix extensions = no
smb3 unix extensions = no
follow symlinks = yes
wide links = no
allow insecure wide links = no
log level = 0
logging = file
log file = /dev/null
passdb backend = tdbsam:{self.runtime}/private/passdb.tdb
private dir = {self.runtime}/private
state directory = {self.runtime}/state
cache directory = {self.runtime}/cache
lock directory = {self.runtime}/lock
pid directory = {self.runtime}/pid
ncalrpc dir = {self.runtime}/ncalrpc
'''
        for name, (path, username, unix_user) in sorted(self.shares.items()):
            cfg += f'''
[{name}]
path = {path}
valid users = {username}
force user = {unix_user}
guest ok = no
browseable = no
read only = no
smb encrypt = required
follow symlinks = yes
wide links = no
hide dot files = yes
create mask = 0600
force create mode = 0600
directory mask = 0700
force directory mode = 0700
'''
        temp = self.config.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(cfg)
            f.flush()
            os.fsync(f.fileno())
        temp.replace(self.config)
    def _control(self, *args):
        return self.run(['smbcontrol', '-s', str(self.config), 'smbd', *args])
    def start(self):
        self.run(['testparm', '-s', str(self.config)])
        self.process = subprocess.Popen(['smbd', '--foreground', '--no-process-group', '-s', str(self.config)],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        start_new_session=True)
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Dedicated Samba failed to start')
            try:
                self._control('ping')
                return
            except subprocess.SubprocessError:
                time.sleep(0.1)
        raise RuntimeError('Dedicated Samba did not become ready')
    def stop(self):
        if not self.process:
            return
        try:
            if self.process.poll() is None:
                self._control('shutdown')
                self.process.wait(timeout=10)
        except subprocess.SubprocessError:
            # smbd owns a separate session/process group. Never signal the API,
            # test runner, host services or a process group inherited from them.
            if self.process.poll() is None:
                try:
                    os.killpg(self.process.pid,signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(self.process.pid,signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    self.process.wait(timeout=5)
    def _account(self, username, unix_uid):
        if type(unix_uid) is not int or not 20000 <= unix_uid <= 2000000000:
            raise ValueError('Invalid account uid')
        group = 'gsa'+str(unix_uid)
        existing = self.run(['getent', 'group', group], check=False)
        if existing.returncode:
            self.run(['groupadd', '--gid', str(unix_uid), group])
        elif existing.stdout.split(':')[2] != str(unix_uid):
            raise RuntimeError('Gateway group identity mismatch')
        anchor = group
        existing = self.run(['getent','passwd',anchor],check=False)
        if existing.returncode:
            self.run(['useradd','--no-create-home','--no-user-group','--no-log-init',
                      '--uid',str(unix_uid),'--gid',group,'--home-dir','/nonexistent',
                      '--shell','/usr/sbin/nologin',anchor])
        elif existing.stdout.split(':')[2:4] != [str(unix_uid),str(unix_uid)]:
            raise RuntimeError('Gateway account uid mismatch')
        existing = self.run(['getent','passwd',username],check=False)
        if existing.returncode:
            # A distinct Unix uid also prevents SID/name aliasing across SMB users.
            self.run(['useradd','--system','--no-create-home','--no-user-group','--no-log-init',
                      '--gid',group,'--home-dir','/nonexistent','--shell','/usr/sbin/nologin',username])
        elif existing.stdout.split(':')[3] != str(unix_uid):
            raise RuntimeError('Gateway session group mismatch')
        return anchor
    def open(self, lease, lower, user, *, save_paths=None, save_namespace=None):
        name, username = self.names(lease)
        paths = self.paths(lease)
        lower = clean_path(lower)
        tree_signature(lower)
        namespace_data=None
        if save_namespace:
            from .save_profiles import verify_variant
            from .namespace_storage import prepare_namespace
            verify_variant(lower,save_namespace['variant'],save_paths=save_paths or [])
            namespace_data=prepare_namespace(self.state,lease,user,save_namespace,
                lambda game:self.paths(dict(lease,game_id=game))['upper'],chown=self.chown)
        mounted_workspace = lease.get('mounted_workspace', False)
        if mounted_workspace and self.workspace_exports is None:
            raise RuntimeError('Mounted workspace export is unavailable')
        unix_user = None if mounted_workspace else self._account(username, user['uid'])
        # Ancestors permit traversal only; the account's directory is private.
        workspaces = self.state/'workspaces'
        workspaces.mkdir(mode=0o711, exist_ok=True)
        os.chmod(workspaces,0o711)
        account = workspaces/lease['user_id']
        account.mkdir(mode=0o700, exist_ok=True)
        self.chown(account, user['uid'], user['uid'])
        base = paths['merged'].parent
        base.mkdir(mode=0o700, exist_ok=True)
        self.chown(base, user['uid'], user['uid'])
        for path in paths.values():
            path.mkdir(mode=0o700, exist_ok=True)
            if path.is_symlink():
                raise RuntimeError('Unsafe workspace')
        if self.mount_probe(paths['merged']) or self.mount_probe(paths['lower']):
            raise RuntimeError('Workspace still mounted')
        if paths['upper'].stat().st_dev != paths['work'].stat().st_dev:
            raise RuntimeError('Upper and work must share a filesystem')
        from .privacy import begin_initialization, mask_seed_saves, finish_initialization
        rules = save_paths or []
        initialize = begin_initialization(paths,rules)
        self.run(['mount', '--bind', str(lower), str(paths['lower'])])
        self.run(['mount', '-o', 'remount,bind,ro,nosuid,nodev,noexec', str(paths['lower'])])
        options = f'lowerdir={paths["lower"]},upperdir={paths["upper"]},workdir={paths["work"]},metacopy=on,redirect_dir=on,index=off,nosuid,nodev,noexec'
        self.run(['mount', '-t', 'overlay', 'overlay', '-o', options, str(paths['merged'])])
        if initialize:
            self.prepare_view(paths['merged'], user['uid'], lower)
            mask_seed_saves(paths['merged'],rules,user['uid'])
            self.run(['sync','-f',str(paths['upper'])])
            finish_initialization(paths)
        profile=paths['merged']/'.gameshelf-profile'
        if profile.is_symlink() or (profile.exists() and not profile.is_dir()):
            raise RuntimeError('Private profile must be a real directory')
        profile.mkdir(mode=0o700,exist_ok=True)
        self.chown(profile,user['uid'],user['uid'])
        if save_namespace:
            from .save_profiles import RELATIVE,apply_export_patches,prepare_windows_images
            from .reviewed_profiles import _check_path
            apply_export_patches(paths['merged'],save_namespace['variant'],user,chown=self.chown)
            _check_path(paths['merged'],RELATIVE,directory=True)
            target=paths['merged']/RELATIVE
            target.mkdir(mode=0o700,parents=True,exist_ok=True)
            if next(target.iterdir(),None) is not None:
                from .common import APIError
                raise APIError(409,'Existing reserved profile saves require explicit backup/adoption')
            self.chown(target.parent,user['uid'],user['uid']); self.chown(target,user['uid'],user['uid'])
            self.run(['mount','--bind',str(namespace_data),str(target)])
            self.run(['mount','-o','remount,bind,rw,nosuid,nodev,noexec',str(target)])
            if not self.mount_probe(target): raise RuntimeError('Canonical save bind could not be verified')
            self.verify_save_namespace(lease,save_namespace)
            prepare_windows_images(paths['merged'],save_namespace['variant'])
        if self.mz_sync is not None:
            self.mz_sync.prepare(lease, lower, user, paths['merged'])
        from .unity_save_redirect import ENTRY
        windows_entry = lease.get('windows_entry')
        # Wine runs this same Windows distribution. Its private save location
        # must not depend on which desktop requested the account workspace.
        if ((windows_entry and windows_entry.casefold() == ENTRY.casefold()) or
                (not windows_entry and (lower / ENTRY).is_file())):
            self._mount_unity_save_redirect(lease, lower, paths['merged'], user['uid'])
        self._write_workspace_receipt(profile, lease)
        if mounted_workspace:
            lease['mounted_workspace_descriptor'] = self.workspace_exports.publish(
                lease['user_id'], user['uid'], lease['id'], paths['merged'])
            return None
        password = secrets.token_urlsafe(32)
        self.run(['smbpasswd', '-s', '-c', str(self.config), '-a', username], input=password+'\n'+password+'\n')
        self.shares[name] = (paths['merged'], username, unix_user)
        self._write_config()
        self._control('reload-config')
        return dict(host=self.host, port=self.port, name=name, username=username, password=password)
    def _write_workspace_receipt(self, profile, lease):
        receipt = secrets.token_hex(32)
        data = (self._id(lease['id']) + '\n' + receipt + '\n').encode('ascii')
        with tempfile.NamedTemporaryFile(dir=profile, prefix='.workspace-receipt-', delete=False) as file:
            stage = Path(file.name)
            try:
                file.write(data)
                file.flush()
                os.fchmod(file.fileno(), 0o444)
                stage.replace(profile/'workspace-receipt-v1')
            finally:
                stage.unlink(missing_ok=True)
        lease['workspace_receipt'] = receipt

    def prepare_generic_windows_entry(self, lease, entry):
        from .common import APIError
        from .workflow import _canonical_relative
        view = self.paths(lease)['merged']
        if not self.mount_probe(view) or _canonical_relative(view, entry) != entry:
            raise APIError(409, 'Windows entry is unavailable in the account overlay')
        from .save_profiles import prepare_windows_entry
        prepare_windows_entry(view/entry)

    def _runtime_patch_file(self, lease):
        return clean_path(self.state / 'windows-runtime-patches' / self._id(lease['id']))

    def _mount_unity_save_redirect(self, lease, lower, view, uid):
        from .unity_save_redirect import ASSEMBLY, prepare
        if not self.mount_probe(view):
            raise RuntimeError('Unity save redirection requires a mounted account view')
        payload = prepare(lower, view)
        patch = self._runtime_patch_file(lease)
        patch.parent.mkdir(mode=0o700, exist_ok=True)
        info = patch.parent.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise RuntimeError('Unsafe Windows runtime patch directory')
        fd = os.open(patch, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as file:
            file.write(payload)
            file.flush()
            # Samba serves this read-only bind as the workspace's Unix account.
            self.chown(patch, uid, uid)
            os.fchmod(file.fileno(), 0o500)
        target = view / ASSEMBLY
        self.run(['mount', '--bind', str(patch), str(target)])
        self.run(['mount', '-o', 'remount,bind,ro,nosuid,nodev,noexec', str(target)])
        if not self.mount_probe(target):
            raise RuntimeError('Unity save redirection bind could not be verified')

    def verify_launch_profile(self, lease, profile):
        from .reviewed_profiles import verify_export
        verify_export(self.paths(lease)['merged'],profile)
    def verify_save_namespace(self,lease,namespace):
        from .namespace_storage import validate_context
        from .save_profiles import RELATIVE,verify_variant
        from .reviewed_profiles import _check_path
        from .common import APIError
        validate_context(lease,namespace)
        view=self.paths(lease)['merged']; target=clean_path(view/RELATIVE)
        _check_path(view,RELATIVE,directory=True,required=True)
        if not self.mount_probe(target): raise APIError(409,'Reviewed canonical save bind is not active')
        verify_variant(view,namespace['variant'],export=True)

    def close(self, lease):
        name, username = self.names(lease)
        paths = self.paths(lease)
        deleted = self.run(['smbpasswd', '-s', '-c', str(self.config), '-x', username], check=False)
        if deleted.returncode:
            remaining = self.run(['pdbedit', '-s', str(self.config), '-L'], check=False)
            if remaining.returncode or any(line.split(':',1)[0] == username for line in remaining.stdout.splitlines()):
                raise RuntimeError('Cannot verify SMB identity revocation')
        self.shares.pop(name, None)
        self._write_config()
        self._control('reload-config')
        self._control('close-share', name)
        if self.workspace_exports is not None:
            self.workspace_exports.revoke(lease['user_id'], lease['id'])
        patch = self._runtime_patch_file(lease)
        if patch.exists():
            from .unity_save_redirect import ASSEMBLY
            target = clean_path(paths['merged'] / ASSEMBLY)
            if self.mount_probe(target):
                self.run(['umount', str(target)])
                if self.mount_probe(target):
                    raise RuntimeError('Windows runtime patch is still mounted')
            patch.unlink()
        if lease.get('save_namespace'):
            import json
            from .namespace_storage import validate_context
            from .save_profiles import RELATIVE
            context=json.loads(lease['save_namespace']); validate_context(lease,context)
            target=clean_path(paths['merged']/RELATIVE)
            if self.mount_probe(target):
                self.run(['umount',str(target)])
                if self.mount_probe(target): raise RuntimeError('Canonical save bind still mounted')
        # A successful normal unmount is the barrier for old open handles.
        # Busy or failed shutdown leaves the service lease in revoking state.
        for key in ('merged', 'lower'):
            if self.mount_probe(paths[key]):
                self.run(['umount', str(paths[key])])
        if self.mz_sync is not None:
            if self.mount_probe(paths['merged']) or self.mount_probe(paths['lower']):
                raise RuntimeError('MZ snapshot requires a fully closed workspace')
            self.mz_sync.commit(lease, paths['upper'])
        existing = self.run(['getent', 'passwd', username], check=False)
        if existing.returncode == 0 and existing.stdout.strip():
            self.run(['userdel', username])
