"""Static site and serverless deployment connectors.

Connectors for deploying and managing sites on modern hosting platforms:
Netlify, Vercel, Cloudflare Pages, and GitHub Pages. Each connector wraps the
platform's REST API (or GraphQL where necessary) and returns
:class:`ConnectorResult` instances — so unconfigured platforms are gracefully
skipped rather than raising.

Config sections expected in config.toml
---------------------------------------
[netlify]
api_token = "..."
site_id   = "..."   # optional default site

[vercel]
token   = "..."
team_id = "..."     # optional — required for team-owned projects

[cloudflare]
api_token    = "..."
account_id   = "..."
project_name = "..."  # optional default project

[github]
token = "ghp_..."
repo  = "owner/repo"  # optional default repo (e.g. 'acme/acme.github.io')
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import requests

from .base import Connector, ConnectorResult, register

# ---------------------------------------------------------------------------
# Module-level registry helpers
# ---------------------------------------------------------------------------
_HOSTING_NAMES: list[str] = []


def configured() -> list[Connector]:
    """Return instantiated connectors for every hosting backend that has valid credentials."""
    from .base import _REGISTRY  # noqa: PLC0415

    result: list[Connector] = []
    for name in _HOSTING_NAMES:
        cls = _REGISTRY.get(name)
        if cls:
            inst = cls()
            if inst.is_configured():
                result.append(inst)
    return result


def get(name: str) -> Connector | None:
    """Return an instantiated hosting connector by registry name, or None."""
    from .base import _REGISTRY  # noqa: PLC0415

    cls = _REGISTRY.get(name)
    return cls() if cls else None


def _hosting_register(cls: type[Connector]) -> type[Connector]:
    """Wrap base.register and track hosting connector names."""
    register(cls)
    _HOSTING_NAMES.append(cls.name)
    return cls


# ---------------------------------------------------------------------------
# NetlifyConnector
# ---------------------------------------------------------------------------

@_hosting_register
class NetlifyConnector(Connector):
    """Netlify hosting connector.

    Implements ``deploy_site``, ``list_sites``, ``create_site``,
    ``delete_site``, ``get_site_info``, and ``set_env_vars``.

    The ``deploy_site`` method uses Netlify's file-digest deployment flow:
    it computes SHA-1 hashes for every file in a local directory, tells
    Netlify which files exist, then uploads only the files whose hash Netlify
    does not already hold on its CDN.
    """

    name = "hosting_netlify"
    label = "Netlify"
    config_section = "netlify"
    required_keys = ("api_token",)
    capabilities = ("hosting", "deploy", "static_site")

    _BASE = "https://api.netlify.com/api/v1"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {cfg['api_token']}",
            "Content-Type": "application/json",
        }

    def deploy_site(
        self,
        local_dir: str,
        site_id: str | None = None,
        message: str = "",
    ) -> ConnectorResult:
        """Deploy a local directory to a Netlify site.

        Uses Netlify's file-digest (atomic) deploy API so only changed files
        are actually transferred on re-deploys.

        Args:
            local_dir: absolute path to the directory to deploy.
            site_id: Netlify site ID; falls back to config ``site_id`` key.
            message: optional deploy message shown in the Netlify dashboard.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        site_id = site_id or cfg.get("site_id", "")
        if not site_id:
            return ConnectorResult(ok=False, message="Netlify deploy_site: site_id not specified.")
        dir_path = Path(local_dir)
        if not dir_path.is_dir():
            return ConnectorResult(ok=False, message=f"Netlify deploy_site: '{local_dir}' is not a directory.")

        file_map: dict[str, str] = {}
        file_contents: dict[str, bytes] = {}
        for file_path in dir_path.rglob("*"):
            if not file_path.is_file():
                continue
            rel = str(file_path.relative_to(dir_path))
            web_path = "/" + rel.replace(os.sep, "/")
            with open(file_path, "rb") as fh:
                content = fh.read()
            sha1 = hashlib.sha1(content).hexdigest()
            file_map[web_path] = sha1
            file_contents[web_path] = content

        deploy_body: dict[str, Any] = {"files": file_map}
        if message:
            deploy_body["title"] = message
        deploy_resp = requests.post(
            f"{self._BASE}/sites/{site_id}/deploys",
            headers=self._headers(cfg),
            json=deploy_body,
            timeout=60,
        )
        if deploy_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Netlify deploy_site (create) failed HTTP {deploy_resp.status_code}: {deploy_resp.text[:200]}",
            )
        deploy_data = deploy_resp.json()
        deploy_id = deploy_data.get("id", "")
        required_sha1s: list[str] = deploy_data.get("required", [])

        upload_errors: list[str] = []
        for sha1 in required_sha1s:
            matching = [p for p, s in file_map.items() if s == sha1]
            for web_path in matching:
                up_resp = requests.put(
                    f"{self._BASE}/deploys/{deploy_id}/files{web_path}",
                    headers={
                        "Authorization": f"Bearer {cfg['api_token']}",
                        "Content-Type": "application/octet-stream",
                    },
                    data=file_contents[web_path],
                    timeout=60,
                )
                if up_resp.status_code >= 400:
                    upload_errors.append(web_path)

        deploy_url = deploy_data.get("deploy_ssl_url") or deploy_data.get("deploy_url", "")
        msg = f"Netlify deploy {deploy_id}: {len(file_map)} files, {len(required_sha1s)} uploaded."
        if upload_errors:
            msg += f" Upload errors on: {upload_errors[:5]}"
        return ConnectorResult(
            ok=len(upload_errors) == 0,
            message=msg,
            url=deploy_url,
            data={
                "deploy_id": deploy_id,
                "url": deploy_url,
                "file_count": len(file_map),
                "upload_errors": upload_errors,
            },
        )

    def list_sites(self, filter_name: str = "") -> ConnectorResult:
        """List all Netlify sites accessible with the configured token.

        Args:
            filter_name: optional substring to filter site names.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/sites",
            headers=self._headers(cfg),
            params={"per_page": 100},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Netlify list_sites failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        sites = resp.json()
        if filter_name:
            sites = [s for s in sites if filter_name.lower() in s.get("name", "").lower()]
        summaries = [
            {"id": s.get("id"), "name": s.get("name"), "url": s.get("ssl_url") or s.get("url")}
            for s in sites
        ]
        return ConnectorResult(
            ok=True,
            message=f"{len(summaries)} Netlify sites.",
            data={"sites": summaries},
        )

    def create_site(self, name: str, custom_domain: str | None = None) -> ConnectorResult:
        """Create a new Netlify site.

        Args:
            name: unique subdomain name for the site (e.g. ``'my-new-site'``).
            custom_domain: optional custom domain to associate immediately.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        body: dict[str, Any] = {"name": name}
        if custom_domain:
            body["custom_domain"] = custom_domain
        resp = requests.post(
            f"{self._BASE}/sites",
            headers=self._headers(cfg),
            json=body,
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Netlify create_site failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        site_id = data.get("id", "")
        site_url = data.get("ssl_url") or data.get("url", "")
        return ConnectorResult(
            ok=True,
            message=f"Netlify site '{name}' created (id={site_id}).",
            url=site_url,
            data={"site_id": site_id, "name": name, "url": site_url},
        )

    def delete_site(self, site_id: str | None = None) -> ConnectorResult:
        """Permanently delete a Netlify site.

        Args:
            site_id: ID of the site to delete; falls back to config ``site_id``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        site_id = site_id or cfg.get("site_id", "")
        if not site_id:
            return ConnectorResult(ok=False, message="Netlify delete_site: site_id not specified.")
        resp = requests.delete(
            f"{self._BASE}/sites/{site_id}",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code not in (200, 204):
            return ConnectorResult(
                ok=False,
                message=f"Netlify delete_site failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Netlify site {site_id} deleted.",
            data={"site_id": site_id},
        )

    def get_site_info(self, site_id: str | None = None) -> ConnectorResult:
        """Fetch details about a Netlify site.

        Args:
            site_id: ID of the site; falls back to config ``site_id``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        site_id = site_id or cfg.get("site_id", "")
        if not site_id:
            return ConnectorResult(ok=False, message="Netlify get_site_info: site_id not specified.")
        resp = requests.get(
            f"{self._BASE}/sites/{site_id}",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Netlify get_site_info failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        site_url = data.get("ssl_url") or data.get("url", "")
        return ConnectorResult(
            ok=True,
            message=f"Netlify site '{data.get('name')}' at {site_url}.",
            url=site_url,
            data=data,
        )

    def set_env_vars(self, env: dict[str, str], site_id: str | None = None) -> ConnectorResult:
        """Set one or more build environment variables for a Netlify site.

        Args:
            env: mapping of variable names to values (e.g. ``{'API_KEY': 'abc'}``)
            site_id: ID of the site; falls back to config ``site_id``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        site_id = site_id or cfg.get("site_id", "")
        if not site_id:
            return ConnectorResult(ok=False, message="Netlify set_env_vars: site_id not specified.")
        resp = requests.patch(
            f"{self._BASE}/sites/{site_id}",
            headers=self._headers(cfg),
            json={"build_settings": {"env": env}},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Netlify set_env_vars failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Netlify site {site_id}: {len(env)} env var(s) set.",
            data={"site_id": site_id, "keys": list(env.keys())},
        )


# ---------------------------------------------------------------------------
# VercelConnector
# ---------------------------------------------------------------------------

@_hosting_register
class VercelConnector(Connector):
    """Vercel deployment connector.

    Implements ``deploy``, ``list_deployments``, ``get_deployment``,
    ``cancel_deployment``, and ``set_env_var``.

    Set ``token`` (and optionally ``team_id``) in ``[vercel]`` config.
    """

    name = "hosting_vercel"
    label = "Vercel"
    config_section = "vercel"
    required_keys = ("token",)
    capabilities = ("hosting", "deploy", "serverless")

    _BASE = "https://api.vercel.com"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {cfg['token']}",
            "Content-Type": "application/json",
        }

    def _team_param(self, cfg: dict) -> dict[str, str]:
        team_id = cfg.get("team_id", "")
        return {"teamId": team_id} if team_id else {}

    def deploy(
        self,
        project_name: str,
        files: dict[str, str] | None = None,
        local_dir: str | None = None,
        framework: str | None = None,
        target: str = "production",
    ) -> ConnectorResult:
        """Create a Vercel deployment.

        Either supply ``files`` (a dict mapping file paths to UTF-8 content)
        or ``local_dir`` (a directory on disk).  One of the two is required.

        Args:
            project_name: Vercel project name (must already exist in the team/account).
            files: dict mapping relative file paths to their string content.
            local_dir: path to a local directory to deploy (loaded into ``files``).
            framework: optional framework hint (e.g. ``'nextjs'``, ``'vite'``, ``None``).
            target: deployment target — ``'production'`` (default) or ``'preview'``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        if local_dir is not None:
            files = {}
            dir_path = Path(local_dir)
            for fp in dir_path.rglob("*"):
                if fp.is_file():
                    rel = str(fp.relative_to(dir_path)).replace(os.sep, "/")
                    try:
                        files[rel] = fp.read_text(encoding="utf-8")
                    except Exception:
                        files[rel] = base64.b64encode(fp.read_bytes()).decode()
        if not files:
            return ConnectorResult(ok=False, message="Vercel deploy: 'files' or 'local_dir' must be provided.")

        file_list = [{"file": path, "data": content} for path, content in files.items()]
        payload: dict[str, Any] = {
            "name": project_name,
            "files": file_list,
            "target": target,
            "projectSettings": {"framework": framework},
        }
        resp = requests.post(
            f"{self._BASE}/v13/deployments",
            headers=self._headers(cfg),
            params=self._team_param(cfg),
            json=payload,
            timeout=60,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Vercel deploy failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        deploy_id = data.get("id", "")
        deploy_url = f"https://{data.get('url', '')}" if data.get("url") else ""
        return ConnectorResult(
            ok=True,
            message=f"Vercel deployment {deploy_id} created for '{project_name}' ({target}).",
            url=deploy_url,
            data={"deploy_id": deploy_id, "url": deploy_url, "state": data.get("readyState")},
        )

    def list_deployments(
        self,
        project_name: str | None = None,
        limit: int = 10,
    ) -> ConnectorResult:
        """List recent Vercel deployments.

        Args:
            project_name: filter by project name; if omitted returns all projects' deploys.
            limit: maximum number of deployments to return (default 10).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        params: dict[str, Any] = {**self._team_param(cfg), "limit": limit}
        if project_name:
            params["projectId"] = project_name
        resp = requests.get(
            f"{self._BASE}/v6/deployments",
            headers=self._headers(cfg),
            params=params,
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Vercel list_deployments failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        deploys = resp.json().get("deployments", [])
        return ConnectorResult(
            ok=True,
            message=f"{len(deploys)} Vercel deployments.",
            data={"deployments": deploys},
        )

    def get_deployment(self, deploy_id: str) -> ConnectorResult:
        """Fetch details for a specific Vercel deployment.

        Args:
            deploy_id: Vercel deployment ID (e.g. ``'dpl_...'``).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/v13/deployments/{deploy_id}",
            headers=self._headers(cfg),
            params=self._team_param(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Vercel get_deployment failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        deploy_url = f"https://{data.get('url', '')}" if data.get("url") else ""
        return ConnectorResult(
            ok=True,
            message=f"Vercel deployment {deploy_id}: state={data.get('readyState')}.",
            url=deploy_url,
            data=data,
        )

    def cancel_deployment(self, deploy_id: str) -> ConnectorResult:
        """Cancel a queued or building Vercel deployment.

        Args:
            deploy_id: Vercel deployment ID to cancel.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.patch(
            f"{self._BASE}/v12/deployments/{deploy_id}/cancel",
            headers=self._headers(cfg),
            params=self._team_param(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Vercel cancel_deployment failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Vercel deployment {deploy_id} cancelled (state={data.get('status')}).",
            data=data,
        )

    def set_env_var(
        self,
        project: str,
        key: str,
        value: str,
        target: list[str] | None = None,
        env_type: str = "plain",
    ) -> ConnectorResult:
        """Create or update an environment variable for a Vercel project.

        Args:
            project: Vercel project name or ID.
            key: environment variable name.
            value: environment variable value.
            target: list of target environments; defaults to ``['production']``.
            env_type: variable type — ``'plain'``, ``'secret'``, or ``'encrypted'``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        target = target or ["production"]
        resp = requests.post(
            f"{self._BASE}/v10/projects/{project}/env",
            headers=self._headers(cfg),
            params=self._team_param(cfg),
            json=[{"key": key, "value": value, "type": env_type, "target": target}],
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Vercel set_env_var failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Vercel project '{project}': env var '{key}' set for {target}.",
            data={"project": project, "key": key, "target": target},
        )


# ---------------------------------------------------------------------------
# CloudflarePagesConnector
# ---------------------------------------------------------------------------

@_hosting_register
class CloudflarePagesConnector(Connector):
    """Cloudflare Pages connector.

    Implements ``create_project``, ``list_projects``, ``get_project``,
    ``list_deployments``, and also ``trigger_deployment`` and ``purge_cache``.

    Config keys
    -----------
    api_token    : Cloudflare API token with Pages and Zone edit permissions.
    account_id   : Cloudflare account ID.
    project_name : Optional default Pages project name.
    zone_id      : Optional zone ID, required for ``purge_cache``.
    """

    name = "hosting_cloudflare"
    label = "Cloudflare Pages"
    config_section = "cloudflare"
    required_keys = ("api_token", "account_id")
    capabilities = ("hosting", "deploy", "static_site")

    _BASE = "https://api.cloudflare.com/client/v4"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {cfg['api_token']}",
            "Content-Type": "application/json",
        }

    def _account(self, cfg: dict) -> str:
        return cfg["account_id"]

    def create_project(
        self,
        name: str,
        production_branch: str = "main",
    ) -> ConnectorResult:
        """Create a new Cloudflare Pages project.

        Args:
            name: unique project name (becomes the subdomain on ``pages.dev``).
            production_branch: Git branch to use for production deployments.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            f"{self._BASE}/accounts/{self._account(cfg)}/pages/projects",
            headers=self._headers(cfg),
            json={"name": name, "production_branch": production_branch},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Cloudflare Pages create_project failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json().get("result", {})
        subdomain = data.get("subdomain", "")
        site_url = f"https://{subdomain}.pages.dev" if subdomain else ""
        return ConnectorResult(
            ok=True,
            message=f"Cloudflare Pages project '{name}' created (subdomain={subdomain}).",
            url=site_url,
            data=data,
        )

    def list_projects(self) -> ConnectorResult:
        """List all Cloudflare Pages projects in the account."""
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.get(
            f"{self._BASE}/accounts/{self._account(cfg)}/pages/projects",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Cloudflare Pages list_projects failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        projects = resp.json().get("result", [])
        summaries = [
            {
                "name": p.get("name"),
                "subdomain": p.get("subdomain"),
                "production_branch": p.get("production_branch"),
            }
            for p in projects
        ]
        return ConnectorResult(
            ok=True,
            message=f"{len(summaries)} Cloudflare Pages projects.",
            data={"projects": summaries},
        )

    def get_project(self, project_name: str | None = None) -> ConnectorResult:
        """Fetch details for a Cloudflare Pages project.

        Args:
            project_name: project name; falls back to config ``project_name``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        project_name = project_name or cfg.get("project_name", "")
        if not project_name:
            return ConnectorResult(ok=False, message="Cloudflare Pages get_project: project_name not specified.")
        resp = requests.get(
            f"{self._BASE}/accounts/{self._account(cfg)}/pages/projects/{project_name}",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Cloudflare Pages get_project failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json().get("result", {})
        subdomain = data.get("subdomain", "")
        return ConnectorResult(
            ok=True,
            message=f"Cloudflare Pages project '{project_name}': subdomain={subdomain}.",
            data=data,
        )

    def list_deployments(
        self,
        project_name: str | None = None,
    ) -> ConnectorResult:
        """List deployments for a Cloudflare Pages project.

        Args:
            project_name: project name; falls back to config ``project_name``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        project_name = project_name or cfg.get("project_name", "")
        if not project_name:
            return ConnectorResult(ok=False, message="Cloudflare Pages list_deployments: project_name not specified.")
        resp = requests.get(
            f"{self._BASE}/accounts/{self._account(cfg)}/pages/projects/{project_name}/deployments",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Cloudflare Pages list_deployments failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        deploys = resp.json().get("result", [])
        return ConnectorResult(
            ok=True,
            message=f"{len(deploys)} deployments for Cloudflare Pages project '{project_name}'.",
            data={"deployments": deploys, "project": project_name},
        )

    def trigger_deployment(self, project_name: str | None = None) -> ConnectorResult:
        """Trigger a new deployment from the connected Git repository.

        Args:
            project_name: project name; falls back to config ``project_name``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        project_name = project_name or cfg.get("project_name", "")
        if not project_name:
            return ConnectorResult(
                ok=False,
                message="Cloudflare Pages trigger_deployment: project_name not specified.",
            )
        resp = requests.post(
            f"{self._BASE}/accounts/{self._account(cfg)}/pages/projects/{project_name}/deployments",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Cloudflare Pages trigger_deployment failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json().get("result", {})
        deploy_id = data.get("id", "")
        url = data.get("url", "")
        return ConnectorResult(
            ok=True,
            message=f"Cloudflare Pages deployment {deploy_id} triggered for '{project_name}'.",
            url=url,
            data=data,
        )

    def purge_cache(self, zone_id: str | None = None) -> ConnectorResult:
        """Purge all cached assets for the Cloudflare zone.

        Args:
            zone_id: Cloudflare zone ID; falls back to config ``zone_id``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        zone_id = zone_id or cfg.get("zone_id", "")
        if not zone_id:
            return ConnectorResult(
                ok=False,
                message="Cloudflare Pages purge_cache: zone_id not specified in config or argument.",
            )
        resp = requests.post(
            f"{self._BASE}/zones/{zone_id}/purge_cache",
            headers=self._headers(cfg),
            json={"purge_everything": True},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Cloudflare purge_cache failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Cloudflare cache purged for zone {zone_id}.",
            data=resp.json().get("result", {}),
        )


# ---------------------------------------------------------------------------
# GithubPagesConnector
# ---------------------------------------------------------------------------

@_hosting_register
class GithubPagesConnector(Connector):
    """GitHub Pages connector via the GitHub REST API.

    Implements ``enable_pages``, ``get_pages_url``, ``list_sites`` (the same
    as ``get_pages_url`` for a list of repos), ``deploy_file``,
    ``create_site``, ``delete_site``, and ``set_env_vars`` (via repository
    Actions secrets/variables).

    Config keys
    -----------
    token : GitHub personal access token (needs ``repo`` scope for private
            repos; ``public_repo`` for public repos).
    repo  : Default repository in ``owner/repo`` format
            (e.g. ``'acme/acme.github.io'``).
    """

    name = "hosting_github_pages"
    label = "GitHub Pages"
    config_section = "github"
    required_keys = ("token",)
    capabilities = ("hosting", "deploy", "static_site")

    _BASE = "https://api.github.com"

    def _headers(self, cfg: dict) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {cfg['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }

    def _repo(self, cfg: dict, repo: str | None) -> str | None:
        return repo or cfg.get("repo", "")

    def enable_pages(
        self,
        repo: str | None = None,
        source_branch: str = "gh-pages",
        source_path: str = "/",
    ) -> ConnectorResult:
        """Enable GitHub Pages for a repository.

        Args:
            repo: repository in ``owner/repo`` format; falls back to config ``repo``.
            source_branch: branch to serve from (default ``'gh-pages'``).
            source_path: folder path within the branch — ``'/'`` or ``'/docs'``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        repo = self._repo(cfg, repo)
        if not repo:
            return ConnectorResult(ok=False, message="GitHub Pages enable_pages: repo not specified.")
        resp = requests.post(
            f"{self._BASE}/repos/{repo}/pages",
            headers=self._headers(cfg),
            json={"source": {"branch": source_branch, "path": source_path}},
            timeout=30,
        )
        if resp.status_code == 409:
            return ConnectorResult(
                ok=True,
                message=f"GitHub Pages already enabled for {repo}.",
                data={"repo": repo, "branch": source_branch},
            )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"GitHub Pages enable_pages failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        url = data.get("html_url", "")
        return ConnectorResult(
            ok=True,
            message=f"GitHub Pages enabled for {repo} (branch='{source_branch}').",
            url=url,
            data=data,
        )

    def get_pages_url(self, repo: str | None = None) -> ConnectorResult:
        """Return the live GitHub Pages URL for a repository.

        Args:
            repo: repository in ``owner/repo`` format; falls back to config ``repo``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        repo = self._repo(cfg, repo)
        if not repo:
            return ConnectorResult(ok=False, message="GitHub Pages get_pages_url: repo not specified.")
        resp = requests.get(
            f"{self._BASE}/repos/{repo}/pages",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"GitHub Pages get_pages_url failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        url = data.get("html_url", "")
        return ConnectorResult(
            ok=True,
            message=f"GitHub Pages URL for {repo}: {url}",
            url=url,
            data=data,
        )

    def deploy_file(
        self,
        path: str,
        content: str | bytes,
        message: str = "",
        branch: str = "gh-pages",
        repo: str | None = None,
    ) -> ConnectorResult:
        """Create or update a single file in the repository (used to deploy to Pages).

        Args:
            path: file path within the repo (e.g. ``'index.html'`` or ``'assets/style.css'``).
            content: UTF-8 string or bytes content of the file.
            message: commit message; defaults to ``'Deploy <path>'``.
            branch: target branch (default ``'gh-pages'``).
            repo: repository in ``owner/repo`` format; falls back to config ``repo``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        repo = self._repo(cfg, repo)
        if not repo:
            return ConnectorResult(ok=False, message="GitHub Pages deploy_file: repo not specified.")
        commit_msg = message or f"Deploy {path}"
        if isinstance(content, str):
            content = content.encode()
        encoded = base64.b64encode(content).decode()

        # Check whether the file already exists (need its SHA to update it)
        existing = requests.get(
            f"{self._BASE}/repos/{repo}/contents/{path}",
            headers=self._headers(cfg),
            params={"ref": branch},
            timeout=30,
        )
        payload: dict[str, Any] = {
            "message": commit_msg,
            "content": encoded,
            "branch": branch,
        }
        if existing.status_code == 200:
            payload["sha"] = existing.json().get("sha", "")

        resp = requests.put(
            f"{self._BASE}/repos/{repo}/contents/{path}",
            headers=self._headers(cfg),
            json=payload,
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"GitHub Pages deploy_file failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        file_url = data.get("content", {}).get("html_url", "")
        return ConnectorResult(
            ok=True,
            message=f"GitHub Pages: deployed '{path}' to {repo} (branch='{branch}').",
            url=file_url,
            data=data,
        )

    def create_site(
        self,
        repo: str,
        description: str = "",
        auto_init: bool = True,
    ) -> ConnectorResult:
        """Create a new repository and enable GitHub Pages on it.

        Args:
            repo: desired repository name (the ``owner`` is inferred from the token).
            description: optional repository description.
            auto_init: if True, initialise with an empty README (required for Pages).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        # Create the repository
        create_resp = requests.post(
            f"{self._BASE}/user/repos",
            headers=self._headers(cfg),
            json={"name": repo, "description": description, "auto_init": auto_init},
            timeout=30,
        )
        if create_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"GitHub Pages create_site (repo) failed HTTP {create_resp.status_code}: {create_resp.text[:200]}",
            )
        repo_data = create_resp.json()
        full_name = repo_data.get("full_name", repo)

        # Enable Pages on the new repo (main branch)
        pages_result = self.enable_pages(repo=full_name, source_branch="main")
        pages_url = pages_result.url
        return ConnectorResult(
            ok=True,
            message=f"GitHub repo '{full_name}' created and Pages enabled at {pages_url}.",
            url=pages_url,
            data={"repo": full_name, "pages_url": pages_url, "repo_data": repo_data},
        )

    def delete_site(
        self,
        repo: str | None = None,
        disable_only: bool = True,
    ) -> ConnectorResult:
        """Disable GitHub Pages for a repository (or delete the repo entirely).

        Args:
            repo: repository in ``owner/repo`` format; falls back to config ``repo``.
            disable_only: if True (default) only disable Pages; if False, delete the repo.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        repo = self._repo(cfg, repo)
        if not repo:
            return ConnectorResult(ok=False, message="GitHub Pages delete_site: repo not specified.")
        if disable_only:
            resp = requests.delete(
                f"{self._BASE}/repos/{repo}/pages",
                headers=self._headers(cfg),
                timeout=30,
            )
            if resp.status_code not in (200, 204):
                return ConnectorResult(
                    ok=False,
                    message=f"GitHub Pages delete_site (disable) failed HTTP {resp.status_code}: {resp.text[:200]}",
                )
            return ConnectorResult(
                ok=True,
                message=f"GitHub Pages disabled for {repo}.",
                data={"repo": repo},
            )
        # Delete the entire repository
        resp = requests.delete(
            f"{self._BASE}/repos/{repo}",
            headers=self._headers(cfg),
            timeout=30,
        )
        if resp.status_code not in (200, 204):
            return ConnectorResult(
                ok=False,
                message=f"GitHub Pages delete_site (repo) failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"GitHub repository {repo} deleted.",
            data={"repo": repo},
        )

    def set_env_vars(
        self,
        env: dict[str, str],
        repo: str | None = None,
        environment_name: str = "github-pages",
    ) -> ConnectorResult:
        """Store environment variables as GitHub Actions repository variables.

        These are accessible via ``${{ vars.KEY }}`` in Actions workflows that
        deploy to Pages.

        Args:
            env: mapping of variable names to values.
            repo: repository in ``owner/repo`` format; falls back to config ``repo``.
            environment_name: GitHub Actions environment name (default ``'github-pages'``).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        repo = self._repo(cfg, repo)
        if not repo:
            return ConnectorResult(ok=False, message="GitHub Pages set_env_vars: repo not specified.")
        errors: list[str] = []
        for key, value in env.items():
            resp = requests.post(
                f"{self._BASE}/repos/{repo}/actions/variables",
                headers=self._headers(cfg),
                json={"name": key, "value": value},
                timeout=30,
            )
            if resp.status_code == 409:
                # Variable exists — update it
                resp = requests.patch(
                    f"{self._BASE}/repos/{repo}/actions/variables/{key}",
                    headers=self._headers(cfg),
                    json={"name": key, "value": value},
                    timeout=30,
                )
            if resp.status_code >= 400:
                errors.append(key)
        if errors:
            return ConnectorResult(
                ok=False,
                message=f"GitHub Pages set_env_vars: failed to set {errors}.",
                data={"repo": repo, "failed": errors},
            )
        return ConnectorResult(
            ok=True,
            message=f"GitHub Pages: {len(env)} variable(s) set on {repo}.",
            data={"repo": repo, "keys": list(env.keys())},
        )
