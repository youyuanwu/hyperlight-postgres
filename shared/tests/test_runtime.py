import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("runtime", Path(__file__).parents[1] / "runtime.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class ResultTests(unittest.TestCase):
    def test_build_preflight_skips_kvm_but_test_preflight_requires_it(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(runtime, "WORK", Path(temporary)), \
                patch.object(runtime.os, "access", return_value=False), \
                patch.object(runtime.shutil, "which", return_value="/usr/bin/tool"), \
                patch.object(runtime.shutil, "disk_usage", return_value=SimpleNamespace(free=8 * 1024**3)), \
                patch.object(runtime.Path, "read_text", return_value="MemAvailable: 4194304 kB\n"), \
                patch.object(runtime, "run", return_value="tool 1\n") as run:
            runtime.preflight(require_kvm=False)
            self.assertFalse(any(call.args[0][0] == "sudo" for call in run.call_args_list))
            run.reset_mock()
            runtime.preflight()
            checks = [call.args[0] for call in run.call_args_list if call.args[0][0] == "sudo"]
            self.assertEqual(checks, [["sudo", "-n", "test", "-r", "/dev/kvm"],
                                      ["sudo", "-n", "test", "-w", "/dev/kvm"]])

    def test_process_profile_is_immutable_and_all_guest_artifacts_are_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "kernel-build").mkdir()
            (root / "upstream/kernel").mkdir(parents=True)
            (root / "upstream/kernel/elfloader_hyperlight-x86_64").write_bytes(b"other-profile")
            paths = {"kernel_sha256": root / "kernel-build/elfloader_hyperlight-x86_64",
                     "cli_sha256": root / "hluk", "rootfs_sha256": root / "phase1.cpio"}
            metadata = {"inputs": {}, "rust_build": runtime.RUST_BUILD}
            for field, path in paths.items():
                path.write_bytes(field.encode())
                metadata[field] = runtime.digest(path)
            metadata["embedded_kernel_sha256"] = metadata["kernel_sha256"]
            (root / "build.json").write_text(json.dumps(metadata))
            with patch.object(runtime, "WORK", root), patch.object(runtime, "UPSTREAM", root / "upstream"), \
                    patch.object(runtime, "verify_upstream"), patch.object(runtime, "prepare_patches"), \
                    patch.object(runtime, "build_inputs", return_value={}):
                runtime.verify_process_artifacts(True)
                for field, path in paths.items():
                    original = path.read_bytes()
                    path.write_bytes(b"changed")
                    with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "Artifact changed"):
                        runtime.verify_process_artifacts(True)
                    path.write_bytes(original)
                metadata["embedded_kernel_sha256"] = "different-kernel"
                (root / "build.json").write_text(json.dumps(metadata))
                with self.assertRaisesRegex(RuntimeError, "embedded kernel"):
                    runtime.verify_process_artifacts(True)

    def test_prebuilt_native_probe_never_compiles_and_rejects_modified_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "native-phase1"
            binary.write_bytes(b"native-probe")
            (root / "build.json").write_text(json.dumps(
                {"inputs": {}, "rust_build": runtime.RUST_BUILD,
                 "native_sha256": runtime.digest(binary)}))
            with patch.object(runtime, "WORK", root), patch.object(runtime, "CASES", ("hello",)), \
                    patch.object(runtime, "verify_upstream"), patch.object(runtime, "prepare_patches"), \
                    patch.object(runtime, "build_inputs", return_value={}), \
                    patch.object(runtime, "compile_executable") as compile, \
                    patch.object(runtime.subprocess, "run",
                                 return_value=SimpleNamespace(stdout="PASS hello\n", returncode=0)):
                runtime.test(False, prebuilt=True)
                compile.assert_not_called()
                binary.write_bytes(b"modified")
                with self.assertRaisesRegex(RuntimeError, "Native process artifact changed"):
                    runtime.test(False, prebuilt=True)

    def test_build_jobs_default_and_invalid_values(self):
        with patch.dict(runtime.os.environ, {}, clear=True):
            self.assertEqual(runtime.build_jobs(), 1)
            for value in ("0", "-1", "two", ""):
                with patch.dict(runtime.os.environ, {"HL_PG_BUILD_JOBS": value}):
                    with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "positive integer"):
                        runtime.build_jobs()

    def test_cargo_uses_selected_jobs_and_preserves_cache_wrapper(self):
        with patch.dict(runtime.os.environ, {"HL_PG_BUILD_JOBS": "2", "CARGO_BUILD_JOBS": "8",
                                            "RUSTC_WRAPPER": "sccache"}):
            env = runtime.cargo_env()
            self.assertEqual(env["CARGO_BUILD_JOBS"], "2")
            self.assertEqual(env["RUSTC_WRAPPER"], "sccache")

    def test_process_compiler_uses_cache_and_rejects_invalid_toggle(self):
        with patch.dict(runtime.os.environ, {"HL_PG_USE_SCCACHE": "1"}), \
                patch.object(runtime, "run") as run:
            runtime.compile_executable(Path("/probe"))
            self.assertEqual(run.call_args.args[0][:2], ["sccache", "musl-gcc"])
        with patch.dict(runtime.os.environ, {"HL_PG_USE_SCCACHE": "yes"}):
            with self.assertRaisesRegex(RuntimeError, "must be 0 or 1"):
                runtime.compiler_args("gcc")

    def test_guest_kernel_identity_matches_host_and_invalidates_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "defconfig-elfloader").write_text("CONFIG_TEST=y\n")
            overlay = root / "kernel.conf"
            overlay.write_text('CONFIG_LIBPOSIX_USER_USERNAME="phase2"\n')
            with patch.object(runtime, "UPSTREAM", root), \
                    patch.object(runtime, "patch_files", return_value=[]), \
                    patch.object(runtime, "pins", return_value={}), \
                    patch.object(runtime.os, "getuid", return_value=1001), \
                    patch.object(runtime.os, "getgid", return_value=2001):
                contents, inputs = runtime.kernel_config(overlay)
                self.assertIn(b"CONFIG_LIBPOSIX_USER_UID=1001\n", contents)
                self.assertIn(b"CONFIG_LIBPOSIX_USER_GID=2001\n", contents)
                self.assertEqual(inputs["guest_identity"], {"uid": 1001, "gid": 2001})
                with patch.object(runtime.os, "getgid", return_value=2002):
                    changed_contents, changed_inputs = runtime.kernel_config(overlay)
                self.assertNotEqual(contents, changed_contents)
                self.assertNotEqual(inputs, changed_inputs)
                contents, inputs = runtime.kernel_config(None)
                self.assertEqual(contents, b"CONFIG_TEST=y\n")
                self.assertNotIn("guest_identity", inputs)

    def test_guest_identity_rejects_root(self):
        with patch.object(runtime.os, "getuid", return_value=0):
            with self.assertRaisesRegex(RuntimeError, "non-root"):
                runtime.guest_identity()

    def test_standalone_runtime_command_keeps_artifacts_in_its_sample_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "workspace/hyperlight-postgres"
            sample = repository / "pg16"
            sample.mkdir(parents=True)
            (sample / "sources.toml").write_text('[postgres]\nversion = "16.15"\n')
            shared = repository / "shared"
            shared.mkdir()
            script = shared / "runtime.py"
            shutil.copy2(runtime.__file__, script)
            spec = importlib.util.spec_from_file_location("standalone_runtime", script)
            standalone = importlib.util.module_from_spec(spec)
            with patch.dict(runtime.os.environ, {"HL_PG_VERSION": "16"}):
                spec.loader.exec_module(standalone)

            with patch.object(standalone, "preflight") as preflight, \
                    patch.object(standalone.sys, "argv", [str(script), "preflight"]):
                standalone.main()

            preflight.assert_called_once_with()
            self.assertTrue((sample / "target/postgres-experiment/phase1.lock").is_file())
            self.assertFalse((repository / "target").exists())
            self.assertFalse((root / "target").exists())
            self.assertTrue((shared / "target/upstream.lock").is_file())
            self.assertFalse((shared / "target/sources").exists())

    def test_version_selection_is_explicit_and_keeps_work_directories_isolated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / "shared"
            shared.mkdir()
            script = shared / "runtime.py"
            shutil.copy2(runtime.__file__, script)
            for major in ("16", "17"):
                directory = root / f"pg{major}"
                directory.mkdir()
                (directory / "sources.toml").write_text(f'[postgres]\nversion = "{major}.1"\n')
            with patch.object(runtime, "HERE", root / "shared"):
                for major in ("16", "17"):
                    with patch.dict(runtime.os.environ, {"HL_PG_VERSION": major}):
                        self.assertEqual(runtime.version_directory(), root / f"pg{major}")
                        spec = importlib.util.spec_from_file_location("selected_runtime", script)
                        selected = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(selected)
                        self.assertEqual(selected.WORK, root / f"pg{major}/target/postgres-experiment")
                        self.assertEqual(selected.UPSTREAM, shared / "target/sources/hyperlight-unikraft")
                        self.assertEqual(selected.pins()["postgres"]["version"], f"{major}.1")
                        self.assertFalse(selected.WORK.exists())
                for value in ("", "../16", "pg16", "016", "16.15", "99"):
                    with patch.dict(runtime.os.environ, {"HL_PG_VERSION": value}):
                        with self.subTest(version=value), self.assertRaises(RuntimeError):
                            runtime.version_directory()
                with patch.object(runtime, "VERSION_DIR", root / "pg17"):
                    self.assertEqual(runtime.pins()["postgres"]["version"], "17.1")
                    (root / "pg17/sources.toml").write_text('[postgres]\nversion = "16.15"\n')
                    with self.assertRaisesRegex(RuntimeError, "does not match pg17"):
                        runtime.pins()

    def test_postgres_patches_are_version_local_and_runtime_patches_are_shared(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("shared/patches/unikraft", "pg16/patches/postgres", "pg17/patches/postgres"):
                directory = root / name
                directory.mkdir(parents=True)
                (directory / "series").write_text("one.patch\n")
                (directory / "one.patch").write_text(name)
            with patch.object(runtime, "HERE", root / "shared"):
                for major in ("16", "17"):
                    with patch.object(runtime, "VERSION_DIR", root / f"pg{major}"):
                        self.assertEqual(runtime.patch_files("postgres"),
                                         [root / f"pg{major}/patches/postgres/one.patch"])
                        self.assertEqual(runtime.patch_files("unikraft"),
                                         [root / "shared/patches/unikraft/one.patch"])
                (root / "pg17/patches/postgres/series").unlink()
                with patch.object(runtime, "VERSION_DIR", root / "pg17"):
                    with self.assertRaisesRegex(RuntimeError, "Missing PostgreSQL patch series"):
                        runtime.patch_files("postgres")

    def test_upstream_checkout_is_reused_across_versions_and_rejects_a_different_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = {"url": "https://example.invalid/upstream.git", "commit": "pinned"}

            def output(args, **kwargs):
                if args[1] == "clone":
                    Path(args[-1]).mkdir()
                return "pinned\n"

            with patch.object(runtime, "SHARED_WORK", root / "shared"), \
                    patch.object(runtime, "run", side_effect=output) as run:
                for major in ("16", "17"):
                    with patch.object(runtime, "WORK", root / f"pg{major}"):
                        self.assertEqual(runtime.checkout("hyperlight-unikraft", spec),
                                         root / "shared/sources/hyperlight-unikraft")
                self.assertEqual(sum(call.args[0][1] == "clone" for call in run.call_args_list), 1)
                self.assertFalse((root / "pg16").exists())
                self.assertFalse((root / "pg17").exists())
                with self.assertRaisesRegex(RuntimeError, "unexpected HEAD"):
                    runtime.checkout("hyperlight-unikraft", dict(spec, commit="different"))

    def test_shared_patch_stamp_survives_switching_postgres_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shared = root / "shared"
            shared.mkdir()
            patchfile = root / "one.patch"
            patchfile.write_text("patch")
            with patch.object(runtime, "SHARED_WORK", shared), \
                    patch.object(runtime, "patch_files", return_value=[patchfile]), \
                    patch.object(runtime, "run", return_value="") as run:
                for major in ("16", "17"):
                    work = root / f"pg{major}"
                    work.mkdir()
                    with patch.object(runtime, "WORK", work):
                        runtime.prepare_patch_target("unikraft", root)
                    self.assertFalse((work / "unikraft-patches.json").exists())
                self.assertEqual(sum(call.args[0][1:3] == ["apply", "--check"]
                                     for call in run.call_args_list), 1)
                self.assertTrue((shared / "unikraft-patches.json").is_file())

    def test_shared_checkout_lock_rejects_another_process_and_releases_after_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            shared = Path(temporary)
            code = (f"import sys; sys.path.insert(0, {str(runtime.HERE)!r}); "
                    "import runtime; from pathlib import Path; "
                    f"runtime.SHARED_WORK=Path({str(shared)!r}); "
                    "ctx=runtime.upstream_lock(); ctx.__enter__()")
            with patch.object(runtime, "SHARED_WORK", shared):
                with self.assertRaisesRegex(RuntimeError, "test error"):
                    with runtime.upstream_lock():
                        result = subprocess.run([sys.executable, "-c", code],
                                                capture_output=True, text=True, timeout=5)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("shared Hyperlight-Unikraft checkout", result.stderr)
                        raise RuntimeError("test error")
                result = subprocess.run([sys.executable, "-c", code],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_cargo_build_settings_enforce_release_even_with_environment_overrides(self):
        with patch.dict(runtime.os.environ, {"CARGO_PROFILE_RELEASE_OPT_LEVEL": "0",
                                            "CARGO_PROFILE_RELEASE_DEBUG_ASSERTIONS": "true",
                                            "HL_PG_BUILD_JOBS": "1",
                                            "CARGO_BUILD_JOBS": "8"}):
            env = runtime.cargo_env()
            self.assertEqual(runtime.RUST_BUILD["profile"], "release")
            self.assertEqual(env["CARGO_PROFILE_RELEASE_OPT_LEVEL"], "3")
            self.assertEqual(env["CARGO_PROFILE_RELEASE_DEBUG_ASSERTIONS"], "false")
            self.assertEqual(env["CARGO_PROFILE_RELEASE_OVERFLOW_CHECKS"], "false")
            self.assertEqual(env["CARGO_PROFILE_RELEASE_DEBUG"], "0")
            self.assertEqual(env["CARGO_PROFILE_RELEASE_INCREMENTAL"], "false")
            self.assertEqual(env["CARGO_BUILD_JOBS"], "1")

    def test_exact_success(self):
        runtime.validate_result("spawn", "diagnostic\nPASS spawn\n", 0)

    def test_missing_wrong_duplicate_and_timeout_fail(self):
        for output, code in (("", 0), ("PASS pipes\n", 0),
                             ("PASS spawn\nPASS spawn\n", 0),
                             ("PASS spawn\n", 1), ("PASS spawn\n", 124)):
            with self.subTest(output=output, code=code), self.assertRaises(RuntimeError):
                runtime.validate_result("spawn", output, code)

    def test_patch_series_order_and_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "patches/unikraft"
            directory.mkdir(parents=True)
            for name in ("one.patch", "two.patch"):
                (directory / name).touch()
            with patch.object(runtime, "HERE", root):
                (directory / "series").write_text("# ordered\ntwo.patch\none.patch\n")
                self.assertEqual([p.name for p in runtime.patch_files("unikraft")],
                                 ["two.patch", "one.patch"])
                for content in ("../one.patch\n", "one.patch\none.patch\n", "missing.patch\n"):
                    (directory / "series").write_text(content)
                    with self.assertRaises(RuntimeError):
                        runtime.patch_files("unikraft")

    def test_submodule_identity_and_locked_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spec = runtime.pins()["hyperlight-unikraft"]
            status = "\n".join(f" {commit} {path}" for path, commit in
                               spec["submodules"].items())
            lock = root / "Cargo.lock"
            lock.write_text('[[package]]\nname="hyperlight-host"\nversion="0.19.0"\n'
                            '[[package]]\nname="hyperlight-common"\nversion="0.19.0"\n')

            def output(args, **kwargs):
                if args[1] == "rev-parse":
                    return spec["commit"] + "\n"
                if args[1] == "submodule":
                    return status
                return ""

            with patch.object(runtime, "UPSTREAM", root), patch.object(runtime, "run", output):
                self.assertEqual(runtime.verify_upstream(), spec["submodules"])
                lock.write_text(lock.read_text().replace("0.19.0", "0.20.0"))
                with self.assertRaises(RuntimeError):
                    runtime.verify_upstream()
                status = status.replace(" ", "+", 1)
                with self.assertRaises(RuntimeError):
                    runtime.verify_upstream()

    def test_patches_checked_and_applied_sequentially(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "patches/unikraft"
            directory.mkdir(parents=True)
            (directory / "series").write_text("one.patch\ntwo.patch\n")
            for name in ("one.patch", "two.patch"):
                (directory / name).write_text(name)
            calls = []

            def output(args, **kwargs):
                calls.append(args)
                return ""

            with patch.object(runtime, "HERE", root), patch.object(runtime, "WORK", root), \
                    patch.object(runtime, "SHARED_WORK", root), \
                    patch.object(runtime, "UPSTREAM", root), patch.object(runtime, "run", output):
                runtime.prepare_patches()
                applies = [args for args in calls if args[1] == "apply"]
                self.assertEqual(applies, [
                    ["git", "apply", "--check", directory / "one.patch"],
                    ["git", "apply", directory / "one.patch"],
                    ["git", "apply", "--check", directory / "two.patch"],
                    ["git", "apply", directory / "two.patch"],
                ])
                calls.clear()
                runtime.prepare_patches()
                self.assertFalse(any(args[1] == "apply" for args in calls))
                (directory / "series").write_text("two.patch\none.patch\n")
                with patch.object(runtime, "run", return_value="existing diff"):
                    with self.assertRaises(RuntimeError):
                        runtime.prepare_patches()
                (directory / "one.patch").write_text("changed")
                with patch.object(runtime, "run", return_value="unexpected diff"):
                    with self.assertRaises(RuntimeError):
                        runtime.prepare_patches()


if __name__ == "__main__":
    unittest.main()
