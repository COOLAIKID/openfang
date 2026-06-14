from __future__ import annotations

import ast
import os
import json
import re
import math
import statistics
import subprocess
import datetime
from pathlib import Path
from typing import Any

import requests


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_file(path: str) -> str:
    """Read a file and return its content as a string."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _iter_files(path_or_dir: str, extensions: list[str]) -> list[str]:
    """Yield file paths matching the given extensions under path_or_dir."""
    p = Path(path_or_dir)
    if p.is_file():
        return [str(p)]
    results: list[str] = []
    for root, _dirs, files in os.walk(str(p)):
        for fname in files:
            if any(fname.endswith(ext) for ext in extensions):
                results.append(os.path.join(root, fname))
    return results


# ---------------------------------------------------------------------------
# analyze_python_file
# ---------------------------------------------------------------------------

def analyze_python_file(path: str) -> str:
    """AST analysis of a Python source file.

    Args:
        path: Absolute or relative path to a Python file.

    Returns:
        JSON string with keys: functions, classes, imports, complexity_score, lines.
    """
    source = _read_file(path)
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return json.dumps({"error": str(exc)})

    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    imports: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            decorator_names = []
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name):
                    decorator_names.append(dec.id)
                elif isinstance(dec, ast.Attribute):
                    decorator_names.append(dec.attr)
            functions.append({
                "name": node.name,
                "lineno": node.lineno,
                "args": args,
                "decorators": decorator_names,
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            })
        elif isinstance(node, ast.ClassDef):
            bases = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(base.attr)
            methods = [
                n.name for n in ast.walk(node)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            classes.append({
                "name": node.name,
                "lineno": node.lineno,
                "bases": bases,
                "methods": methods,
            })
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")

    # Cyclomatic complexity approximation: count branches
    branch_nodes = (
        ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler,
        ast.With, ast.Assert, ast.comprehension,
    )
    branch_count = sum(1 for n in ast.walk(tree) if isinstance(n, branch_nodes))
    complexity_score = 1 + branch_count

    lines = source.splitlines()

    result = {
        "path": str(path),
        "lines": len(lines),
        "functions": functions,
        "classes": classes,
        "imports": sorted(set(imports)),
        "complexity_score": complexity_score,
        "analyzed_at": datetime.datetime.utcnow().isoformat(),
    }
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# find_todos
# ---------------------------------------------------------------------------

_TODO_PATTERN = re.compile(
    r"(?:#|//|/\*|\*|<!--)\s*(TODO|FIXME|HACK|XXX)\b[:\s]*(.*)",
    re.IGNORECASE,
)

def find_todos(path_or_dir: str) -> str:
    """Scan source files for TODO/FIXME/HACK/XXX comments.

    Args:
        path_or_dir: File or directory to scan.

    Returns:
        JSON list of {file, lineno, tag, text}.
    """
    extensions = [".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".go", ".rs", ".html"]
    results: list[dict[str, Any]] = []

    for filepath in _iter_files(path_or_dir, extensions):
        try:
            lines = _read_file(filepath).splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            m = _TODO_PATTERN.search(line)
            if m:
                results.append({
                    "file": filepath,
                    "lineno": lineno,
                    "tag": m.group(1).upper(),
                    "text": m.group(2).strip(),
                })

    return json.dumps(results, indent=2)


# ---------------------------------------------------------------------------
# count_lines_of_code
# ---------------------------------------------------------------------------

def count_lines_of_code(path_or_dir: str, extensions: list[str] | None = None) -> str:
    """Count lines of code, blank lines, and comment lines.

    Args:
        path_or_dir: File or directory to analyze.
        extensions: File extensions to include. Defaults to ['.py'].

    Returns:
        JSON with per-file and totals breakdown.
    """
    if extensions is None:
        extensions = [".py"]

    totals = {"code": 0, "blank": 0, "comment": 0, "total": 0}
    per_file: list[dict[str, Any]] = []

    for filepath in _iter_files(path_or_dir, extensions):
        try:
            raw = _read_file(filepath)
        except OSError:
            continue
        lines = raw.splitlines()
        code_count = 0
        blank_count = 0
        comment_count = 0

        in_multiline = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                blank_count += 1
                continue
            # Python multi-line strings used as comments
            if stripped.startswith('"""') or stripped.startswith("'''"):
                comment_count += 1
                # Toggle multiline if not closed on same line
                delimiter = stripped[:3]
                occurrences = stripped.count(delimiter)
                if occurrences < 2:
                    in_multiline = not in_multiline
                continue
            if in_multiline:
                comment_count += 1
                if '"""' in stripped or "'''" in stripped:
                    in_multiline = False
                continue
            if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("*"):
                comment_count += 1
            else:
                code_count += 1

        file_total = code_count + blank_count + comment_count
        per_file.append({
            "file": filepath,
            "code": code_count,
            "blank": blank_count,
            "comment": comment_count,
            "total": file_total,
        })
        totals["code"] += code_count
        totals["blank"] += blank_count
        totals["comment"] += comment_count
        totals["total"] += file_total

    return json.dumps({"totals": totals, "files": per_file}, indent=2)


