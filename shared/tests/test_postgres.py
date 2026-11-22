import tempfile
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import postgres


class PostgresTests(unittest.TestCase):
    def test_artifact_only_install_validation_needs_no_sources_and_rejects_modified_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / "postgres-native-build"
            (build / "src/include").mkdir(parents=True)
            config = build / "src/include/pg_config.h"
            config.write_text("/* #undef USE_ASSERT_CHECKING */\n")
            native = root / "native-pg"
            (native / "bin").mkdir(parents=True)
            server = native / "bin/postgres"
            client = native / "bin/psql"
            server.write_bytes(b"server")
            client.write_bytes(b"client")
            with patch.object(postgres, "WORK", root), patch.object(postgres, "NATIVE", native), \
                    patch.dict(postgres.runtime.os.environ, {"HL_PG_ARTIFACT_ONLY": "1"}), \
                    patch.object(postgres, "postgres_inputs", side_effect=AssertionError("source lookup")):
                metadata = {"configure": list(map(str, postgres.configure_args(False)[1:])),
                            "version": postgres.runtime.pins()["postgres"]["version"],
                            "pg_config_sha256": postgres.runtime.digest(config),
                            "installed_sha256": postgres.installed_hashes(False)}
                stamp = build / "experiment-inputs.json"
                stamp.write_text(json.dumps(metadata))
                postgres.verify_postgres_install(False)
                self.assertFalse((root / "sources").exists())
                for change in ({"version": "99.1"}, {"configure": []}, {"pg_config_sha256": "other"}):
                    stamp.write_text(json.dumps(dict(metadata, **change)))
                    with self.subTest(change=change), self.assertRaises(RuntimeError):
                        postgres.verify_postgres_install(False)
                stamp.write_text(json.dumps(metadata))
                client.write_bytes(b"modified")
                with self.assertRaisesRegex(RuntimeError, "Installed PostgreSQL files"):
                    postgres.verify_postgres_install(False)
                server.unlink()
                with self.assertRaisesRegex(RuntimeError, "Missing installed PostgreSQL"):
                    postgres.verify_postgres_install(False)

    def test_configure_uses_cached_native_and_guest_compilers(self):
        with patch.dict(postgres.runtime.os.environ, {"HL_PG_USE_SCCACHE": "1"}):
            self.assertIn("CC=sccache gcc", postgres.configure_args(False))
            self.assertIn("CC=sccache musl-gcc", postgres.configure_args(True))
        with patch.dict(postgres.runtime.os.environ, {"HL_PG_USE_SCCACHE": "0"}):
            self.assertNotIn("CC=gcc", postgres.configure_args(False))
            self.assertIn("CC=musl-gcc", postgres.configure_args(True))

    def test_guest_output_requires_the_packaged_non_default_uid(self):
        output = ("PASS shared-creator-exit\nPASS guest-ownership\nPASS postmaster-shutdown\n"
                  "PG_INIT postmaster=4 uid=1001\n"
                  "PG_INIT rejected invalid shutdown identity\n")
        postgres.validate_guest_output(output, 0, 1001)
        for uid in (0, 1000, 10010):
            with self.subTest(uid=uid), self.assertRaisesRegex(RuntimeError, "Non-root identity"):
                postgres.validate_guest_output(output, 0, uid)

    def test_exact_postgres_version(self):
        for version in ("16.15", "17.1", "18.7"):
            with patch.object(postgres.runtime, "pins", return_value={"postgres": {"version": version}}):
                postgres.check_version(f"postgres (PostgreSQL) {version}\n")
                for value in ("postgres (PostgreSQL) 16.4", "16.15", ""):
                    with self.subTest(version=version, value=value), self.assertRaises(RuntimeError):
                        postgres.check_version(value)

    def test_ram_profile_selects_sync_io_only_for_versions_with_aio(self):
        profile = postgres.PROFILE.read_text()
        for version in ("16.15", "17.12", "18.7"):
            with self.subTest(version=version), \
                    patch.object(postgres.runtime, "pins", return_value={"postgres": {"version": version}}):
                rendered = postgres.ram_profile()
                if version.startswith("18."):
                    self.assertEqual(rendered, profile + "\nio_method = sync\n")
                else:
                    self.assertEqual(rendered, profile)

    def test_pg18_rejects_async_io_or_disabled_checksums_without_querying_older_servers(self):
        session = Mock()
        for version in ("16.15", "17.12"):
            with patch.object(postgres.runtime, "pins", return_value={"postgres": {"version": version}}):
                postgres.verify_version_settings(session)
        session.execute.assert_not_called()
        with patch.object(postgres.runtime, "pins", return_value={"postgres": {"version": "18.7"}}):
            session.execute.return_value = ["sync|on"]
            postgres.verify_version_settings(session)
            for actual in (["worker|on"], ["io_uring|on"], ["sync|off"], []):
                session.execute.return_value = actual
                with self.subTest(actual=actual), self.assertRaisesRegex(RuntimeError, "SQL mismatch"):
                    postgres.verify_version_settings(session)

    def test_rss_threshold(self):
        postgres.check_rss_growth(1000, 33768)
        postgres.check_rss_growth(1000, 900)
        with self.assertRaises(RuntimeError):
            postgres.check_rss_growth(1000, 33769)

    def test_budget_counts_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "subdir").mkdir()
            (root / "one").write_bytes(b"abc")
            (root / "subdir/two").write_bytes(b"defg")
            self.assertEqual(postgres.bytes_in(root), 7)

    def test_sql_shape_and_values(self):
        class FakeSession:
            def execute(self, _):
                return ["1|committed"]
        postgres.expect(FakeSession(), "SELECT", ["1|committed"])
        with self.assertRaises(RuntimeError):
            postgres.expect(FakeSession(), "SELECT", ["1", "committed"])

    def test_packaging_rejects_stale_flags_and_mixed_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / "postgres-guest-build"
            (build / "src/backend").mkdir(parents=True)
            (build / "src/include").mkdir()
            config = build / "src/include/pg_config.h"
            config.write_text("/* #undef USE_ASSERT_CHECKING */\n")
            installed = root / "phase2/guest-install/usr/local/bin"
            installed.mkdir(parents=True)
            (build / "src/backend/postgres").write_bytes(b"verified")
            (installed / "postgres").write_bytes(b"verified")
            expected = {"configure": ["current-flags"], "patches": []}
            with patch.object(postgres, "WORK", root), patch.object(postgres, "DATA", root / "phase2"), \
                    patch.object(postgres, "postgres_inputs", return_value=expected):
                metadata = dict(expected, version=postgres.runtime.pins()["postgres"]["version"],
                                pg_config_sha256=postgres.runtime.digest(config),
                                installed_sha256=postgres.installed_hashes(True))
                (build / "experiment-inputs.json").write_text(json.dumps(metadata))
                postgres.verify_postgres_install(True)
                config.write_text("#define USE_ASSERT_CHECKING 1\n")
                with self.assertRaisesRegex(RuntimeError, "assertions disabled"):
                    postgres.verify_postgres_install(True)
                config.write_text("/* #undef USE_ASSERT_CHECKING */\n")
                (installed / "postgres").write_bytes(b"stale")
                with self.assertRaises(RuntimeError):
                    postgres.verify_postgres_install(True)
                (installed / "postgres").write_bytes(b"verified")
                (build / "experiment-inputs.json").write_text('{"configure":["old-flags"]}')
                with self.assertRaises(RuntimeError):
                    postgres.verify_postgres_install(True)

    def test_guest_and_native_comparison_flags_are_optimized_without_assertions(self):
        for guest in (False, True):
            args = postgres.configure_args(guest)
            self.assertIn("CFLAGS=-O2 -std=gnu17 -march=x86-64", args)
            self.assertNotIn("--enable-cassert", args)
        self.assertIn("--disable-cassert", postgres.configure_args(True))

    def test_build_generates_headers_before_compiling_under_an_outer_make(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "phase2").mkdir()
            with patch.object(postgres, "WORK", root), patch.object(postgres, "DATA", root / "phase2"), \
                    patch.object(postgres, "postgres_inputs", return_value={"configure": []}), \
                    patch.object(postgres, "installed_hashes", return_value={"bin/postgres": "sha256"}), \
                    patch.object(postgres.runtime, "digest", return_value="sha256"), \
                    patch.dict(postgres.runtime.os.environ, {"HL_PG_BUILD_JOBS": "2"}), \
                    patch.object(postgres.runtime, "run") as run:
                postgres.build_postgres(True)
                commands = [call.args[0] for call in run.call_args_list]
                headers = ["make", "-j2", "-C", "src/backend", "generated-headers"]
                self.assertIn(headers, commands)
                self.assertTrue(all("-j2" in args for args in commands if args[0] == "make"))
                compilation = next(i for i, args in enumerate(commands) if "all" in args)
                self.assertLess(commands.index(headers), compilation)

if __name__ == "__main__":
    unittest.main()
