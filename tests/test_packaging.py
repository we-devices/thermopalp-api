from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_metadata_describes_api_only_distribution(self) -> None:
        metadata = tomllib.loads(
            (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        project = metadata["project"]

        self.assertEqual(project["name"], "thermopalp")
        self.assertEqual(project["dependencies"], ["pyserial>=3.5,<4"])
        self.assertEqual(project["license"], "MIT")
        self.assertNotIn("gui-scripts", project)
        self.assertNotIn("ui", project["optional-dependencies"])
        self.assertEqual(
            metadata["tool"]["setuptools"]["packages"]["find"]["include"],
            ["thermopalp*"],
        )

    def test_version_is_sourced_from_api_package(self) -> None:
        metadata = tomllib.loads(
            (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["project"]["dynamic"], ["version"])
        self.assertEqual(
            metadata["tool"]["setuptools"]["dynamic"]["version"]["attr"],
            "thermopalp.__version__",
        )

    def test_import_has_no_ui_dependency(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import thermopalp; "
                "print('thermopalp_ui' in sys.modules); "
                "print(thermopalp.__version__)",
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.splitlines(), ["False", "0.1.0"])

    def test_typed_package_marker_is_present(self) -> None:
        self.assertTrue((PROJECT_ROOT / "thermopalp" / "py.typed").is_file())


if __name__ == "__main__":
    unittest.main()