# ---------------------------------------------------------------------------
# check_syntax
# ---------------------------------------------------------------------------

def check_syntax(code: str, language: str = "python") -> str:
    """Check syntax of code.

    Args:
        code: Source code string.
        language: 'python' or 'javascript'/'js'.

    Returns:
        JSON with {valid: bool, error: str or null}.
    """
    lang = language.lower()
    if lang == "python":
        try:
            ast.parse(code)
            return json.dumps({"valid": True, "error": None})
        except SyntaxError as exc:
            return json.dumps({"valid": False, "error": f"SyntaxError at line {exc.lineno}: {exc.msg}"})
    elif lang in ("javascript", "js", "typescript", "ts"):
        # Write to a temp file and run node --check
        import tempfile
        suffix = ".js" if lang in ("javascript", "js") else ".ts"
        with tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False) as tmp:
            tmp.write(code)
            tmp_path = tmp.name
        try:
            result = subprocess.run(
                ["node", "--check", tmp_path],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return json.dumps({"valid": True, "error": None})
            else:
                err = result.stderr.strip() or result.stdout.strip()
                return json.dumps({"valid": False, "error": err})
        except FileNotFoundError:
            return json.dumps({"valid": False, "error": "node not found in PATH"})
        except subprocess.TimeoutExpired:
            return json.dumps({"valid": False, "error": "node --check timed out"})
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    else:
        return json.dumps({"valid": False, "error": f"Unsupported language: {language}"})


# ---------------------------------------------------------------------------
# generate_docstring
# ---------------------------------------------------------------------------

def generate_docstring(function_code: str) -> str:
    """Generate a Google-style docstring from a function's signature and body.

    Args:
        function_code: Complete Python function source as a string.

    Returns:
        A docstring string (including triple quotes).
    """
    try:
        tree = ast.parse(function_code)
    except SyntaxError:
        return '"""TODO: add docstring."""'

    func_nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if not func_nodes:
        return '"""TODO: add docstring."""'

    func = func_nodes[0]
    func_name = func.name
    args = func.args

    # Collect positional args
    arg_names = [a.arg for a in args.args if a.arg != "self"]
    defaults_offset = len(args.args) - len(args.defaults)

    arg_lines: list[str] = []
    for i, arg in enumerate(args.args):
        if arg.arg == "self":
            continue
        annotation = ""
        if arg.annotation:
            annotation = ast.unparse(arg.annotation) if hasattr(ast, "unparse") else ""
        default_val = ""
        default_idx = i - defaults_offset
        if default_idx >= 0 and default_idx < len(args.defaults):
            default_node = args.defaults[default_idx]
            default_val = f" Defaults to {ast.unparse(default_node)}." if hasattr(ast, "unparse") else ""
        type_hint = f" ({annotation})" if annotation else ""
        arg_lines.append(f"        {arg.arg}{type_hint}: Description.{default_val}")

    # Return annotation
    return_annotation = ""
    if func.returns:
        return_annotation = ast.unparse(func.returns) if hasattr(ast, "unparse") else "Any"

    # Build docstring
    summary = f"{'Async f' if isinstance(func, ast.AsyncFunctionDef) else 'F'}unction {func_name}."
    lines = ['"""' + summary, ""]
    if arg_lines:
        lines.append("    Args:")
        lines.extend(arg_lines)
        lines.append("")
    if return_annotation:
        lines.append("    Returns:")
        lines.append(f"        {return_annotation}: Description.")
        lines.append("")
    lines.append('    """')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# extract_api_endpoints
# ---------------------------------------------------------------------------

_ROUTE_PATTERNS = [
    # FastAPI / Flask style: @app.get("/path") or @router.post("/path")
    re.compile(
        r'@\w+\.(get|post|put|patch|delete|head|options|trace|route)\s*\(\s*["\']([^"\']+)["\']',
        re.IGNORECASE,
    ),
    # Django path/re_path: path("url", view_func)
    re.compile(
        r'(?:path|re_path|url)\s*\(\s*["\']([^"\']*)["\'].*?,\s*(\w+)',
        re.IGNORECASE,
    ),
]

def extract_api_endpoints(path_or_dir: str) -> str:
    """Find API route decorators in Python files.

    Args:
        path_or_dir: File or directory to scan.

    Returns:
        JSON list of {method, path, handler, file, lineno}.
    """
    endpoints: list[dict[str, Any]] = []
    for filepath in _iter_files(path_or_dir, [".py"]):
        try:
            lines = _read_file(filepath).splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            # FastAPI/Flask pattern
            m = _ROUTE_PATTERNS[0].search(line)
            if m:
                method = m.group(1).upper()
                route_path = m.group(2)
                # Try to find handler name on next line
                handler = ""
                if lineno < len(lines):
                    next_line = lines[lineno].strip()
                    hm = re.match(r"(?:async\s+)?def\s+(\w+)", next_line)
                    if hm:
                        handler = hm.group(1)
                endpoints.append({
                    "method": method,
                    "path": route_path,
                    "handler": handler,
                    "file": filepath,
                    "lineno": lineno,
                })
                continue
            # Django pattern
            m2 = _ROUTE_PATTERNS[1].search(line)
            if m2:
                endpoints.append({
                    "method": "ANY",
                    "path": m2.group(1),
                    "handler": m2.group(2),
                    "file": filepath,
                    "lineno": lineno,
                })

    return json.dumps(endpoints, indent=2)


# ---------------------------------------------------------------------------
# dependency_graph
# ---------------------------------------------------------------------------

def dependency_graph(entry_file: str) -> str:
    """Trace Python import graph from an entry point.

    Args:
        entry_file: Path to the root Python file.

    Returns:
        JSON with {nodes: [...], edges: [{from, to}, ...]}.
    """
    visited: set[str] = set()
    nodes: list[str] = []
    edges: list[dict[str, str]] = []

    base_dir = str(Path(entry_file).parent.resolve())

    def _resolve_module(module_name: str, current_dir: str) -> str | None:
        """Try to find a local .py file matching the module name."""
        parts = module_name.split(".")
        local_path = os.path.join(current_dir, *parts) + ".py"
        if os.path.isfile(local_path):
            return os.path.realpath(local_path)
        init_path = os.path.join(current_dir, *parts, "__init__.py")
        if os.path.isfile(init_path):
            return os.path.realpath(init_path)
        return None

    def _visit(filepath: str) -> None:
        real = os.path.realpath(filepath)
        if real in visited:
            return
        visited.add(real)
        nodes.append(real)
        try:
            source = _read_file(real)
            tree = ast.parse(source, filename=real)
        except (OSError, SyntaxError):
            return

        current_dir = str(Path(real).parent)
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    resolved = _resolve_module(module, current_dir)
                    if resolved:
                        edges.append({"from": real, "to": resolved})
                        _visit(resolved)
                    else:
                        edges.append({"from": real, "to": module})
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level and node.level > 0:
                    # Relative import
                    up = node.level - 1
                    rel_dir = current_dir
                    for _ in range(up):
                        rel_dir = str(Path(rel_dir).parent)
                    resolved = _resolve_module(module, rel_dir)
                else:
                    resolved = _resolve_module(module, current_dir)
                if resolved:
                    edges.append({"from": real, "to": resolved})
                    _visit(resolved)
                elif module:
                    edges.append({"from": real, "to": module})

    _visit(os.path.realpath(entry_file))

    return json.dumps({"nodes": nodes, "edges": edges}, indent=2)


# ---------------------------------------------------------------------------
# github_repo_stats
# ---------------------------------------------------------------------------

def github_repo_stats(owner: str, repo: str, token: str = "") -> str:
    """Fetch GitHub repository statistics.

    Args:
        owner: Repository owner username or org.
        repo: Repository name.
        token: Optional GitHub personal access token.

    Returns:
        JSON with stars, forks, open_issues, last_commit, license, description.
    """
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"https://api.github.com/repos/{owner}/{repo}"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return json.dumps({"error": str(exc)})

    # Last commit
    commits_url = f"https://api.github.com/repos/{owner}/{repo}/commits"
    last_commit = ""
    try:
        cr = requests.get(commits_url, headers=headers, timeout=10, params={"per_page": 1})
        cr.raise_for_status()
        commits = cr.json()
        if commits:
            last_commit = commits[0].get("commit", {}).get("committer", {}).get("date", "")
    except requests.RequestException:
        pass

    license_name = ""
    if data.get("license"):
        license_name = data["license"].get("name", "")

    return json.dumps({
        "owner": owner,
        "repo": repo,
        "stars": data.get("stargazers_count", 0),
        "forks": data.get("forks_count", 0),
        "open_issues": data.get("open_issues_count", 0),
        "watchers": data.get("watchers_count", 0),
        "description": data.get("description", ""),
        "language": data.get("language", ""),
        "license": license_name,
        "last_commit": last_commit,
        "homepage": data.get("homepage", ""),
        "created_at": data.get("created_at", ""),
        "updated_at": data.get("updated_at", ""),
    }, indent=2)


# ---------------------------------------------------------------------------
# github_search_repos
# ---------------------------------------------------------------------------

def github_search_repos(query: str, sort: str = "stars", limit: int = 10) -> str:
    """Search GitHub repositories.

    Args:
        query: Search query string.
        sort: Sort field: 'stars', 'forks', 'updated'. Defaults to 'stars'.
        limit: Maximum number of results. Defaults to 10.

    Returns:
        JSON list of repository summaries.
    """
    url = "https://api.github.com/search/repositories"
    params = {"q": query, "sort": sort, "order": "desc", "per_page": min(limit, 30)}
    headers = {"Accept": "application/vnd.github+json"}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return json.dumps({"error": str(exc)})

    items = []
    for item in data.get("items", [])[:limit]:
        items.append({
            "full_name": item.get("full_name", ""),
            "description": item.get("description", ""),
            "stars": item.get("stargazers_count", 0),
            "forks": item.get("forks_count", 0),
            "language": item.get("language", ""),
            "url": item.get("html_url", ""),
            "updated_at": item.get("updated_at", ""),
        })
    return json.dumps({"total_count": data.get("total_count", 0), "items": items}, indent=2)


# ---------------------------------------------------------------------------
# github_trending
# ---------------------------------------------------------------------------

def github_trending(language: str = "python", since: str = "daily") -> str:
    """Scrape GitHub trending repositories.

    Args:
        language: Programming language filter. Defaults to 'python'.
        since: Time window: 'daily', 'weekly', 'monthly'. Defaults to 'daily'.

    Returns:
        JSON list of trending repos with name, description, stars, url.
    """
    url = "https://github.com/trending"
    params: dict[str, str] = {}
    if language:
        params["l"] = language
    if since:
        params["since"] = since

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException as exc:
        return json.dumps({"error": str(exc)})

    # Parse HTML with regex (no external parser dependency)
    repos: list[dict[str, Any]] = []
    # Match repo articles
    repo_blocks = re.findall(
        r'<article[^>]*class="[^"]*Box-row[^"]*"[^>]*>(.*?)</article>',
        html, re.DOTALL
    )
    for block in repo_blocks[:25]:
        name_m = re.search(r'href="/([^/"]+/[^/"]+)"', block)
        desc_m = re.search(r'<p[^>]*>\s*(.*?)\s*</p>', block, re.DOTALL)
        stars_m = re.search(r'aria-label="(\d[\d,]*)\s+stars?', block)
        full_name = name_m.group(1) if name_m else ""
        description = re.sub(r"<[^>]+>", "", desc_m.group(1)).strip() if desc_m else ""
        stars_str = stars_m.group(1).replace(",", "") if stars_m else "0"
        try:
            stars = int(stars_str)
        except ValueError:
            stars = 0
        if full_name:
            repos.append({
                "full_name": full_name,
                "url": f"https://github.com/{full_name}",
                "description": description,
                "stars": stars,
                "language": language,
                "since": since,
            })

    return json.dumps({"repos": repos, "count": len(repos)}, indent=2)


# ---------------------------------------------------------------------------
# npm_package_info
# ---------------------------------------------------------------------------

def npm_package_info(package_name: str) -> str:
    """Fetch npm package information.

    Args:
        package_name: The npm package name (e.g. 'react').

    Returns:
        JSON with version, description, downloads, dependencies, homepage.
    """
    registry_url = f"https://registry.npmjs.org/{package_name}"
    downloads_url = f"https://api.npmjs.org/downloads/point/last-month/{package_name}"

    try:
        resp = requests.get(registry_url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return json.dumps({"error": str(exc)})

    latest_version = data.get("dist-tags", {}).get("latest", "")
    version_data = data.get("versions", {}).get(latest_version, {})

    monthly_downloads = 0
    try:
        dl_resp = requests.get(downloads_url, timeout=10)
        dl_resp.raise_for_status()
        monthly_downloads = dl_resp.json().get("downloads", 0)
    except requests.RequestException:
        pass

    return json.dumps({
        "name": package_name,
        "latest_version": latest_version,
        "description": data.get("description", ""),
        "homepage": data.get("homepage", ""),
        "license": data.get("license", ""),
        "monthly_downloads": monthly_downloads,
        "dependencies": list((version_data.get("dependencies") or {}).keys()),
        "dev_dependencies": list((version_data.get("devDependencies") or {}).keys()),
        "keywords": data.get("keywords", []),
        "author": (data.get("author") or {}).get("name", "") if isinstance(data.get("author"), dict) else str(data.get("author", "")),
    }, indent=2)


# ---------------------------------------------------------------------------
# pypi_package_info
# ---------------------------------------------------------------------------

def pypi_package_info(package_name: str) -> str:
    """Fetch PyPI package information.

    Args:
        package_name: The PyPI package name (e.g. 'requests').

    Returns:
        JSON with version, description, downloads, requires, classifiers.
    """
    url = f"https://pypi.org/pypi/{package_name}/json"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return json.dumps({"error": str(exc)})

    info = data.get("info", {})
    # Download stats (from pypistats alternative endpoint)
    downloads = {}
    try:
        stats_url = f"https://pypistats.org/api/packages/{package_name}/recent"
        sr = requests.get(stats_url, timeout=10)
        sr.raise_for_status()
        stats_data = sr.json()
        downloads = stats_data.get("data", {})
    except requests.RequestException:
        pass

    return json.dumps({
        "name": package_name,
        "version": info.get("version", ""),
        "summary": info.get("summary", ""),
        "author": info.get("author", ""),
        "license": info.get("license", ""),
        "home_page": info.get("home_page", ""),
        "requires_python": info.get("requires_python", ""),
        "requires_dist": info.get("requires_dist") or [],
        "classifiers": info.get("classifiers") or [],
        "downloads": downloads,
        "project_urls": info.get("project_urls") or {},
    }, indent=2)


# ---------------------------------------------------------------------------
# detect_secrets
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS Access Key",       re.compile(r'(?i)AKIA[0-9A-Z]{16}')),
    ("AWS Secret Key",       re.compile(r'(?i)aws.{0,20}secret.{0,20}["\'][0-9a-zA-Z/+]{40}["\']')),
    ("GitHub Token",         re.compile(r'(?i)gh[pousr]_[0-9a-zA-Z]{36,}')),
    ("Slack Token",          re.compile(r'xox[baprs]-[0-9a-zA-Z\-]+')),
    ("Generic API Key",      re.compile(r'(?i)(api[_\-]?key|apikey)\s*[=:]\s*["\'][0-9a-zA-Z_\-]{16,}["\']')),
    ("Generic Secret",       re.compile(r'(?i)(secret[_\-]?key|client_secret)\s*[=:]\s*["\'][0-9a-zA-Z_\-]{16,}["\']')),
    ("Generic Password",     re.compile(r'(?i)(password|passwd|pwd)\s*[=:]\s*["\'][^"\']{8,}["\']')),
    ("JWT Token",            re.compile(r'eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}')),
    ("Private Key Header",   re.compile(r'-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----')),
    ("Google API Key",       re.compile(r'AIza[0-9A-Za-z\-_]{35}')),
    ("Stripe Secret Key",    re.compile(r'sk_(live|test)_[0-9a-zA-Z]{24,}')),
    ("Stripe Publishable",   re.compile(r'pk_(live|test)_[0-9a-zA-Z]{24,}')),
    ("SendGrid API Key",     re.compile(r'SG\.[a-zA-Z0-9_\-]{22}\.[a-zA-Z0-9_\-]{43}')),
    ("Database URL",         re.compile(r'(?i)(postgres|mysql|mongodb|redis)://[^\s"\']+')),
]

def detect_secrets(path: str) -> str:
    """Scan files for hardcoded secrets and API keys.

    Args:
        path: File or directory to scan.

    Returns:
        JSON list of {file, lineno, type, match_preview}.
    """
    extensions = [".py", ".js", ".ts", ".env", ".yml", ".yaml", ".json", ".toml", ".cfg", ".ini", ".sh"]
    findings: list[dict[str, Any]] = []

    for filepath in _iter_files(path, extensions):
        # Skip obviously safe files
        if any(skip in filepath for skip in [".git", "node_modules", "__pycache__", ".pyc"]):
            continue
        try:
            lines = _read_file(filepath).splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            for secret_type, pattern in _SECRET_PATTERNS:
                m = pattern.search(line)
                if m:
                    match_text = m.group(0)
                    # Redact most of the secret for safety in output
                    preview = match_text[:8] + "***" if len(match_text) > 8 else "***"
                    findings.append({
                        "file": filepath,
                        "lineno": lineno,
                        "type": secret_type,
                        "match_preview": preview,
                    })

    return json.dumps({
        "findings": findings,
        "total": len(findings),
        "scanned_at": datetime.datetime.utcnow().isoformat(),
    }, indent=2)


# ---------------------------------------------------------------------------
# generate_readme_template
# ---------------------------------------------------------------------------

def generate_readme_template(
    project_name: str,
    description: str,
    usage: str,
    installation: str,
) -> str:
    """Generate a README.md template.

    Args:
        project_name: Name of the project.
        description: Short description of what the project does.
        usage: Example usage snippet.
        installation: Installation instructions.

    Returns:
        Markdown string for a README.md.
    """
    year = datetime.datetime.utcnow().year
    slug = re.sub(r"[^a-z0-9_-]", "-", project_name.lower()).strip("-")

    readme = f"""# {project_name}

> {description}

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue)](https://python.org)

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [Contributing](#contributing)
- [License](#license)

## Features

- TODO: list your key features here

## Installation

{installation}

## Usage

```python
{usage}
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TODO`   | `TODO`  | Add config variables here |

## Contributing

1. Fork the repository
2. Create your feature branch: `git checkout -b feature/amazing-feature`
3. Commit your changes: `git commit -m 'Add some amazing feature'`
4. Push to the branch: `git push origin feature/amazing-feature`
5. Open a Pull Request

## License

Copyright (c) {year} — Released under the [MIT License](LICENSE).

---

Made with Python. Project slug: `{slug}`.
"""
    return readme


# ---------------------------------------------------------------------------
# lint_python
# ---------------------------------------------------------------------------

def lint_python(code: str) -> str:
    """Basic Python linting: detect common issues without external tools.

    Args:
        code: Python source code string.

    Returns:
        JSON with {issues: [{line, type, message}], issue_count: int}.
    """
    issues: list[dict[str, Any]] = []

    # 1. Syntax check first
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return json.dumps({
            "issues": [{"line": exc.lineno, "type": "SyntaxError", "message": exc.msg}],
            "issue_count": 1,
        })

    lines = code.splitlines()

    # 2. Walk AST for issues
    imported_names: set[str] = set()
    used_names: set[str] = set()

    for node in ast.walk(tree):
        # Collect imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name.split(".")[0]
                imported_names.add(name)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                if name != "*":
                    imported_names.add(name)
        # Collect used names
        elif isinstance(node, ast.Name):
            used_names.add(node.id)

    # Unused imports (heuristic)
    for name in imported_names:
        if name not in used_names and name != "__all__":
            # Find the import line
            for lineno, line in enumerate(lines, start=1):
                if re.search(r'\bimport\b.*\b' + re.escape(name) + r'\b', line):
                    issues.append({
                        "line": lineno,
                        "type": "UnusedImport",
                        "message": f"'{name}' imported but unused",
                    })
                    break

    # 3. Line-level checks
    for lineno, line in enumerate(lines, start=1):
        stripped = line.rstrip()

        # Trailing whitespace
        if line != stripped and stripped:
            issues.append({
                "line": lineno,
                "type": "TrailingWhitespace",
                "message": "Trailing whitespace",
            })

        # Line too long (>120 chars)
        if len(line) > 120:
            issues.append({
                "line": lineno,
                "type": "LineTooLong",
                "message": f"Line length {len(line)} exceeds 120 characters",
            })

        # Bare except
        if re.search(r'\bexcept\s*:', line):
            issues.append({
                "line": lineno,
                "type": "BareExcept",
                "message": "Bare 'except:' clause; use 'except Exception:' or more specific",
            })

        # Comparison to None using == instead of is
        if re.search(r'==\s*None\b|\bNone\s*==', line):
            issues.append({
                "line": lineno,
                "type": "NoneComparison",
                "message": "Use 'is None' instead of '== None'",
            })

        # Comparison to True/False using ==
        if re.search(r'==\s*True\b|==\s*False\b|\bTrue\s*==|\bFalse\s*==', line):
            issues.append({
                "line": lineno,
                "type": "BoolComparison",
                "message": "Use 'if x:' or 'if not x:' instead of comparing to True/False",
            })

        # print() calls (warn in production code)
        if re.match(r'\s*print\s*\(', line) and not line.strip().startswith("#"):
            issues.append({
                "line": lineno,
                "type": "PrintStatement",
                "message": "print() found; consider using logging",
            })

        # Mutable default argument
        if re.search(r'def\s+\w+\s*\([^)]*=\s*(\[\]|\{\}|\(\))', line):
            issues.append({
                "line": lineno,
                "type": "MutableDefault",
                "message": "Mutable default argument ([], {}, or ()) — use None and set in body",
            })

    issues.sort(key=lambda x: x["line"])
    return json.dumps({"issues": issues, "issue_count": len(issues)}, indent=2)
