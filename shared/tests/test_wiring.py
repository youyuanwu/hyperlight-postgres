from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import evaluate
import postgres
import recovery
import read_benchmark
import runtime
import storage


class WiringTests(unittest.TestCase):
    def test_root_and_version_makefiles_route_to_the_shared_implementation(self):
        repository = runtime.HERE.parent
        for directory, selection in ((repository, ["PG=16"]), (repository / "pg16", [])):
            result = subprocess.run(["make", "--no-print-directory", "-n", "-C", str(directory),
                                     *selection, "pg-native", "validate-built"],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f'"{runtime.HERE / "postgres.py"}" build-native', result.stdout)
            self.assertIn(f'"{runtime.HERE / "runtime.py"}" test-native-built', result.stdout)
            self.assertIn(f'"{runtime.HERE / "read_benchmark.py"}" test', result.stdout)
        result = subprocess.run(["make", "-C", str(repository), "PG=99", "pg-native"],
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported PostgreSQL version", result.stderr)

    def test_input_fingerprints_use_current_script_names(self):
        with patch.object(runtime, "digest", return_value="sha256"):
            for inputs, expected in (
                    (runtime.build_inputs, {"runtime.py"}),
                    (postgres.inputs, {"runtime.py", "postgres.py"}),
                    (storage.inputs, {"runtime.py", "postgres.py", "storage.py"})):
                scripts = {Path(path).name for path in inputs() if Path(path).suffix == ".py"}
                self.assertEqual(scripts, expected)
                self.assertIn(str(runtime.VERSION_DIR / "sources.toml"), inputs())
                self.assertIn(str(runtime.VERSION_DIR / "Makefile"), inputs())

    def test_all_validation_commands_load(self):
        for module in (runtime, postgres, storage, recovery, evaluate, read_benchmark):
            with self.subTest(command=module.__name__):
                result = subprocess.run([sys.executable, "-B", module.__file__, "--help"],
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)


if __name__ == "__main__":
    unittest.main()
