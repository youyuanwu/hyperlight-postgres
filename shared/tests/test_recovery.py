import unittest
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

import recovery


class RecoveryTests(unittest.TestCase):
    def test_spill_requires_actual_disk_sort_and_both_temp_io_directions(self):
        node = {"Node Type": "Sort", "Sort Space Type": "Disk", "Sort Space Used": 1024,
                "Actual Rows": recovery.ROWS, "Temp Read Blocks": 12, "Temp Written Blocks": 16}
        evidence = recovery.validate_spill([{"Plan": node}])
        self.assertEqual(evidence["sort_space_kib"], 1024)
        for change in ({"Sort Space Type": "Memory"}, {"Sort Space Used": 0},
                       {"Temp Read Blocks": 0}, {"Temp Written Blocks": 0}, {"Actual Rows": 1}):
            with self.assertRaises(RuntimeError):
                recovery.validate_spill([{"Plan": dict(node, **change)}])

    def test_nested_sort_is_validated(self):
        node = {"Node Type": "Sort", "Sort Space Type": "Disk", "Sort Space Used": 10,
                "Actual Rows": recovery.ROWS, "Temp Read Blocks": 1, "Temp Written Blocks": 1}
        self.assertEqual(recovery.validate_spill([{"Plan": {"Plans": [node]}}])["temp_read_blocks"], 1)

    def test_stale_descriptor_identity_is_bounded_and_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "pgdata/pg_dynshmem"
            directory.mkdir(parents=True)
            descriptor = directory / "mmap.1"
            generation = "a" * 32
            payload = b"100MSDLH" + (4096).to_bytes(8, "little") * 2 + generation.encode() + b"\0" * 8
            with patch.object(recovery.storage, "STORAGE", root):
                descriptor.write_bytes(payload)
                self.assertEqual(recovery.dsm_generations(), {"mmap.1": generation})
                for content in (payload[:56], b"invalid!" + payload[8:], payload[:24] + b"z" * 32 + payload[56:]):
                    descriptor.write_bytes(content)
                    with self.assertRaises(RuntimeError):
                        recovery.dsm_generations()
                descriptor.unlink()
                descriptor.symlink_to("/missing")
                with self.assertRaises(RuntimeError):
                    recovery.dsm_generations()

    def test_crash_requires_stale_dsm_by_default_but_allows_panic_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pgdata").mkdir()
            lock = root / "pgdata/postmaster.pid"
            lock.touch()
            vm = recovery.VM("crash", {}, output=root)
            vm.host_pid = 123
            vm.monitor = Mock()
            vm.process = Mock(returncode=-9)
            with patch.object(recovery.storage, "STORAGE", root), \
                    patch.object(recovery.os, "kill") as kill, \
                    patch.object(recovery, "dsm_generations", return_value={}) as generations:
                with self.assertRaisesRegex(RuntimeError, "stale DSM descriptors"):
                    vm.crash()
                result = vm.crash(require_stale_dsm=False)
                self.assertEqual(result["stale_descriptors"], 0)
                self.assertEqual(result["host_exit"], -9)
                kill.assert_called_with(vm.host_pid, recovery.signal.SIGKILL)
                generations.return_value = {"mmap.1": vm.identity}
                self.assertEqual(vm.crash()["stale_descriptors"], 1)
                for stale in ({"mmap.1": "a" * 32},
                              {"mmap.1": vm.identity, "mmap.2": "a" * 32}):
                    generations.return_value = stale
                    for required in (True, False):
                        with self.subTest(stale=stale, required=required), \
                                self.assertRaisesRegex(RuntimeError, "stale DSM descriptors"):
                            vm.crash(require_stale_dsm=required)
                generations.side_effect = RuntimeError("malformed guest DSM descriptor")
                with self.assertRaisesRegex(RuntimeError, "malformed"):
                    vm.crash(require_stale_dsm=False)
                lock.unlink()
                with self.assertRaisesRegex(RuntimeError, "postmaster lockfile"):
                    vm.crash(require_stale_dsm=False)

    def test_panic_recovery_without_stale_dsm_still_requires_recovery_and_sync_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vm = recovery.VM("recover-quota", {}, output=root)
            session = Mock()
            session.execute.return_value = ["checksum"]
            context = Mock()
            context.__enter__ = Mock(return_value=session)
            context.__exit__ = Mock(return_value=False)
            vm.session = Mock(return_value=context)
            markers = ("database system was interrupted", "automatic recovery in progress",
                       "checkpoint starting: end-of-recovery", "redo is not required")
            vm.log_path.write_text("\n".join(markers))
            rows = [{"ok": True, "operation": "stale_guest_lock", "path": "pgdata/postmaster.pid"},
                    {"ok": True, "operation": "sync_all", "path": "pgdata/pg_wal/segment"}]
            with patch.object(recovery.postgres, "expect"), \
                    patch.object(recovery, "dsm_generations", return_value={}) as generations, \
                    patch.object(recovery, "audit_snapshot", return_value=(rows, 0)) as audit:
                recovery.assert_recovered(vm, "checksum", "a" * 32, require_redo=False)
                for marker in markers:
                    vm.log_path.write_text("\n".join(value for value in markers if value != marker))
                    with self.subTest(marker=marker), self.assertRaises(RuntimeError):
                        recovery.assert_recovered(vm, "checksum", "a" * 32, require_redo=False)
                vm.log_path.write_text("\n".join(markers))
                for remaining in (rows[:1], rows[1:]):
                    audit.return_value = (remaining, 0)
                    with self.subTest(rows=remaining), self.assertRaises(RuntimeError):
                        recovery.assert_recovered(vm, "checksum", "a" * 32, require_redo=False)
                audit.return_value = (rows, 0)
                generations.return_value = {"mmap.1": "a" * 32}
                with self.assertRaisesRegex(RuntimeError, "survived recovery"):
                    recovery.assert_recovered(vm, "checksum", "a" * 32, require_redo=False)

    def test_only_interrupted_audit_tail_is_permitted_and_counted(self):
        with tempfile.TemporaryDirectory() as temporary:
            audit = Path(temporary) / "audit"
            audit.write_bytes(b'{"ok":true}\n{"operation":')
            rows, tail = recovery.audit_snapshot(audit, allow_partial=True)
            self.assertEqual(rows, [{"ok": True}])
            self.assertEqual(tail, len(b'{"operation":'))
            with self.assertRaises(RuntimeError):
                recovery.audit_snapshot(audit)
            audit.write_bytes(b'not-json\n')
            with self.assertRaises(ValueError):
                recovery.audit_snapshot(audit, allow_partial=True)


if __name__ == "__main__":
    unittest.main()
