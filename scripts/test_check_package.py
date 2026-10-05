"""Behavioral checks for the public package boundary."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_package


class PackageCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="sofa-package-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = Path(__file__).resolve().parents[1]
        for directory in (".claude-plugin", ".github", "scripts", "skills"):
            shutil.copytree(source / directory, self.root / directory)
        for filename in (".mcp.json", "README.md", "plugin-guidance.json"):
            shutil.copy2(source / filename, self.root / filename)

    def check_staged_package(self) -> None:
        with patch.object(check_package, "ROOT", self.root):
            check_package.check_package()

    def test_intact_package_and_optional_license(self) -> None:
        self.check_staged_package()
        (self.root / "LICENSE").write_text("License text pending policy decision.\n")
        self.check_staged_package()

    def test_extra_root_env_is_rejected(self) -> None:
        (self.root / ".env").write_text("UNRELATED=value\n")
        with self.assertRaisesRegex(ValueError, "unexpected package file"):
            self.check_staged_package()

    def test_extra_component_and_symlink_are_rejected(self) -> None:
        component = self.root / "hooks"
        component.mkdir()
        (component / "hooks.json").write_text("{}\n")
        with self.assertRaisesRegex(ValueError, "unexpected package directory"):
            self.check_staged_package()

        shutil.rmtree(component)
        component.symlink_to("skills", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "linked package path"):
            self.check_staged_package()

    def test_credential_assignment_in_readme_is_rejected(self) -> None:
        readme = self.root / "README.md"
        with readme.open("a", encoding="utf-8") as stream:
            stream.write("\n" + "SOFA_API_KEY" + "=" + "example_token_123\n")
        with self.assertRaisesRegex(ValueError, "possible credential assignment"):
            self.check_staged_package()


if __name__ == "__main__":
    unittest.main()
