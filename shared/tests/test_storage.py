import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import storage


class StorageTests(unittest.TestCase):
    def test_build_uses_the_workspace_binary_without_injecting_an_upstream_example(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "phase3"
            (data / "kernel-build").mkdir(parents=True)
            (root / "cargo/release").mkdir(parents=True)
            (root / "upstream").mkdir()
            binary = root / "cargo/release/storage-host"
            binary.write_bytes(b"storage host")
            for path in (data / "pg-init", data / "storage-probe",
                         data / "kernel-build/elfloader_hyperlight-x86_64"):
                path.write_bytes(b"guest artifact")
            with patch.object(storage, "WORK", root), \
                    patch.object(storage, "DATA", data), \
                    patch.object(storage, "host_inputs", return_value={}), \
                    patch.object(storage.runtime, "UPSTREAM", root / "upstream"), \
                    patch.object(storage.runtime, "build"), \
                    patch.object(storage.runtime, "cargo_env", return_value={}), \
                    patch.object(storage.runtime, "compile_guest_executable"), \
                    patch.object(storage.runtime, "run") as run:
                storage.build()
            self.assertEqual((data / "storage-host").read_bytes(), binary.read_bytes())
            self.assertFalse((root / "upstream/examples").exists())
            commands = [call.args[0] for call in run.call_args_list]
            workspace = ["--manifest-path", storage.HERE.parent / "Cargo.toml",
                         "--package", "storage-host"]
            self.assertEqual(commands, [
                ["cargo", "build", "--release", "--locked", *workspace],
                ["cargo", "test", "--release", "--locked", "--lib", "hostfs::tests"],
                ["cargo", "test", "--release", "--locked", *workspace],
            ])
            for call in run.call_args_list:
                self.assertEqual(call.kwargs["cwd"], root / "upstream")

    def test_workspace_dependency_drift_is_rejected_before_building(self):
        pins = storage.runtime.runtime_sources()["hyperlight-unikraft"]["dependencies"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in pins:
                for change in ("version", "missing", "duplicate"):
                    entries = [(key, "0.20.0" if key == name and change == "version" else value)
                               for key, value in pins.items()
                               if key != name or change != "missing"]
                    if change == "duplicate":
                        entries.append((name, pins[name]))
                    (root / "Cargo.lock").write_text("".join(
                        f'[[package]]\nname = "{key}"\nversion = "{value}"\n'
                        for key, value in entries))
                    with self.subTest(name=name, change=change), \
                            patch.object(storage, "HERE", root / "shared"), \
                            patch.object(storage.runtime, "build",
                                         side_effect=AssertionError("kernel build")), \
                            self.assertRaisesRegex(RuntimeError, f"Unexpected locked {name}"):
                        storage.build()

    def test_crate_changes_invalidate_storage_host_fingerprints(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(storage, "HERE", Path(temporary) / "shared"), \
                patch.object(storage.runtime, "build_inputs", return_value={}):
            paths = [*storage.crate_files(), storage.HERE / "storage.py",
                     storage.HERE / "tests/storage.c", storage.HERE / "rootfs/kernel.conf"]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"original")
            original = storage.host_inputs()
            for path in storage.crate_files():
                path.write_bytes(b"modified")
                with self.subTest(path=path):
                    self.assertNotEqual(storage.host_inputs(), original)
                path.write_bytes(b"original")

    def test_artifact_only_storage_build_refuses_source_and_cargo_access(self):
        with patch.dict(storage.runtime.os.environ, {"HL_PG_ARTIFACT_ONLY": "1"}), \
                patch.object(storage.runtime, "verify_rust_dependencies",
                             side_effect=AssertionError("source lockfile")), \
                patch.object(storage.runtime, "run", side_effect=AssertionError("cargo")), \
                self.assertRaisesRegex(RuntimeError, "disabled in artifact-only"):
            storage.build()

    def test_shared_host_artifacts_reject_mixed_kernel_metadata_and_modified_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "kernel-build").mkdir()
            paths = {"host_sha256": root / "storage-host",
                     "kernel_sha256": root / "kernel-build/elfloader_hyperlight-x86_64",
                     "init_sha256": root / "pg-init", "probe_sha256": root / "storage-probe"}
            metadata = {"inputs": {}, "rust_build": storage.runtime.RUST_BUILD,
                        "guest_identity": storage.runtime.guest_identity()}
            for field, path in paths.items():
                path.write_bytes(field.encode())
                metadata[field] = storage.runtime.digest(path)
            kernel = {field: metadata[field] for field in ("kernel_sha256", "init_sha256")}
            (root / "host.json").write_text(json.dumps(metadata))
            (root / "kernel.json").write_text(json.dumps(kernel))
            with patch.object(storage, "DATA", root), \
                    patch.object(storage, "host_inputs", return_value={}), \
                    patch.object(storage.runtime, "verify_kernel_metadata"):
                storage.verify_host_artifacts()
                for field, path in paths.items():
                    original = path.read_bytes()
                    path.write_bytes(b"modified")
                    with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "Storage guest tool changed"):
                        storage.verify_host_artifacts()
                    path.write_bytes(original)
                (root / "kernel.json").write_text(json.dumps(dict(kernel, kernel_sha256="other-kernel")))
                with self.assertRaisesRegex(RuntimeError, "metadata disagree"):
                    storage.verify_host_artifacts()
                (root / "host.json").write_text(json.dumps(dict(metadata, inputs={"other-runtime": True})))
                with self.assertRaisesRegex(RuntimeError, "build inputs changed"):
                    storage.verify_host_artifacts()

    def test_packaging_and_running_reject_guest_identity_from_another_user(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(storage, "DATA", Path(temporary)), \
                patch.object(storage, "inputs", return_value={}), \
                patch.object(storage, "host_inputs", return_value={}), \
                patch.object(storage.runtime, "verify_upstream"), \
                patch.object(storage.runtime, "prepare_patches"), \
                patch.object(storage.postgres, "prepare_sources"), \
                patch.object(storage.runtime.os, "getuid", return_value=1001), \
                patch.object(storage.runtime.os, "getgid", return_value=2001):
            for filename, check in (("host.json", storage.package), ("build.json", storage.verify)):
                for identity in (None, {"uid": 1000, "gid": 1000},
                                 {"uid": 1001, "gid": 2002}):
                    (Path(temporary) / filename).write_text(json.dumps(
                        {"inputs": {}, "rust_build": storage.runtime.RUST_BUILD,
                         "guest_identity": identity}))
                    with self.subTest(command=check.__name__, identity=identity):
                        with self.assertRaisesRegex(RuntimeError, "UID/GID changed"):
                            check()

    def test_release_metadata_is_required_before_packaging_or_running(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(storage, "DATA", Path(temporary)), \
                patch.object(storage, "inputs", return_value={}), \
                patch.object(storage, "host_inputs", return_value={}), \
                patch.object(storage.runtime, "verify_upstream"), \
                patch.object(storage.runtime, "prepare_patches"), \
                patch.object(storage.postgres, "prepare_sources"):
            for filename, check in (("host.json", storage.package), ("build.json", storage.verify)):
                for settings in ({}, dict(storage.runtime.RUST_BUILD, profile="dev"),
                                 dict(storage.runtime.RUST_BUILD, opt_level=0)):
                    (Path(temporary) / filename).write_text(
                        json.dumps({"inputs": {}, "rust_build": settings}))
                    with self.assertRaisesRegex(RuntimeError, "release build"):
                        check()

    def test_writer_lock_rejects_aliases_and_is_released(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "storage"
            directory.mkdir()
            alias = root / "alias"
            alias.symlink_to(directory)
            with storage.ownership(directory):
                with self.assertRaises(RuntimeError):
                    with storage.ownership(alias):
                        pass
                code = ("import sys; from pathlib import Path; "
                        f"sys.path.insert(0, {str(storage.HERE)!r}); import storage; "
                        f"ctx=storage.ownership(Path({str(alias)!r})); ctx.__enter__()")
                denied = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
                self.assertNotEqual(denied.returncode, 0)
                self.assertIn("already owned", denied.stderr)
            with storage.ownership(directory):
                pass

    def test_postgres_needs_observed_wal_and_directory_sync(self):
        rows = [{"operation": "sync_all", "path": "pgdata/pg_wal/segment", "ok": True},
                {"operation": "sync_all", "path": "pgdata/pg_logical", "ok": True}]
        storage.validate_audit(rows, True)
        for values in ([], rows[:1], rows[1:],
                       [dict(row, ok=False) for row in rows]):
            with self.assertRaises(RuntimeError):
                storage.validate_audit(values, True)

    def test_directory_requires_multiple_successful_pages(self):
        rows = [{"operation": op, "path": path, "ok": ok} for op, path, ok in (
            ("sync_all", "probes", True),
            ("sync_all", "probes/sync-write", True),
            ("sync_all", "probes/dsync-write", True),
            ("sync_all", "probes/fail-sync", False),
            ("sync_all", "probes/fail-dir-sync", False),
            ("write", "probes/partial-write", False),
            ("write", "probes/quota", False))]
        pages = [{"operation": "list_page", "path": "probes/many", "ok": True}] * 40
        storage.validate_audit(rows + pages, False)
        with self.assertRaises(RuntimeError):
            storage.validate_audit(rows + pages[:39], False)
        with self.assertRaises(RuntimeError):
            storage.validate_audit(rows + [dict(row, ok=False) for row in pages], False)

    def test_initialization_never_overwrites_existing_storage(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(storage, "STORAGE", Path(temporary)):
            with self.assertRaisesRegex(RuntimeError, "never overwrite"):
                storage.initialize()

    def test_budget_scan_surfaces_permission_errors(self):
        error = PermissionError("unreadable database subdirectory")
        def unreadable(*_, onerror, **__):
            onerror(error)
            return iter(())
        with patch.object(storage.postgres.os, "walk", unreadable):
            with self.assertRaises(PermissionError):
                storage.postgres.bytes_in(Path("/example"))

    def test_budget_does_not_follow_rootfs_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "link").symlink_to("/missing")
            self.assertEqual(storage.postgres.bytes_in(root), len("/missing"))


if __name__ == "__main__":
    unittest.main()
