"""Owner-save import tests use temporary trees only, never NAS or game processes."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gameshelf_server import legacy_saves as saves


class LegacySaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.source, self.target, self.backup = [self.base / n for n in ('source', 'private', 'backup')]
        for root in (self.source, self.target, self.backup):
            root.mkdir(mode=0o700)
        (self.target / '.gameshelf-profile').mkdir(mode=0o700)
        self.identity = 'opaque-owner/server/account/canonical'

    def put(self, root, name, data=b'owner progress'):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def plan(self, rules=None, **kwargs):
        return saves.plan_import(self.source, self.target,
            rules or [dict(path='saves', kind='directory')], self.backup, self.identity, **kwargs)

    def contents(self, root):
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def test_nested_empty_directories_zero_placeholder_backup_and_idempotent_retry(self):
        self.put(self.source, 'saves/slot1.sav', b'owner slot one')
        self.put(self.source, 'saves/nested/persistent', b'owner persistent')
        (self.source / 'saves/empty').mkdir()
        self.put(self.source, 'assets.bin', b'not a save')
        self.put(self.target, 'saves/slot1.sav', b'')
        self.put(self.target, 'saves/unrelated.sav', b'keep existing progress')
        before = self.contents(self.source)
        target_before = self.contents(self.target)
        plan = self.plan()
        self.assertEqual(self.contents(self.source), before)
        self.assertEqual(self.contents(self.target), target_before)
        self.assertEqual(list(self.backup.iterdir()), [])  # Planning is read-only.
        result = saves.apply_import(plan)
        self.assertEqual(result.status, 'applied')
        self.assertEqual(self.contents(self.source), before)
        self.assertEqual((self.target / 'saves/nested/persistent').read_bytes(), b'owner persistent')
        self.assertEqual((self.target / 'saves/unrelated.sav').read_bytes(), b'keep existing progress')
        self.assertTrue((self.target / 'saves/empty').is_dir())
        self.assertFalse((self.target / 'assets.bin').exists())
        operation = Path(result.backup_path)
        self.assertEqual((operation / 'source/saves/slot1.sav').read_bytes(), b'owner slot one')
        self.assertEqual((operation / 'target/saves/slot1.sav').read_bytes(), b'')
        self.assertEqual(operation.stat().st_mode & 0o777, 0o700)
        self.assertEqual((operation / 'ledger.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(saves.apply_import(plan).status, 'already-applied')
        self.assertEqual(saves.apply_import(self.plan()).status, 'already-applied')

    def test_nonempty_difference_preflights_entire_batch_without_any_write(self):
        self.put(self.source, 'saves/a.sav', b'importable')
        self.put(self.source, 'saves/z.sav', b'old owner')
        self.put(self.target, 'saves/z.sav', b'new account progress')
        before = self.contents(self.target)
        with self.assertRaises(saves.DestinationConflict) as caught:
            self.plan()
        self.assertEqual(caught.exception.paths, ('saves/z.sav',))
        self.assertEqual(self.contents(self.target), before)
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_identical_nonempty_destination_is_not_replaced(self):
        self.put(self.source, 'one.sav', b'identical')
        dest = self.put(self.target, 'one.sav', b'identical')
        inode = dest.stat().st_ino
        result = saves.apply_import(self.plan([dict(path='one.sav', kind='file')]))
        self.assertEqual(result.copied, ())
        self.assertEqual(result.identical, ('one.sav',))
        self.assertEqual(dest.stat().st_ino, inode)

    def test_source_content_change_with_same_size_and_restored_mtime_rejected(self):
        source = self.put(self.source, 'saves/a.sav', b'AAAA')
        plan = self.plan()
        stamp = source.stat()
        source.write_bytes(b'BBBB')
        os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with self.assertRaises(saves.SnapshotChanged):
            saves.apply_import(plan)
        self.assertFalse((self.target / 'saves').exists())

    def test_target_changed_after_plan_preserves_new_progress(self):
        self.put(self.source, 'saves/a.sav')
        dest = self.put(self.target, 'saves/a.sav', b'')
        plan = self.plan()
        dest.write_bytes(b'new progress after plan')
        with self.assertRaises(saves.SnapshotChanged):
            saves.apply_import(plan)
        self.assertEqual(dest.read_bytes(), b'new progress after plan')

    def test_new_progress_after_success_is_never_overwritten_by_old_retry(self):
        self.put(self.source, 'saves/a.sav')
        plan = self.plan()
        saves.apply_import(plan)
        (self.target / 'saves/a.sav').write_bytes(b'advanced progress')
        with self.assertRaises((saves.SnapshotChanged, saves.DestinationConflict)):
            saves.apply_import(plan)
        self.assertEqual((self.target / 'saves/a.sav').read_bytes(), b'advanced progress')

    def test_partial_stage_write_rolls_back_prior_files_and_placeholder_then_retry(self):
        self.put(self.source, 'saves/a.sav', b'owner A')
        self.put(self.source, 'saves/z.sav', b'owner Z')
        self.put(self.target, 'saves/a.sav', b'')
        plan = self.plan()
        original = saves._copy_file
        staged = []

        def partial(source_fd, source_name, target_fd, target_name, expected):
            if target_name.startswith('.legacy-save-stage-'):
                staged.append(target_name)
                if len(staged) == 2:
                    fd = os.open(target_name, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=target_fd)
                    try:
                        os.write(fd, b'partial')
                    finally:
                        os.close(fd)
                    raise OSError('injected partial file write')
            return original(source_fd, source_name, target_fd, target_name, expected)

        with patch.object(saves, '_copy_file', partial):
            with self.assertRaises(OSError):
                saves.apply_import(plan)
        self.assertEqual((self.target / 'saves/a.sav').read_bytes(), b'')
        self.assertFalse((self.target / 'saves/z.sav').exists())
        self.assertFalse(any(p.name.startswith('.legacy-save-stage-') for p in self.target.rglob('*')))
        self.assertEqual(saves.apply_import(plan).status, 'applied')
        self.assertEqual((self.target / 'saves/z.sav').read_bytes(), b'owner Z')

    def test_rollback_failure_retains_ledger_and_retry_finishes_recovery(self):
        self.put(self.source, 'saves/a.sav', b'owner A')
        self.put(self.source, 'saves/z.sav', b'owner Z')
        plan = self.plan()
        install = saves._install_file
        count = 0

        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 3:  # Owner marker + first save installed, then fail.
                raise OSError('injected install failure')
            return install(*args, **kwargs)

        with patch.object(saves, '_install_file', fail_second), patch.object(saves, '_rollback_file', side_effect=OSError('rollback I/O failure')):
            with self.assertRaises(saves.RollbackError) as caught:
                saves.apply_import(plan)
        self.assertEqual(caught.exception.operation_id, plan.operation_id)
        operation = self.backup / plan.operation_id
        self.assertEqual(json.loads((operation / 'ledger.json').read_text())['phase'], 'rollback-failed')
        self.assertEqual((operation / 'source/saves/a.sav').read_bytes(), b'owner A')
        self.assertEqual(saves.apply_import(plan).status, 'applied')
        self.assertEqual((self.target / 'saves/a.sav').read_bytes(), b'owner A')
        self.assertEqual((self.target / 'saves/z.sav').read_bytes(), b'owner Z')

    def test_rollback_will_not_delete_an_unexpected_new_progress_file(self):
        self.put(self.source, 'saves/a.sav', b'owner A')
        self.put(self.source, 'saves/z.sav', b'owner Z')
        plan = self.plan()
        install = saves._install_file
        count = 0

        def changed_progress(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 3:
                (self.target / 'saves/a.sav').write_bytes(b'concurrent new progress')
                raise OSError('fixture interrupts import')
            return install(*args, **kwargs)

        with patch.object(saves, '_install_file', changed_progress):
            with self.assertRaises(saves.RollbackError):
                saves.apply_import(plan)
        self.assertEqual((self.target / 'saves/a.sav').read_bytes(), b'concurrent new progress')

    def test_rollback_preserves_owner_binding_if_unmodified_progress_changed(self):
        self.put(self.source, 'saves/a.sav', b'owner A')
        self.put(self.source, 'saves/z.sav', b'owner Z')
        self.put(self.target, 'saves/existing.sav', b'old existing progress')
        plan = self.plan()
        install = saves._install_file
        count = 0

        def changed_other_progress(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 3:
                (self.target / 'saves/existing.sav').write_bytes(b'new unrelated progress')
                raise OSError('fixture interruption')
            return install(*args, **kwargs)

        with patch.object(saves, '_install_file', changed_other_progress):
            with self.assertRaises(saves.RollbackError):
                saves.apply_import(plan)
        self.assertEqual((self.target / 'saves/existing.sav').read_bytes(), b'new unrelated progress')
        self.assertTrue((self.target / saves.OWNER).exists())

    def test_rejects_unsafe_rules_overlap_links_special_and_hardlinks(self):
        self.put(self.source, 'saves/a.sav')
        for name in ('../saves', '/saves', 'saves//a', 'saves/../a', '.gameshelf-profile', 'C:save', 'saves\\a'):
            with self.subTest(name=name), self.assertRaises(saves.SaveImportError):
                self.plan([dict(path=name, kind='file')])
        with self.assertRaises(saves.SaveImportError):
            self.plan([dict(path='saves', kind='directory'), dict(path='saves/a.sav', kind='file')])
        (self.source / 'saves/link').symlink_to(self.base)
        with self.assertRaises(saves.SaveImportError):
            self.plan()
        (self.source / 'saves/link').unlink()
        os.mkfifo(self.source / 'saves/pipe')
        with self.assertRaises(saves.SaveImportError):
            self.plan()
        (self.source / 'saves/pipe').unlink()
        os.link(self.source / 'saves/a.sav', self.source / 'saves/hard.sav')
        with self.assertRaises(saves.SaveImportError):
            self.plan()

    def test_roots_and_destination_parents_cannot_be_links_or_overlap(self):
        self.put(self.source, 'saves/a.sav')
        alias = self.base / 'alias'
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(saves.SaveImportError):
            saves.plan_import(alias, self.target, [dict(path='saves', kind='directory')], self.backup, self.identity)
        with self.assertRaises(saves.SaveImportError):
            saves.plan_import(self.source, self.target, [dict(path='saves', kind='directory')], self.target, self.identity)
        (self.target / 'saves').symlink_to(self.source / 'saves', target_is_directory=True)
        with self.assertRaises(saves.SaveImportError):
            self.plan()

    def test_case_and_unicode_aliases_are_rejected_even_for_exact_requested_name(self):
        self.put(self.source, 'saves/a.sav')
        # Inject directory names only on normalization/case-insensitive filesystems.
        original = saves._names
        for extra in ('A.SAV',):
            with patch.object(saves, '_names', side_effect=lambda fd, extra=extra: original(fd) + [extra] if os.fstat(fd).st_ino == (self.source / 'saves').stat().st_ino else original(fd)):
                with self.assertRaises(saves.SaveImportError):
                    self.plan()
        self.put(self.source, 'saves/Café.sav')
        with patch.object(saves, '_names', side_effect=lambda fd: original(fd) + ['Cafe\u0301.sav'] if os.fstat(fd).st_ino == (self.source / 'saves').stat().st_ino else original(fd)):
            with self.assertRaises(saves.SaveImportError):
                self.plan()

    def test_bounded_files_bytes_and_depth(self):
        self.put(self.source, 'saves/a.sav', b'1234')
        self.put(self.source, 'saves/deep/b.sav', b'5678')
        for limits in (saves.Limits(max_files=1), saves.Limits(max_bytes=4), saves.Limits(max_depth=1)):
            with self.subTest(limits=limits), self.assertRaises(saves.SaveImportError):
                self.plan(limits=limits)
        self.assertEqual(list(self.backup.iterdir()), [])

    def test_exact_save_bounds_do_not_count_fixed_owner_marker(self):
        self.put(self.source, 'one.sav', b'1234')
        plan = self.plan([dict(path='one.sav', kind='file')], limits=saves.Limits(max_files=1, max_bytes=4, max_entries=1, max_depth=1))
        self.assertEqual(saves.apply_import(plan).status, 'applied')
        self.assertEqual((self.target / 'one.sav').read_bytes(), b'1234')

    def test_recovery_after_directories_already_rolled_back(self):
        self.put(self.source, 'saves/a.sav', b'owner A')
        self.put(self.source, 'saves/z.sav', b'owner Z')
        plan = self.plan()
        install, rollback = saves._install_file, saves._rollback_file
        count = 0

        def fail_install(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 3:
                raise OSError('fixture stop')
            return install(*args, **kwargs)

        def fail_marker(target, operation, ledger, entry):
            if entry['path'] == saves.OWNER:
                raise OSError('marker rollback failure')
            return rollback(target, operation, ledger, entry)

        with patch.object(saves, '_install_file', fail_install), patch.object(saves, '_rollback_file', fail_marker):
            with self.assertRaises(saves.RollbackError):
                saves.apply_import(plan)
        self.assertFalse((self.target / 'saves').exists())
        self.assertEqual(saves.apply_import(plan).status, 'applied')

    def test_interrupted_import_recovers_before_rejecting_changed_source(self):
        self.put(self.source, 'saves/a.sav', b'owner A')
        self.put(self.source, 'saves/z.sav', b'owner Z')
        plan = self.plan()
        install = saves._install_file
        count = 0

        def interrupt(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 3:
                raise KeyboardInterrupt('simulate caller death after first save')
            return install(*args, **kwargs)

        with patch.object(saves, '_install_file', interrupt):
            with self.assertRaises(KeyboardInterrupt):
                saves.apply_import(plan)
        self.assertTrue((self.target / 'saves/a.sav').exists())
        (self.source / 'saves/a.sav').write_bytes(b'changed source')
        with self.assertRaises(saves.SnapshotChanged):
            saves.apply_import(plan)
        self.assertFalse((self.target / 'saves').exists())
        self.assertFalse((self.target / saves.OWNER).exists())

    def test_real_private_marker_and_identity_binding_required(self):
        self.put(self.source, 'saves/a.sav')
        marker = self.target / '.gameshelf-profile'
        marker.rmdir()
        with self.assertRaises(saves.SaveImportError):
            self.plan()
        marker.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(saves.SaveImportError):
            self.plan()
        marker.unlink(); marker.mkdir(mode=0o700)
        saves.apply_import(self.plan())
        other = self.base / 'other-backup'; other.mkdir(mode=0o700)
        with self.assertRaises(saves.SaveImportError):
            saves.plan_import(self.source, self.target, [dict(path='saves', kind='directory')], other, 'different-owner')
        self.assertEqual(list(other.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
