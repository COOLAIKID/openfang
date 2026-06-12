"""Per-agent Docker sandbox manager.

Each agent gets its own persistent, isolated Linux container:
  - Base image: python:3.11-slim
  - Pre-installed: pip, requests, beautifulsoup4, lxml
  - /output bind-mounted from the host project (shared with all agents)
  - 256 MB RAM cap, 1 CPU cap
  - Runs ``sleep infinity`` — stays up between agent ticks

The Docker CLI (subprocess) is used so no extra Python package is required.
If Docker is unavailable, every tool returns a readable error string and the org
continues without sandbox support.

Container names follow the pattern: autoearn_<agent>_sandbox
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

BASE_IMAGE = "python:3.11-slim"
CONTAINER_PREFIX = "autoearn_"

_lock = threading.Lock()
_bootstrapped: set[str] = set()   # container names we've already set up


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------

def _docker(*args: str, stdin: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    """Run ``docker <args>`` and return (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ["docker", *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", "docker: command not found. Install Docker to use sandboxes."
    except subprocess.TimeoutExpired:
        return 1, "", "docker command timed out"


def _docker_available() -> bool:
    rc, _, _ = _docker("info", "--format", "{{.ServerVersion}}", timeout=8)
    return rc == 0


def _container_name(agent: str) -> str:
    safe = "".join(c for c in agent.lower() if c.isalnum() or c == "_")
    return f"{CONTAINER_PREFIX}{safe}_sandbox"


def _ensure_container(agent: str) -> str:
    """Return the name of a running container for agent, creating one if needed."""
    name = _container_name(agent)
    with _lock:
        rc, running, _ = _docker(
            "inspect", "--format", "{{.State.Running}}", name, timeout=10
        )
        if rc == 0:
            if running != "true":
                _docker("start", name, timeout=30)
        else:
            # Container doesn't exist — create it.
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            rc2, _, err = _docker(
                "run", "-d",
                "--name", name,
                "--memory", "256m",
                "--cpus", "1",
                "--network", "bridge",
                "-v", f"{OUTPUT_DIR}:/output:rw",
                "--workdir", "/workspace",
                "--restart", "unless-stopped",
                BASE_IMAGE,
                "sh", "-c", "mkdir -p /workspace && sleep infinity",
                timeout=120,
            )
            if rc2 != 0:
                raise RuntimeError(f"docker run failed: {err}")

        if name not in _bootstrapped:
            # Install baseline Python packages once.
            _docker(
                "exec", name,
                "pip", "install", "--quiet", "--no-cache-dir",
                "requests", "beautifulsoup4", "lxml",
                timeout=180,
            )
            _bootstrapped.add(name)

    return name


# --------------------------------------------------------------------------
# Public API used by the tool registry
# --------------------------------------------------------------------------

def exec_cmd(agent_name: str, command: str = "", timeout: int = 30) -> str:
    """Run a shell command inside the agent's container. Returns stdout/stderr."""
    if not _docker_available():
        return (
            "ERROR: Docker is not running. "
            "Install and start Docker to use per-agent sandboxes."
        )
    try:
        name = _ensure_container(agent_name)
    except RuntimeError as exc:
        return f"ERROR creating sandbox: {exc}"

    rc, out, err = _docker("exec", name, "sh", "-c", command, timeout=int(timeout) + 15)
    combined = (out + ("\n" + err if err else "")).strip()
    return f"[exit {rc}]\n{combined}"[:3000]


def install_package(agent_name: str, package: str = "", manager: str = "pip") -> str:
    """Install a package in the agent's container (pip or apt)."""
    if not _docker_available():
        return "ERROR: Docker not available."
    if manager == "pip":
        cmd = f"pip install --quiet --no-cache-dir {package}"
        t = 120
    elif manager == "apt":
        cmd = f"apt-get update -qq && apt-get install -y --no-install-recommends {package}"
        t = 180
    else:
        return f"ERROR: Unknown package manager '{manager}'. Use 'pip' or 'apt'."
    return exec_cmd(agent_name, cmd, timeout=t)


def browse_url(agent_name: str, url: str = "") -> str:
    """Fetch a URL inside the agent's container and return readable page text."""
    if not _docker_available():
        return "ERROR: Docker not available."
    script = (
        "import os, requests\n"
        "from bs4 import BeautifulSoup\n"
        "url = os.environ['_URL']\n"
        "resp = requests.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=20)\n"
        "soup = BeautifulSoup(resp.text, 'html.parser')\n"
        "for t in soup(['script','style','nav','footer']): t.decompose()\n"
        "print(soup.get_text(' ')[:4000])\n"
    )
    try:
        name = _ensure_container(agent_name)
        proc = subprocess.run(
            ["docker", "exec", "-e", f"_URL={url}", "-i", name, "python3"],
            input=script,
            capture_output=True,
            text=True,
            timeout=45,
        )
        return (proc.stdout or proc.stderr)[:4000]
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def write_file(agent_name: str, path: str = "", content: str = "") -> str:
    """Write content to a file path inside the agent's container."""
    if not _docker_available():
        return "ERROR: Docker not available."
    try:
        name = _ensure_container(agent_name)
        parent = str(Path(path).parent)
        _docker("exec", name, "mkdir", "-p", parent, timeout=10)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".tmp", delete=False) as f:
            f.write(content)
            tmp = f.name
        rc, _, err = _docker("cp", tmp, f"{name}:{path}", timeout=30)
        Path(tmp).unlink(missing_ok=True)
        if rc != 0:
            return f"ERROR: cp failed: {err}"
        return f"Written {len(content)} bytes to {path}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def read_file(agent_name: str, path: str = "") -> str:
    """Read a file from inside the agent's container."""
    return exec_cmd(agent_name, f"cat {path}", timeout=15)


def list_files(agent_name: str, path: str = "/workspace") -> str:
    """List files in a directory inside the agent's container."""
    return exec_cmd(agent_name, f"ls -la {path}", timeout=15)


def sandbox_status() -> str:
    """List all running AutoEarn sandbox containers as JSON."""
    if not _docker_available():
        return "[]"
    rc, out, _ = _docker(
        "ps",
        "--filter", f"name={CONTAINER_PREFIX}",
        "--format", "{{.Names}}\t{{.Status}}\t{{.Size}}",
        timeout=15,
    )
    if rc != 0 or not out:
        return "[]"
    rows: list[dict] = []
    for line in out.strip().split("\n"):
        parts = line.split("\t")
        rows.append({
            "name": parts[0] if parts else "?",
            "status": parts[1] if len(parts) > 1 else "?",
            "size": parts[2] if len(parts) > 2 else "?",
        })
    return json.dumps(rows)


def destroy_sandbox(agent_name: str) -> str:
    """Stop and remove the agent's sandbox container."""
    if not _docker_available():
        return "ERROR: Docker not available."
    name = _container_name(agent_name)
    _docker("rm", "-f", name, timeout=30)
    _bootstrapped.discard(name)
    return f"Destroyed sandbox: {name}"


def rebuild_sandbox(agent_name: str) -> str:
    """Destroy then recreate the agent's sandbox (fresh environment)."""
    destroy_sandbox(agent_name)
    try:
        _ensure_container(agent_name)
        return f"Sandbox rebuilt for {agent_name}."
    except RuntimeError as exc:
        return f"ERROR: {exc}"
