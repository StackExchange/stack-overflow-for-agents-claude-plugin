#!/usr/bin/env python3
"""Check the public Claude plugin's local structure and connection boundary."""

from __future__ import annotations

import argparse
import json
import re
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("sofa", "sofa-contribute", "sofa-status")
MCP_URL = "https://agents.stackoverflow.com/mcp"
VERSIONED_CONTENT = (
    ".mcp.json",
    "plugin-guidance.json",
    *(f"skills/{name}/SKILL.md" for name in SKILLS),
)
PACKAGE_DIRECTORIES = {
    ".claude-plugin",
    ".github",
    ".github/workflows",
    "scripts",
    "skills",
    *(f"skills/{name}" for name in SKILLS),
}
PACKAGE_FILES = {
    ".claude-plugin/plugin.json",
    ".github/workflows/validate.yml",
    ".mcp.json",
    "LICENSE",
    "README.md",
    "plugin-guidance.json",
    "scripts/check_package.py",
    "scripts/test_check_package.py",
    *(f"skills/{name}/SKILL.md" for name in SKILLS),
}
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:export\s+)?[a-z][a-z0-9_]*(?:api_key|access_token|auth_token|secret|password|private_key)\s*=\s*\S+"
)


def check_tree() -> None:
    """Reject hidden, linked, and unrecognized payloads as well as missing members."""
    found_directories: set[str] = set()
    found_files: set[str] = set()
    pending = [ROOT]

    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            relative = path.relative_to(ROOT).as_posix()
            if relative == ".git":
                continue
            if path.is_symlink():
                raise ValueError(f"linked package path: {relative}")
            if path.is_dir():
                if relative not in PACKAGE_DIRECTORIES:
                    raise ValueError(f"unexpected package directory: {relative}")
                found_directories.add(relative)
                pending.append(path)
            elif path.is_file() and stat.S_ISREG(path.stat().st_mode):
                if relative not in PACKAGE_FILES:
                    raise ValueError(f"unexpected package file: {relative}")
                found_files.add(relative)
                if CREDENTIAL_ASSIGNMENT.search(path.read_text(encoding="utf-8")):
                    raise ValueError(f"possible credential assignment in package file: {relative}")
            else:
                raise ValueError(f"unsupported package path: {relative}")

    if missing := PACKAGE_DIRECTORIES - found_directories:
        raise ValueError(f"missing package directories: {sorted(missing)}")
    if missing := PACKAGE_FILES - found_files:
        raise ValueError(f"missing package files: {sorted(missing)}")


def require_regular(relative: str) -> Path:
    path = ROOT
    for part in Path(relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f"linked package path: {relative}")
    if not path.exists() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"missing or non-regular package file: {relative}")
    return path


def read_json(relative: str) -> dict:
    value = json.loads(require_regular(relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {relative}")
    return value


def check_package() -> None:
    check_tree()

    manifest_dir = ROOT / ".claude-plugin"
    if manifest_dir.is_symlink() or not manifest_dir.is_dir():
        raise ValueError(".claude-plugin must be a directory")
    if {path.name for path in manifest_dir.iterdir()} != {"plugin.json"}:
        raise ValueError(".claude-plugin must contain only plugin.json")

    skills_dir = ROOT / "skills"
    if skills_dir.is_symlink() or not skills_dir.is_dir():
        raise ValueError("skills must be a directory")
    if {path.name for path in skills_dir.iterdir()} != set(SKILLS):
        raise ValueError("skills must contain exactly the three SOFA workflows")
    for name in SKILLS:
        skill_dir = skills_dir / name
        if skill_dir.is_symlink() or not skill_dir.is_dir():
            raise ValueError(f"skill must be a directory: {name}")
        if {path.name for path in skill_dir.iterdir()} != {"SKILL.md"}:
            raise ValueError(f"skill must contain only SKILL.md: {name}")
        if not require_regular(f"skills/{name}/SKILL.md").read_text(encoding="utf-8").strip():
            raise ValueError(f"skill is empty: {name}")

    manifest = read_json(".claude-plugin/plugin.json")
    if manifest.get("name") != "sofa":
        raise ValueError("manifest name must be sofa")
    if manifest.get("displayName") != "Stack Overflow for Agents":
        raise ValueError("manifest displayName is missing or incorrect")
    if not isinstance(manifest.get("version"), str) or not re.fullmatch(
        r"\d+\.\d+\.\d+", manifest["version"]
    ):
        raise ValueError("manifest version must be major.minor.patch")
    if not isinstance(manifest.get("description"), str) or not manifest["description"].strip():
        raise ValueError("manifest description is required")
    if manifest.get("author", {}).get("name") != "Stack Overflow":
        raise ValueError("manifest author is missing or incorrect")
    for key in ("repository", "homepage", "supportUrl", "privacyPolicyUrl", "termsOfServiceUrl"):
        value = manifest.get(key)
        if not isinstance(value, str) or not value.startswith("https://"):
            raise ValueError(f"manifest {key} must be an HTTPS URL")
    if "mcpServers" in manifest or "skills" in manifest:
        raise ValueError("default MCP and skill locations must not be duplicated in the manifest")

    mcp = read_json(".mcp.json")
    if mcp != {"mcpServers": {"sofa": {"type": "http", "url": MCP_URL}}}:
        raise ValueError("MCP config must contain only the fixed hosted SOFA server")

    guidance = read_json("plugin-guidance.json")
    if guidance.get("surface") != "public_directory":
        raise ValueError("plugin guidance must target the public directory surface")
    if not isinstance(guidance.get("version"), str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", str(guidance.get("digest"))
    ):
        raise ValueError("plugin guidance is missing a version or SHA-256 digest")

    readme = require_regular("README.md").read_text(encoding="utf-8")
    prose = re.sub(r"```.*?```", "", readme, flags=re.DOTALL)
    if len(re.findall(r"\b[\w]+\b", prose)) < 40:
        raise ValueError("README needs at least 40 non-code words")


def version_parts(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("base or proposed manifest version must be major.minor.patch")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def check_version_against_base(base_sha: str) -> None:
    """Require a higher reviewer-chosen version when release content changes."""
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise ValueError("base SHA must be a full Git commit ID")

    baseline = subprocess.run(
        ["git", "show", f"{base_sha}:.claude-plugin/plugin.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if baseline.returncode != 0:
        raise ValueError("cannot read the base plugin manifest")

    base_version = version_parts(json.loads(baseline.stdout).get("version"))
    proposed_version = version_parts(read_json(".claude-plugin/plugin.json").get("version"))
    if proposed_version < base_version:
        raise ValueError("manifest version must not decrease from public main")

    difference = subprocess.run(
        ["git", "diff", "--quiet", base_sha, "--", *VERSIONED_CONTENT],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if difference.returncode not in (0, 1):
        raise ValueError("cannot compare release content with public main")
    if difference.returncode == 1 and proposed_version == base_version:
        raise ValueError("changed plugin guidance or behavior needs a higher manifest version")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-sha", help="public main commit to compare for PR version policy")
    args = parser.parse_args()
    try:
        check_package()
        if args.base_sha is not None:
            check_version_against_base(args.base_sha)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f"Package validation failed: {error}\n")
        raise SystemExit(1) from error
    sys.stdout.write("Package validation passed\n")
