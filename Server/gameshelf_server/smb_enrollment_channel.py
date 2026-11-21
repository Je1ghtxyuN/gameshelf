"""Bounded Unix-socket protocol between the gateway and the Samba host."""

import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import struct
import time


def read_message(stream):
    line = stream.readline(4097)
    if len(line) > 4096 or not line.endswith(b'\n'):
        raise ValueError('Invalid SMB enrollment frame')

    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError('Duplicate SMB enrollment field')
        return result

    data = json.loads(line,object_pairs_hook=unique)
    if not isinstance(data,dict):
        raise ValueError('Invalid SMB enrollment frame')
    return data


def dispatch_host_request(host, request):
    fields = {
        'resolve': ('username',),
        'deliver': ('principal','challenge','proof'),
        'discard': ('challenge',),
        'provision': ('account','principal'),
    }
    operation = request.get('op')
    arguments = fields.get(operation) if isinstance(operation,str) else None
    if (arguments is None or set(request) != {'op',*arguments}
            or not all(isinstance(request[key],str) and 0 < len(request[key]) <= 256 for key in arguments)):
        raise ValueError('Unsupported SMB enrollment request')
    return getattr(host,operation)(*(request[key] for key in arguments))


def _require_root_peer(connection):
    if not hasattr(socket,'SO_PEERCRED'):
        raise OSError('SMB enrollment channel requires Linux peer credentials')
    data = connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12)
    if struct.unpack('3i',data)[1] != 0:
        raise PermissionError('SMB enrollment channel requires root peer')


class _EnrollmentHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        try:
            _require_root_peer(self.connection)
            value = dispatch_host_request(self.server.host,read_message(self.rfile))
            response = {'result':value}
        except Exception:
            response = {'error':'SMB host operation failed'}
        try:
            self.wfile.write(json.dumps(response).encode('utf-8')+b'\n')
        except OSError:
            pass


class _EnrollmentServer(socketserver.UnixStreamServer):
    next_cleanup = 0

    def service_actions(self):
        now = time.monotonic()
        if now < self.next_cleanup:
            return
        self.next_cleanup = now + 60
        try:
            self.host.purge_expired()
        except OSError:
            # A temporary filesystem failure must not stop the host service.
            pass


def serve_host(path, host):
    """Run a serial host service on an administrator-owned private socket."""
    path = Path(path)
    parent = path.parent
    info = parent.lstat()
    if (os.geteuid() != 0 or not path.is_absolute() or parent.resolve(strict=True) != parent
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('SMB host channel requires a root-owned private directory')
    with _EnrollmentServer(str(path),_EnrollmentHandler) as server:
        try:
            server.host = host
            os.chmod(path,0o600)
            server.serve_forever()
        finally:
            path.unlink(missing_ok=True)


class SMBEnrollmentHostClient:
    def __init__(self, path):
        self.path = str(path)

    def _request(self, operation, **arguments):
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.settimeout(30)
            connection.connect(self.path)
            _require_root_peer(connection)
            connection.sendall(json.dumps(dict(op=operation,**arguments)).encode('utf-8')+b'\n')
            with connection.makefile('rb') as stream:
                response = read_message(stream)
        if set(response) != {'result'}:
            raise OSError('SMB host operation failed')
        return response['result']

    def resolve(self, username):
        value = self._request('resolve',username=username)
        if not isinstance(value,str) or not value or len(value) > 256:
            raise OSError('Invalid SMB identity response')
        return value

    def deliver(self, principal, challenge, proof):
        if self._request('deliver',principal=principal,challenge=challenge,proof=proof) is not None:
            raise OSError('Invalid SMB proof delivery response')

    def discard(self, challenge):
        self._request('discard',challenge=challenge)

    def provision(self, account, principal):
        return self._request('provision',account=account,principal=principal)
