"""Behavioral checks for the public package boundary."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
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
        for filename in (".mcp.json", "LICENSE", "README.md", "plugin-guidance.json"):
            shutil.copy2(source / filename, self.root / filename)

    def check_staged_package(self) -> None:
        with patch.object(check_package, "ROOT", self.root):
            check_package.check_package()

    def set_version(self, version: str) -> None:
        manifest_path = self.root / ".claude-plugin/plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = version
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    def commit_baseline(self) -> str:
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(
            [
                "git", "-c", "user.name=SOFA Test", "-c", "user.email=test@example.com",
                "-c", "commit.gpgsign=false", "commit", "-qm", "baseline",
            ],
            cwd=self.root,
            check=True,
        )
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()

    def run_version_check(self, base_sha: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", "scripts/check_package.py", "--base-sha", base_sha],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_intact_package_with_license(self) -> None:
        self.check_staged_package()

    def test_missing_license_is_rejected(self) -> None:
        (self.root / "LICENSE").unlink()
        with self.assertRaisesRegex(ValueError, "missing package files: .*LICENSE"):
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

    def test_different_author_name_is_rejected(self) -> None:
        manifest_path = self.root / ".claude-plugin/plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["author"]["name"] = "Stack Exchange, Inc."
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "manifest author"):
            self.check_staged_package()

    def test_changed_skill_requires_higher_version(self) -> None:
        base_sha = self.commit_baseline()
        skill = self.root / "skills/sofa/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nUpdated guidance.\n", encoding="utf-8")

        result = self.run_version_check(base_sha)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("version", result.stderr)

    def test_numeric_version_increase_allows_changed_guidance(self) -> None:
        self.set_version("0.1.9")
        base_sha = self.commit_baseline()
        guidance = self.root / "plugin-guidance.json"
        guidance.write_text(guidance.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.set_version("0.1.10")

        self.assertEqual(self.run_version_check(base_sha).returncode, 0)

    def test_unrelated_change_does_not_require_version_bump(self) -> None:
        base_sha = self.commit_baseline()
        readme = self.root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\nA documentation correction.\n", encoding="utf-8")

        self.assertEqual(self.run_version_check(base_sha).returncode, 0)

    def test_version_decrease_fails_without_guidance_change(self) -> None:
        self.set_version("0.2.0")
        base_sha = self.commit_baseline()
        self.set_version("0.1.9")

        result = self.run_version_check(base_sha)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("version", result.stderr)

    def test_empty_base_sha_fails_closed(self) -> None:
        result = self.run_version_check("")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("base SHA", result.stderr)


if __name__ == "__main__":
    unittest.main()
