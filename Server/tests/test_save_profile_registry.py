"""Trusted review provenance and the exact MIT RenPy 8.3.7 isolation patch."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import test_unified_library
from namespace_support import setup_sources
from gameshelf_server import save_profiles as profiles
from gameshelf_server.common import APIError

MIT=Path(__file__).resolve().parents[2]/'Contracts/Fixtures/renpy-8.3.7/savelocation.py'
ORIGINAL='7a84aea6ddccab67c7e004d94cd38092e4a910c15ccb6fabb44aecbde1108427'
PATCHED='ba48a4c044ec8d557bbc2f42aab441caaf52480ebeb29f2a9208594b6dc09596'


def reviewed_837(case):
    mac,win,manifest=setup_sources(case)
    original=MIT.read_bytes()
    text=original.decode(); start=text.index('    # 2. Game-local savedir.\n'); end=text.index('    # Scan the location once.\n',start)
    replaced=(text[:start]+'    # GameShelf account adapter v1: use only the supplied account savedir.\n\n'+text[end:]).encode()
    assert hashlib.sha256(original).hexdigest()==ORIGINAL
    assert hashlib.sha256(replaced).hexdigest()==PATCHED
    for record,folder in zip(manifest['profiles'][0]['variants'],(mac,win)):
        item=next(f for f in record['fingerprints'] if f['role']=='locations')
        (folder/item['path']).write_bytes(original); item['sha256']=ORIGINAL
        record['patches']=[dict(path=item['path'],sourceSHA256=ORIGINAL,targetSHA256=PATCHED,replacementBase64=base64.b64encode(replaced).decode())]
    (case.s.state/profiles.CATALOG).write_text(json.dumps(manifest))
    return mac,win,manifest


class SaveProfileRegistryTests(unittest.TestCase):
    setUp=test_unified_library.UnifiedLibraryTests.setUp

    def test_secondary_locations_require_an_exact_patch(self):
        _, _, manifest = setup_sources(self)
        for digest in (
            '2fcd8f8dd88e85fd689a91db1f98c7ab7c5790d3b871b1f89420e27db3bba8f2',
            'e0b32aab4127325a307a8396b6a9b636781161fd23d595127e668d784342b85c',
        ):
            with self.subTest(digest=digest):
                record = manifest['profiles'][0]['variants'][1]
                locations = next(f for f in record['fingerprints'] if f['role'] == 'locations')
                locations['sha256'] = digest
                with self.assertRaises(APIError):
                    profiles.validate_catalog(manifest)

    def test_exact_official_patch_is_fingerprint_bound_and_private_idempotent(self):
        mac,win,manifest=reviewed_837(self)
        catalog=profiles.load_catalog(self.s.state); variant=catalog[manifest['profiles'][0]['id']]['variants'][1]
        profiles.verify_variant(win,variant,save_paths=variant['savePaths'],entries=[dict(path='Play.exe',backend='renpy')])
        view=self.s.state/'synthetic-private-copy'; shutil.copytree(win,view)
        source=win/'renpy/savelocation.py'; target=view/'renpy/savelocation.py'
        profiles.apply_export_patches(view,variant,dict(uid=os.getuid()),chown=lambda *a:None)
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),PATCHED)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),ORIGINAL)
        profiles.verify_variant(view,variant,export=True)
        inode=target.stat().st_ino
        profiles.apply_export_patches(view,variant,dict(uid=os.getuid()),chown=lambda *a:None)
        self.assertEqual(target.stat().st_ino,inode)

    def test_original_secondary_locations_and_forged_patch_target_never_certified(self):
        mac,win,manifest=reviewed_837(self)
        raw=copy.deepcopy(manifest); del raw['profiles'][0]['variants'][0]['patches']
        with self.assertRaises(APIError): profiles.validate_catalog(raw)
        raw=copy.deepcopy(manifest); record=raw['profiles'][0]['variants'][0]
        payload=b'# not the approved RenPy patch\n'
        record['patches'][0]['targetSHA256']=hashlib.sha256(payload).hexdigest()
        record['patches'][0]['replacementBase64']=base64.b64encode(payload).decode()
        with self.assertRaises(APIError): profiles.validate_catalog(raw)

    def test_832_location_module_requires_the_exact_832_patch(self):
        _, _, manifest = reviewed_837(self)
        windows = manifest['profiles'][0]['variants'][1]
        location = next(item for item in windows['fingerprints'] if item['role'] == 'locations')
        patch = windows['patches'][0]
        original = MIT.read_bytes().replace(b'Copyright 2004-2025', b'Copyright 2004-2024', 1)
        replacement = base64.b64decode(patch['replacementBase64']).replace(
            b'Copyright 2004-2025', b'Copyright 2004-2024', 1)
        location['sha256'] = patch['sourceSHA256'] = hashlib.sha256(original).hexdigest()
        with self.assertRaises(APIError):
            profiles.validate_catalog(manifest)
        patch['targetSHA256'] = hashlib.sha256(replacement).hexdigest()
        patch['replacementBase64'] = base64.b64encode(replacement).decode()
        profiles.validate_catalog(manifest)

    def test_unknown_private_runtime_is_preserved_not_overwritten(self):
        mac,win,manifest=reviewed_837(self)
        variant=profiles.load_catalog(self.s.state)[manifest['profiles'][0]['id']]['variants'][1]
        view=self.s.state/'other-private-copy'; shutil.copytree(win,view)
        target=view/'renpy/savelocation.py'; target.write_bytes(b'unknown private runtime')
        with self.assertRaises(APIError): profiles.apply_export_patches(view,variant,dict(uid=os.getuid()),chown=lambda *a:None)
        self.assertEqual(target.read_bytes(),b'unknown private runtime')

    def test_catalog_must_be_owned_private_regular_unlinked_file(self):
        setup_sources(self); path=self.s.state/profiles.CATALOG
        path.chmod(0o644)
        with self.assertRaises(APIError): profiles.load_catalog(self.s.state)
        path.chmod(0o600)
        with patch('gameshelf_server.save_profiles.os.geteuid',return_value=os.geteuid()+1000):
            with self.assertRaises(APIError): profiles.load_catalog(self.s.state)
        saved=path.with_name('real-private-review.json'); path.rename(saved); path.symlink_to(saved)
        with self.assertRaises(APIError): profiles.load_catalog(self.s.state)

    def test_code_paths_env_or_arbitrary_args_are_not_registry_shortcuts(self):
        mac,win,manifest=setup_sources(self)
        for extra in ('args','environment','verified'):
            raw=copy.deepcopy(manifest); raw['profiles'][0][extra]=True
            with self.assertRaises(APIError): profiles.validate_catalog(raw)
        raw=copy.deepcopy(manifest); raw['profiles'][0]['variants'][0]['fingerprints'][0]['path']='../outside'
        with self.assertRaises(APIError): profiles.validate_catalog(raw)

    def test_reviewed_cache_is_masked_only_in_the_private_view(self):
        _, win, manifest = reviewed_837(self)
        relative = 'renpy/__pycache__/savelocation.cpython-39.pyc'
        cache = win / relative
        cache.parent.mkdir()
        cache.write_bytes(b'pinned cache')
        record = manifest['profiles'][0]['variants'][1]
        record['patches'][0]['compiledCaches'] = [
            dict(path=relative, sha256=hashlib.sha256(cache.read_bytes()).hexdigest())]
        variant = profiles.validate_catalog(manifest)[manifest['profiles'][0]['id']]['variants'][1]
        view = self.s.state / 'cache-overlay-fixture'
        shutil.copytree(win, view)
        other = view / 'renpy/__pycache__/savelocation.cpython-39.opt-1.pyc'
        other.write_bytes(b'unknown')
        with self.assertRaises(APIError):
            profiles.apply_export_patches(view, variant, dict(uid=os.getuid()), chown=lambda *a: None)
        self.assertEqual((view / relative).read_bytes(), b'pinned cache')
        other.unlink()
        (view / relative).write_bytes(b'changed cache')
        with self.assertRaises(APIError):
            profiles.apply_export_patches(view, variant, dict(uid=os.getuid()), chown=lambda *a: None)
        self.assertEqual((view / relative).read_bytes(), b'changed cache')
        (view / relative).write_bytes(b'pinned cache')
        profiles.apply_export_patches(view, variant, dict(uid=os.getuid()), chown=lambda *a: None)
        self.assertFalse((view / relative).exists())
        self.assertEqual(cache.read_bytes(), b'pinned cache')
        profiles.verify_variant(view, variant, export=True)
        profiles.apply_export_patches(view, variant, dict(uid=os.getuid()), chown=lambda *a: None)

    def test_regenerated_cache_is_discarded_after_exact_patched_source_verification(self):
        _, win, manifest = reviewed_837(self)
        relative = 'renpy/__pycache__/savelocation.cpython-39.pyc'
        cache = win / relative
        cache.parent.mkdir()
        cache.write_bytes(b'official original cache')
        record = manifest['profiles'][0]['variants'][1]
        record['patches'][0]['compiledCaches'] = [
            dict(path=relative, sha256=hashlib.sha256(cache.read_bytes()).hexdigest())]
        variant = profiles.validate_catalog(manifest)[manifest['profiles'][0]['id']]['variants'][1]
        view = self.s.state / 'repeated-launch-cache'
        shutil.copytree(win, view)
        profiles.apply_export_patches(view, variant, dict(uid=os.getuid()), chown=lambda *a: None)
        for _ in range(2):
            (view / relative).write_bytes(b'regenerated from private patched source')
            profiles.apply_export_patches(view, variant, dict(uid=os.getuid()), chown=lambda *a: None)
            self.assertFalse((view / relative).exists())
            profiles.verify_variant(view, variant, export=True)
        self.assertEqual(cache.read_bytes(), b'official original cache')
        (view / relative).write_bytes(b'do not trust this if source changed')
        (view / 'renpy/savelocation.py').write_bytes(b'changed source')
        with self.assertRaises(APIError):
            profiles.apply_export_patches(view, variant, dict(uid=os.getuid()), chown=lambda *a: None)
        self.assertTrue((view / relative).is_file())

    def test_private_patch_refuses_unreviewed_compiled_savelocation_cache(self):
        mac,win,manifest=reviewed_837(self)
        variant=profiles.load_catalog(self.s.state)[manifest['profiles'][0]['id']]['variants'][1]
        view=self.s.state/'private-with-compiled-cache'; shutil.copytree(win,view)
        cache=view/'renpy/__pycache__/savelocation.cpython-39.pyc'; cache.parent.mkdir(); cache.write_bytes(b'unreviewed compiled runtime')
        target=view/'renpy/savelocation.py'; before=target.read_bytes()
        with self.assertRaises(APIError): profiles.apply_export_patches(view,variant,dict(uid=os.getuid()),chown=lambda *a:None)
        self.assertEqual(target.read_bytes(),before); self.assertEqual(cache.read_bytes(),b'unreviewed compiled runtime')
