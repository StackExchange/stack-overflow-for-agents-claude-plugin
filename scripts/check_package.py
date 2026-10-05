#!/usr/bin/env python3
"""Check the public Claude plugin's local structure and connection boundary."""

from __future__ import annotations

import json
import re
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("sofa", "sofa-contribute", "sofa-status")
MCP_URL = "https://agents.stackoverflow.com/mcp"


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
    if manifest.get("author", {}).get("name") != "Stack Exchange, Inc.":
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


if __name__ == "__main__":
    try:
        check_package()
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        sys.stderr.write(f"Package validation failed: {error}\n")
        raise SystemExit(1) from error
    sys.stdout.write("Package validation passed\n")
