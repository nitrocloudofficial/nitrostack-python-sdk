"""Clone nitrocloudofficial/skills-python-sdk and copy skills into agent folders."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

SKILLS_REPO_URL = "https://github.com/nitrocloudofficial/skills-python-sdk.git"
SKILLS_SUBDIR = "skills"
STAMP_FILENAME = ".nitrostack.json"
CLONE_TIMEOUT_S = 60

AGENT_SKILL_DIRS = (
    ".cursor/skills",
    ".codex/skills",
    ".claude/skills",
    ".gemini/skills",
    ".antigravity/skills",
    ".copilot/skills",
    ".opencode/skills",
    ".agents/skills",
)


class SkillsCloneError(Exception):
    pass


def clone_skills_repo() -> str:
    temp_dir = os.path.join(tempfile.gettempdir(), f"nitrostack-skills-{os.urandom(6).hex()}")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", SKILLS_REPO_URL, temp_dir],
            check=True,
            capture_output=True,
            text=True,
            timeout=CLONE_TIMEOUT_S,
            env=env,
        )
    except FileNotFoundError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise SkillsCloneError(
            "Git is not installed or not in PATH. Install Git from https://git-scm.com and try again."
        ) from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or str(exc)).split("\n")[0]
        else:
            detail = str(exc)
        raise SkillsCloneError(f"Failed to clone skills repository: {detail}") from exc
    return temp_dir


def discover_skills(clone_dir: str) -> list[tuple[str, str]]:
    root = os.path.join(clone_dir, SKILLS_SUBDIR)
    if not os.path.isdir(root):
        return []
    skills = []
    for name in sorted(os.listdir(root)):
        if name.startswith("."):
            continue
        source = os.path.join(root, name)
        if os.path.isdir(source):
            skills.append((name, source))
    return skills


def read_skills_version(clone_dir: str) -> str:
    path = os.path.join(clone_dir, "package.json")
    if not os.path.isfile(path):
        return "1.0.0"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return str(data.get("version") or "1.0.0")
    except (OSError, json.JSONDecodeError):
        return "1.0.0"


def stamp_path(project_dir: str) -> str:
    return os.path.join(project_dir, STAMP_FILENAME)


def read_local_skills_version(project_dir: str) -> str:
    path = stamp_path(project_dir)
    if not os.path.isfile(path):
        return "0.0.0"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return str(data.get("skillsVersion") or "0.0.0")
    except (OSError, json.JSONDecodeError):
        return "0.0.0"


def write_local_skills_version(project_dir: str, version: str) -> None:
    with open(stamp_path(project_dir), "w", encoding="utf-8") as fh:
        json.dump({"skillsVersion": version}, fh, indent=2)
        fh.write("\n")


def _version_key(value: str) -> tuple:
    parts = []
    for piece in value.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def install_skills(project_dir: str, skills: list[tuple[str, str]], force: bool) -> None:
    for rel in AGENT_SKILL_DIRS:
        dest_root = os.path.join(project_dir, rel)
        os.makedirs(dest_root, exist_ok=True)
        for name, source in skills:
            dest = os.path.join(dest_root, name)
            if os.path.exists(dest) and not force:
                continue
            if os.path.exists(dest):
                shutil.rmtree(dest)
            shutil.copytree(source, dest)


def run_skills_flow(project_dir: str, force: bool = False) -> None:
    print("\nInstalling agent skills...")
    try:
        clone_dir = clone_skills_repo()
    except SkillsCloneError as err:
        print(f"Warning: {err}")
        print("Skipped agent skills. Init continues.\n")
        return
    try:
        skills = discover_skills(clone_dir)
        if not skills:
            print("Warning: No skills found in the repository.\n")
            return
        install_skills(project_dir, skills, force)
        write_local_skills_version(project_dir, read_skills_version(clone_dir))
        print("\033[32m✓\033[0m Agent skills installed\n")
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)


def upgrade_agent_skills(project_dir: str, *, dry_run: bool = False) -> dict:
    """Reinstall project skills when the remote package.json version is newer."""
    try:
        clone_dir = clone_skills_repo()
    except SkillsCloneError as err:
        print(f"Warning: Could not refresh agent skills: {err}")
        return {"upgraded": False, "error": str(err)}
    try:
        remote = read_skills_version(clone_dir)
        local = read_local_skills_version(project_dir)
        if _version_key(local) >= _version_key(remote):
            print(f"Skills: already up to date ({local})")
            return {"upgraded": False, "local": local, "remote": remote, "dry_run": dry_run}
        if dry_run:
            print(f"Skills: updates available ({local} → {remote})")
            return {"upgraded": False, "local": local, "remote": remote, "dry_run": True}
        skills = discover_skills(clone_dir)
        install_skills(project_dir, skills, force=True)
        write_local_skills_version(project_dir, remote)
        print(f"Skills: upgraded {local} → {remote}")
        return {"upgraded": True, "local": local, "remote": remote, "dry_run": False}
    finally:
        shutil.rmtree(clone_dir, ignore_errors=True)
