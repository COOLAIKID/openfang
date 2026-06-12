"""Claude-skill compatibility layer.

A "skill" here follows the Claude convention: a directory containing a
``SKILL.md`` file whose YAML-ish frontmatter declares at least ``name`` and
``description``, with the markdown body holding the instructions. Skills live in
the ``skills/`` directory and can be **installed at runtime** from:

* a local directory path,
* a git repository URL (``...git`` or a GitHub URL),
* a ``.zip`` URL.

Installed skills are exposed two ways:

* to the user, via the dashboard Skills tab and ``/skill`` slash commands;
* to agents, via the :func:`tools.use_skill` tool — invoking a skill runs the
  skill's instructions (its SKILL.md body) as a system prompt over the agent's
  input through the normal AI cascade.

We deliberately do **not** auto-execute bundled scripts: a skill's power is its
instructions. Bundled files remain on disk for an agent to read via ``fetch_url``
/ ``http_request`` or for the user to inspect.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from . import ai_client, database as db

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "skills"


# --------------------------------------------------------------------------
# Frontmatter parsing
# --------------------------------------------------------------------------
def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a SKILL.md into (frontmatter dict, body). Tolerant of no fence."""
    meta: dict[str, str] = {}
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if m:
        block, body = m.group(1), m.group(2)
        for line in block.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                meta[key.strip().lower()] = val.strip().strip("\"'")
    return meta, body.strip()


def _skill_md(skill_dir: Path) -> Path | None:
    for candidate in ("SKILL.md", "skill.md", "Skill.md"):
        p = skill_dir / candidate
        if p.exists():
            return p
    return None


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------
def discover() -> list[dict[str, Any]]:
    """Return metadata for every installed skill."""
    SKILLS_DIR.mkdir(exist_ok=True)
    out: list[dict[str, Any]] = []
    for child in sorted(SKILLS_DIR.iterdir()):
        if not child.is_dir():
            continue
        md = _skill_md(child)
        if md is None:
            continue
        meta, body = _parse_frontmatter(md.read_text(encoding="utf-8", errors="ignore"))
        out.append(
            {
                "name": meta.get("name", child.name),
                "dir": child.name,
                "description": meta.get("description", ""),
                "instructions_chars": len(body),
                "files": [str(p.relative_to(child)) for p in child.rglob("*") if p.is_file()][:50],
            }
        )
    return out


def get(name: str) -> dict[str, Any] | None:
    for skill in discover():
        if skill["name"] == name or skill["dir"] == name:
            md = _skill_md(SKILLS_DIR / skill["dir"])
            if md:
                _, body = _parse_frontmatter(md.read_text(encoding="utf-8", errors="ignore"))
                skill = dict(skill)
                skill["instructions"] = body
            return skill
    return None


# --------------------------------------------------------------------------
# Installation
# --------------------------------------------------------------------------
def install(source: str) -> dict[str, Any]:
    """Install a skill from a local path, git URL, or zip URL. Returns its metadata."""
    SKILLS_DIR.mkdir(exist_ok=True)
    source = source.strip()

    if _looks_like_zip(source):
        name = _install_zip(source)
    elif _looks_like_git(source):
        name = _install_git(source)
    elif Path(source).exists():
        name = _install_local(Path(source))
    else:
        raise ValueError(
            f"Don't know how to install '{source}'. Provide a local directory path, "
            "a git URL (…git or a GitHub URL), or a .zip URL."
        )

    skill = get(name)
    if skill is None:
        raise ValueError(f"Installed '{name}' but no valid SKILL.md was found inside it.")
    db.log_activity("skills", "install", f"{skill['name']} ({source})")
    return skill


def remove(name: str) -> bool:
    skill = get(name)
    if skill is None:
        return False
    shutil.rmtree(SKILLS_DIR / skill["dir"], ignore_errors=True)
    db.log_activity("skills", "remove", name)
    return True


def _looks_like_git(source: str) -> bool:
    return source.endswith(".git") or ("github.com" in source and not source.endswith(".zip"))


def _looks_like_zip(source: str) -> bool:
    return source.lower().endswith(".zip") and source.startswith(("http://", "https://"))


def _install_local(path: Path) -> str:
    if not path.is_dir():
        raise ValueError(f"'{path}' is not a directory.")
    name = path.name
    dest = SKILLS_DIR / name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(path, dest)
    return _normalize(dest)


def _install_git(url: str) -> str:
    name = re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1]) or "skill"
    dest = SKILLS_DIR / name
    if dest.exists():
        shutil.rmtree(dest)
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    shutil.rmtree(dest / ".git", ignore_errors=True)
    return _normalize(dest)


def _install_zip(url: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / "skill.zip"
        with urlopen(url, timeout=180) as resp:  # noqa: S310 - user-provided URL by design
            archive.write_bytes(resp.read())
        extract_dir = tmp_path / "extract"
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract_dir)
        # If the zip wraps everything in a single top folder, descend into it.
        children = [c for c in extract_dir.iterdir() if c.is_dir()]
        root_dir = children[0] if len(children) == 1 and _skill_md(children[0]) else extract_dir
        name = url.rstrip("/").split("/")[-1].removesuffix(".zip") or "skill"
        dest = SKILLS_DIR / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(root_dir, dest)
        return _normalize(dest)


def _normalize(dest: Path) -> str:
    """Ensure the installed dir has a SKILL.md at its top level; return dir name."""
    if _skill_md(dest) is None:
        # Look one level down for a folder that contains SKILL.md.
        for child in dest.iterdir():
            if child.is_dir() and _skill_md(child):
                for item in child.iterdir():
                    shutil.move(str(item), str(dest / item.name))
                shutil.rmtree(child, ignore_errors=True)
                break
    if _skill_md(dest) is None:
        raise ValueError(f"No SKILL.md found in installed skill at {dest.name}.")
    return dest.name


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------
def run(name: str, user_input: str, model_preference: str | None = None) -> str:
    """Run a skill: its instructions become the system prompt over the input."""
    skill = get(name)
    if skill is None:
        return f"ERROR: no skill named '{name}'. Install it first."
    system = (
        f"You are executing the Claude skill '{skill['name']}'. "
        f"{skill['description']}\n\nFollow these skill instructions exactly:\n\n"
        f"{skill.get('instructions', '')}"
    )
    try:
        result = ai_client.ask(user_input or "Begin.", system=system, model_preference=model_preference)
        db.log_activity("skills", "run", f"{name}: {user_input[:60]}")
        return result.text
    except ai_client.AIError as exc:
        return f"ERROR: skill '{name}' could not run (no AI provider): {exc}"
