import importlib.util
from pathlib import Path
import tempfile
import unittest

AVAILABLE = importlib.util.find_spec('gameshelf_server.__main__') is not None

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(AVAILABLE, 'Container entry point and configuration must exist')
    def test_explicit_tls_source_and_smb_configuration(self):
        from gameshelf_server.__main__ import Settings
        with self.assertRaises(ValueError):
            Settings.from_env({})
        env = dict(GAMESHELF_STATE='/state', GAMESHELF_SOURCE_ROOTS='["/sources/library"]',
                   GAMESHELF_SMB_HOST='gateway.example.invalid', GAMESHELF_TLS_CERT='/tls/server.crt',
                   GAMESHELF_TLS_KEY='/tls/server.key', GAMESHELF_BIND='0.0.0.0')
        cfg = Settings.from_env(env)
        self.assertEqual(cfg.port,8443)
        self.assertEqual(cfg.smb_port,1445)
        self.assertEqual(cfg.sources,[Path('/sources/library')])
        self.assertEqual(cfg.lease_ttl,90)
        for bad in ('[]','{}','[1]','"/sources/library"'):
            with self.assertRaises(ValueError):
                Settings.from_env(dict(env,GAMESHELF_SOURCE_ROOTS=bad))
        with self.assertRaises(ValueError):
            Settings.from_env(dict(env,GAMESHELF_TLS_KEY=''))
        with self.assertRaises(ValueError):
            Settings.from_env(dict(env,GAMESHELF_LEASE_TTL='0'))
    def test_workspace_exports_are_explicitly_enabled(self):
        from gameshelf_server.__main__ import Settings
        env = dict(GAMESHELF_SOURCE_ROOTS='["/games"]', GAMESHELF_SMB_HOST='nas.example.invalid')
        self.assertIsNone(Settings.from_env(env).workspace_export_root)
        env['GAMESHELF_WORKSPACE_EXPORT_ROOT'] = '/exports'
        with self.assertRaises(ValueError):
            Settings.from_env(env)
        env['GAMESHELF_WORKSPACE_MANIFEST'] = '/manifest.json'
        with self.assertRaises(ValueError):
            Settings.from_env(env)
        env['GAMESHELF_DIRECT_NAS'] = '1'
        self.assertEqual(Settings.from_env(env).workspace_export_root, Path('/exports'))

    def test_smb_enrollment_requires_mounted_workspace_configuration(self):
        from gameshelf_server.__main__ import Settings
        env = dict(GAMESHELF_SOURCE_ROOTS='["/games"]', GAMESHELF_SMB_HOST='nas.example.invalid',
                   GAMESHELF_SMB_ENROLLMENT_SOCKET='/host/enrollment.sock',
                   GAMESHELF_SMB_VERIFICATION_SHARE='verify-gameshelf')
        with self.assertRaises(ValueError):
            Settings.from_env(env)
        env.update(GAMESHELF_DIRECT_NAS='1', GAMESHELF_WORKSPACE_EXPORT_ROOT='/exports',
                   GAMESHELF_WORKSPACE_MANIFEST='/manifest.json')
        settings = Settings.from_env(env)
        self.assertEqual(settings.smb_enrollment_socket,Path('/host/enrollment.sock'))
        self.assertEqual(settings.smb_verification_share,'verify-gameshelf')
        with self.assertRaises(ValueError):
            Settings.from_env(dict(env,GAMESHELF_SMB_VERIFICATION_SHARE='../unsafe'))

    def test_reserved_lease_requires_original_workspace_export_mount(self):
        from contextlib import closing
        import sqlite3
        from gameshelf_server.__main__ import record_workspace_export_root
        with tempfile.TemporaryDirectory() as directory, closing(sqlite3.connect(':memory:')) as db:
            root = Path(directory)
            db.executescript('CREATE TABLE settings(key PRIMARY KEY,value); CREATE TABLE leases(status);')
            record_workspace_export_root(db, root)
            db.execute("INSERT INTO leases VALUES ('revoking')")
            with self.assertRaisesRegex(ValueError, 'original workspace export mount'):
                record_workspace_export_root(db, None)
            record_workspace_export_root(db, root)
            db.execute("UPDATE leases SET status='closed'")
            record_workspace_export_root(db, None)
            self.assertEqual(db.execute('SELECT * FROM settings').fetchall(), [])

    def test_process_lock_prevents_multiple_writers(self):
        from gameshelf_server.__main__ import state_lock
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp).resolve()
            with state_lock(state):
                with self.assertRaises(RuntimeError):
                    with state_lock(state):
                        self.fail('second process must not acquire lock')
            with state_lock(state):
                pass
    def test_bootstrap_secret_file_only_on_empty_database(self):
        from gameshelf_server.__main__ import bootstrap
        from gameshelf_server.service import Service
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp).resolve()
            source=base/'source'; source.mkdir()
            secret=base/'password'; secret.write_text('a bootstrap test password\n')
            service=Service(base/'state',None,[source])
            self.addCleanup(service.close)
            bootstrap(service,'owner',secret)
            self.assertTrue(service.login('owner','a bootstrap test password')['user']['admin'])
            secret.unlink()
            bootstrap(service,'other',secret) # Existing users never get reset.
            self.assertEqual(len(service.users()),1)

    def test_live_create_admin_via_root_private_socket(self):
        from gameshelf_server.control import AdminControl, create_live_admin
        from gameshelf_server.service import Service
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp).resolve(); source=base/'source'; source.mkdir()
            service=Service(base/'state',None,[source])
            self.addCleanup(service.close)
            with AdminControl(service):
                self.assertEqual((service.state/'admin.sock').stat().st_mode & 0o777,0o600)
                result=create_live_admin(service.state,'operator','a private operator password')
                self.assertTrue(result['admin'])
                self.assertTrue(service.login('operator','a private operator password')['user']['admin'])
    def test_certificate_fingerprint_is_sha256_of_der(self):
        from gameshelf_server.__main__ import cert_fingerprint
        import base64,hashlib
        with tempfile.TemporaryDirectory() as tmp:
            cert=Path(tmp)/'cert.pem'
            der=b'certificate bytes for deterministic fingerprint fixture'
            cert.write_text('-----BEGIN CERTIFICATE-----\n'+base64.b64encode(der).decode()+'\n-----END CERTIFICATE-----\n')
            self.assertEqual(cert_fingerprint(cert),hashlib.sha256(der).hexdigest())
    def test_invitation_configuration_uses_explicit_https_and_normalizes_pin(self):
        from gameshelf_server.__main__ import Settings
        env=dict(GAMESHELF_SOURCE_ROOTS='["/sources/library"]',GAMESHELF_SMB_HOST='gateway.example.invalid',
                 GAMESHELF_PUBLIC_ADDRESS='https://games.example.invalid:8787/',
                 GAMESHELF_CERTIFICATE_SHA256=':'.join(['AB']*32))
        cfg=Settings.from_env(env)
        self.assertEqual(getattr(cfg,'public_address',None),'https://games.example.invalid:8787')
        self.assertEqual(cfg.certificate_sha256,'ab'*32)
        no_address=dict(env); no_address.pop('GAMESHELF_PUBLIC_ADDRESS'); no_address.pop('GAMESHELF_CERTIFICATE_SHA256')
        self.assertIsNone(Settings.from_env(no_address).public_address)
        self.assertEqual(Settings.from_env(dict(no_address,GAMESHELF_PUBLIC_ORIGIN='https://games.example.invalid:8787')).public_address,
                         'https://games.example.invalid:8787')
        for address in ('http://games.example.invalid','https://user:password@games.example.invalid',
                        'https://@games.example.invalid','https://games.example.invalid/path',
                        'https://games.example.invalid?','https://games.example.invalid#',
                        'https://games.example.invalid:0','https://games.example.invalid:65536',
                        'https://games.example.invalid/\\bad','https://games.example.invalid/\n'):
            with self.subTest(address=address),self.assertRaises(ValueError):
                Settings.from_env(dict(env,GAMESHELF_PUBLIC_ADDRESS=address))
        with self.assertRaises(ValueError):
            Settings.from_env(dict(env,GAMESHELF_CERTIFICATE_SHA256='not-a-pin'))
if __name__ == '__main__':
    unittest.main()
