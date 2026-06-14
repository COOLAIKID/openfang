"""Home connector — let your own computer work for the cloud dashboard.

Your computer runs this; it dials *out* to your cloud dashboard (so it works
behind any home router with no ports to open), then picks up and runs jobs:

  • shell — run a command on your computer
  • agent — run one of your agents locally (in its own container if Docker is on)

Every request carries a bearer token derived from your dashboard password, so
only your machines can connect. Pure standard library — no extra installs.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import platform
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request


def session_token(password: str) -> str:
    """Same token the dashboard expects (matches dashboard/auth.py)."""
    return hmac.new(password.encode("utf-8"), b"autoearn-session-v1", hashlib.sha256).hexdigest()


def _has_docker() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=5).returncode == 0
    except Exception:
        return False


class Runner:
    def __init__(self, base_url: str, password: str = "", machine: str | None = None,
                 poll_interval: float = 3.0) -> None:
        self.base = base_url.rstrip("/")
        self.password = password
        self.machine = machine or socket.gethostname()
        self.poll_interval = poll_interval

    # -- HTTP -----------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.password:
            h["Authorization"] = "Bearer " + session_token(self.password)
        return h

    def _req(self, method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=self._headers(), method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else {}

    # -- API ------------------------------------------------------------
    def _info(self) -> str:
        return json.dumps({
            "os": platform.system(),
            "release": platform.release(),
            "docker": _has_docker(),
            "python": platform.python_version(),
        })

    def register(self) -> None:
        self._req("POST", "/api/runner/register", {"name": self.machine, "info": self._info()})

    def poll(self) -> dict:
        return self._req("GET", "/api/runner/poll?machine=" + urllib.parse.quote(self.machine))

    def report(self, job_id: int, status: str, result: str) -> None:
        self._req("POST", "/api/runner/result",
                  {"job_id": job_id, "status": status, "result": result[:8000]})

    # -- Execution ------------------------------------------------------
    def execute(self, job: dict) -> tuple[str, str]:
        kind = job.get("kind")
        payload = job.get("payload") or ""
        try:
            if kind == "shell":
                out = subprocess.run(payload, shell=True, capture_output=True, text=True, timeout=600)
                res = (out.stdout + out.stderr).strip() or "(no output)"
                return ("done" if out.returncode == 0 else "error"), res
            if kind == "agent":
                return self._run_agent(payload)
            return "error", f"unknown job kind: {kind}"
        except subprocess.TimeoutExpired:
            return "error", "timed out after 600s"
        except Exception as exc:  # noqa: BLE001
            return "error", str(exc)

    def _run_agent(self, name: str) -> tuple[str, str]:
        from core.agent_manager import AgentManager  # local import: runner runs from autoearn/

        mgr = AgentManager()
        mgr.discover()
        agent = mgr.get(name)
        if agent is None:
            return "error", f"no such agent '{name}' on this computer"
        return "done", agent.run()

    # -- Loop -----------------------------------------------------------
    def loop(self) -> None:
        print(f"[connector] {self.machine}  →  {self.base}")
        backoff = 1
        while True:
            try:
                self.register()
                job = self.poll()
                if job and job.get("id"):
                    print(f"[connector] job #{job['id']} {job.get('kind')}: {str(job.get('payload'))[:60]}")
                    status, result = self.execute(job)
                    self.report(job["id"], status, result)
                    print(f"[connector]   → {status}")
                    backoff = 1
                    continue
                backoff = 1
                time.sleep(self.poll_interval)
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    print("[connector] auth failed — check AUTOEARN_PASSWORD matches the dashboard.")
                    time.sleep(10)
                else:
                    time.sleep(min(30, backoff)); backoff *= 2
            except Exception as exc:  # noqa: BLE001
                print(f"[connector] connection issue, retrying: {exc}")
                time.sleep(min(30, backoff)); backoff *= 2
