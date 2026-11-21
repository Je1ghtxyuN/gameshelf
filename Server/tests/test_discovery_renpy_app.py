import plistlib
import tempfile
import unittest
from pathlib import Path
from gameshelf_server.discovery import discover

class RenpyAppDiscoveryTests(unittest.TestCase):
    def test_pc_distribution_stub_does_not_override_working_windows_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'renpy').mkdir()
            (root/'renpy/__init__.py').write_text('# engine')
            (root/'Play.exe').write_bytes(b'MZ game')
            (root/'Play.app/Contents/Resources/lib').mkdir(parents=True)
            entries,_=discover(root,['renpy','Play.app'],['Play.exe'])
            self.assertEqual(entries,[dict(path='Play.exe',backend='renpy',engine='renpy')])
            self.assertTrue((root/'Play.app/Contents/Resources/lib').is_dir())
            (root/'Play.app/Contents/Resources/autorun/renpy').mkdir(parents=True)
            entries,_=discover(root,['renpy','Play.app'],['Play.exe'])
            self.assertIn(dict(path='Play.app',backend='native',engine='renpy'),entries)

    def test_independent_macos_launcher_beside_pc_game_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'renpy').mkdir()
            (root/'Play.exe').write_bytes(b'MZ game')
            app=root/'Play.app'
            (app/'Contents/MacOS').mkdir(parents=True)
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable':'Play'}))
            (app/'Contents/MacOS/Play').write_bytes(b'\xcf\xfa\xed\xfe synthetic')
            entries,_=discover(root,['renpy','Play.app'],['Play.exe'])
            self.assertIn(dict(path='Play.app',backend='native',engine='unknown'),entries)
