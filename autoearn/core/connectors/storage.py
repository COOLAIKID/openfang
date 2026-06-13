"""Cloud storage connectors.

Connectors for uploading, downloading, and managing files in remote object
stores and CDNs: Amazon S3, Dropbox, Backblaze B2, and BunnyCDN. Each
connector wraps the platform's HTTP API directly so there are no heavy SDK
dependencies beyond ``requests``.

Config sections expected in config.toml
---------------------------------------
[s3]
access_key_id     = "AKIA..."
secret_access_key = "..."
region            = "us-east-1"         # optional, defaults to us-east-1
bucket            = "my-default-bucket"  # optional default bucket

[dropbox]
access_token = "sl...."

[backblaze]
application_key_id = "..."
application_key    = "..."
bucket_id          = "..."     # optional default bucket id
bucket_name        = "..."     # used for friendly download URLs

[bunny]
api_key         = "..."
storage_zone    = "my-zone"
pull_zone_url   = "https://my-zone.b-cdn.net"  # optional
storage_region  = "storage"                    # optional region prefix
pull_zone_id    = "123456"                     # optional, for purge_cache
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import requests

from .base import Connector, ConnectorResult, register

# ---------------------------------------------------------------------------
# Module-level registry helpers
# ---------------------------------------------------------------------------
_STORAGE_NAMES: list[str] = []


def configured() -> list[Connector]:
    """Return instantiated connectors for every storage backend that has valid credentials."""
    from .base import _REGISTRY  # noqa: PLC0415

    result: list[Connector] = []
    for name in _STORAGE_NAMES:
        cls = _REGISTRY.get(name)
        if cls:
            inst = cls()
            if inst.is_configured():
                result.append(inst)
    return result


def get(name: str) -> Connector | None:
    """Return an instantiated storage connector by registry name, or None."""
    from .base import _REGISTRY  # noqa: PLC0415

    cls = _REGISTRY.get(name)
    return cls() if cls else None


def _storage_register(cls: type[Connector]) -> type[Connector]:
    """Wrap base.register and track storage connector names."""
    register(cls)
    _STORAGE_NAMES.append(cls.name)
    return cls


# ---------------------------------------------------------------------------
# Internal signing / hashing utilities
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


# ---------------------------------------------------------------------------
# S3Connector
# ---------------------------------------------------------------------------

@_storage_register
class S3Connector(Connector):
    """Amazon S3 connector using AWS Signature Version 4 over the S3 REST API.

    No ``boto3`` dependency — all requests are signed manually so the connector
    works in any lightweight Python environment.

    Implements ``upload_file``, ``download_file``, ``list_files``,
    ``delete_file``, ``get_presigned_url``, ``create_bucket``, and
    ``bucket_size``.
    """

    name = "storage_s3"
    label = "Amazon S3"
    config_section = "s3"
    required_keys = ("access_key_id", "secret_access_key")
    capabilities = ("storage", "upload", "download")

    # ------------------------------------------------------------------
    # Low-level helpers

    def _region(self) -> str:
        return self.cfg().get("region", "us-east-1")

    def _host(self, bucket: str) -> str:
        region = self._region()
        if region == "us-east-1":
            return f"{bucket}.s3.amazonaws.com"
        return f"{bucket}.s3.{region}.amazonaws.com"

    def _signing_key(self, cfg: dict, datestamp: str) -> bytes:
        k_date = _hmac_sha256(("AWS4" + cfg["secret_access_key"]).encode(), datestamp)
        k_region = _hmac_sha256(k_date, self._region())
        k_service = _hmac_sha256(k_region, "s3")
        return _hmac_sha256(k_service, "aws4_request")

    def _sign_request(
        self,
        cfg: dict,
        method: str,
        bucket: str,
        key: str,
        payload: bytes = b"",
        extra_headers: dict[str, str] | None = None,
        query_params: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Return a dict of HTTP headers that include an AWS SigV4 Authorization."""
        now = datetime.now(tz=timezone.utc)
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        host = self._host(bucket)
        payload_hash = _sha256_hex(payload)

        canonical_uri = "/" + urllib.parse.quote(key, safe="/")
        canonical_qs = ""
        if query_params:
            canonical_qs = "&".join(
                f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
                for k, v in sorted(query_params.items())
            )

        headers: dict[str, str] = {
            "host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amzdate,
        }
        if extra_headers:
            for k, v in extra_headers.items():
                headers[k.lower()] = v

        signed_headers_str = ";".join(sorted(headers.keys()))
        canonical_headers = "".join(f"{k}:{v}\n" for k, v in sorted(headers.items()))

        canonical_request = "\n".join([
            method, canonical_uri, canonical_qs,
            canonical_headers, signed_headers_str, payload_hash,
        ])

        credential_scope = "/".join([datestamp, self._region(), "s3", "aws4_request"])
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amzdate, credential_scope, _sha256_hex(canonical_request),
        ])

        signing_key = self._signing_key(cfg, datestamp)
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth = (
            f"AWS4-HMAC-SHA256 Credential={cfg['access_key_id']}/{credential_scope}, "
            f"SignedHeaders={signed_headers_str}, Signature={signature}"
        )
        result: dict[str, str] = {
            "Authorization": auth,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amzdate,
            "Host": host,
        }
        if extra_headers:
            for k, v in extra_headers.items():
                result[k] = v
        return result

    def _url(self, bucket: str, key: str = "") -> str:
        host = self._host(bucket)
        path = "/" + urllib.parse.quote(key, safe="/") if key else "/"
        return f"https://{host}{path}"

    # ------------------------------------------------------------------
    # Public API

    def upload_file(
        self,
        local_path: str,
        bucket: str | None = None,
        s3_key: str | None = None,
        content_type: str = "application/octet-stream",
    ) -> ConnectorResult:
        """Upload a local file to S3.

        Args:
            local_path: absolute path to the local file.
            bucket: destination S3 bucket; falls back to config ``bucket`` key.
            s3_key: destination key in the bucket; defaults to the local filename.
            content_type: MIME type to set on the object.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        bucket = bucket or cfg.get("bucket", "")
        if not bucket:
            return ConnectorResult(ok=False, message="S3 upload_file: bucket not specified.")
        s3_key = s3_key or os.path.basename(local_path)
        try:
            with open(local_path, "rb") as fh:
                payload = fh.read()
        except OSError as exc:
            return ConnectorResult(ok=False, message=f"S3 upload_file: cannot read '{local_path}': {exc}")

        headers = self._sign_request(
            cfg, "PUT", bucket, s3_key, payload=payload,
            extra_headers={"Content-Type": content_type},
        )
        url = self._url(bucket, s3_key)
        resp = requests.put(url, data=payload, headers=headers, timeout=120)
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"S3 upload_file failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Uploaded '{local_path}' to s3://{bucket}/{s3_key}.",
            url=url,
            data={"bucket": bucket, "key": s3_key, "url": url},
        )

    def download_file(
        self,
        s3_key: str,
        local_path: str,
        bucket: str | None = None,
    ) -> ConnectorResult:
        """Download an S3 object to a local path.

        Args:
            s3_key: key of the object in the bucket.
            local_path: destination path on disk.
            bucket: source bucket; falls back to config ``bucket`` key.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        bucket = bucket or cfg.get("bucket", "")
        if not bucket:
            return ConnectorResult(ok=False, message="S3 download_file: bucket not specified.")
        headers = self._sign_request(cfg, "GET", bucket, s3_key)
        url = self._url(bucket, s3_key)
        resp = requests.get(url, headers=headers, timeout=120, stream=True)
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"S3 download_file failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        try:
            with open(local_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
        except OSError as exc:
            return ConnectorResult(ok=False, message=f"S3 download_file: cannot write '{local_path}': {exc}")
        size = os.path.getsize(local_path)
        return ConnectorResult(
            ok=True,
            message=f"Downloaded s3://{bucket}/{s3_key} -> '{local_path}' ({size} bytes).",
            data={"local_path": local_path, "size": size, "key": s3_key},
        )

    def list_files(
        self,
        bucket: str | None = None,
        prefix: str = "",
        max_keys: int = 1000,
    ) -> ConnectorResult:
        """List objects in an S3 bucket, optionally filtered by prefix."""
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        bucket = bucket or cfg.get("bucket", "")
        if not bucket:
            return ConnectorResult(ok=False, message="S3 list_files: bucket not specified.")
        qp: dict[str, str] = {"max-keys": str(max_keys)}
        if prefix:
            qp["prefix"] = prefix
        headers = self._sign_request(cfg, "GET", bucket, "", query_params=qp)
        url = self._url(bucket)
        resp = requests.get(url, headers=headers, params=qp, timeout=30)
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"S3 list_files failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        root = ET.fromstring(resp.text)
        contents = root.findall("s3:Contents", ns)
        files = [
            {
                "key": el.findtext("s3:Key", namespaces=ns) or "",
                "size": int(el.findtext("s3:Size", default="0", namespaces=ns) or 0),
                "last_modified": el.findtext("s3:LastModified", namespaces=ns) or "",
            }
            for el in contents
        ]
        return ConnectorResult(
            ok=True,
            message=f"{len(files)} objects in s3://{bucket} (prefix='{prefix}').",
            data={"files": files, "bucket": bucket},
        )

    def delete_file(self, s3_key: str, bucket: str | None = None) -> ConnectorResult:
        """Delete a single S3 object.

        Args:
            s3_key: key of the object to remove.
            bucket: bucket name; falls back to config ``bucket`` key.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        bucket = bucket or cfg.get("bucket", "")
        if not bucket:
            return ConnectorResult(ok=False, message="S3 delete_file: bucket not specified.")
        headers = self._sign_request(cfg, "DELETE", bucket, s3_key)
        url = self._url(bucket, s3_key)
        resp = requests.delete(url, headers=headers, timeout=30)
        if resp.status_code not in (200, 204):
            return ConnectorResult(
                ok=False,
                message=f"S3 delete_file failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Deleted s3://{bucket}/{s3_key}.",
            data={"bucket": bucket, "key": s3_key},
        )

    def get_presigned_url(
        self,
        s3_key: str,
        bucket: str | None = None,
        expires_in: int = 3600,
    ) -> ConnectorResult:
        """Generate a presigned GET URL valid for ``expires_in`` seconds.

        Args:
            s3_key: key of the object to sign.
            bucket: bucket name; falls back to config ``bucket`` key.
            expires_in: URL lifetime in seconds (default 3600 = 1 hour).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        bucket = bucket or cfg.get("bucket", "")
        if not bucket:
            return ConnectorResult(ok=False, message="S3 get_presigned_url: bucket not specified.")
        now = datetime.now(tz=timezone.utc)
        amzdate = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        host = self._host(bucket)
        credential_scope = "/".join([datestamp, self._region(), "s3", "aws4_request"])
        credential = f"{cfg['access_key_id']}/{credential_scope}"
        canonical_uri = "/" + urllib.parse.quote(s3_key, safe="/")

        qp: dict[str, str] = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": credential,
            "X-Amz-Date": amzdate,
            "X-Amz-Expires": str(expires_in),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_qs = "&".join(
            f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}"
            for k, v in sorted(qp.items())
        )
        canonical_request = "\n".join([
            "GET", canonical_uri, canonical_qs,
            f"host:{host}\n", "host", "UNSIGNED-PAYLOAD",
        ])
        string_to_sign = "\n".join([
            "AWS4-HMAC-SHA256", amzdate, credential_scope, _sha256_hex(canonical_request),
        ])
        signing_key = self._signing_key(cfg, datestamp)
        signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        qp["X-Amz-Signature"] = signature
        qs = urllib.parse.urlencode(qp)
        presigned = f"https://{host}{canonical_uri}?{qs}"
        return ConnectorResult(
            ok=True,
            message=f"Presigned URL for s3://{bucket}/{s3_key} (expires {expires_in}s).",
            url=presigned,
            data={"url": presigned, "expires_in": expires_in},
        )

    def create_bucket(self, bucket: str, region: str | None = None) -> ConnectorResult:
        """Create a new S3 bucket.

        Args:
            bucket: name of the bucket to create.
            region: AWS region; defaults to the connector's configured region.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        region = region or self._region()
        payload = b""
        if region != "us-east-1":
            payload = (
                '<CreateBucketConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                f"<LocationConstraint>{region}</LocationConstraint>"
                "</CreateBucketConfiguration>"
            ).encode()
        extra = {"Content-Type": "application/xml"} if payload else {}
        headers = self._sign_request(cfg, "PUT", bucket, "", payload=payload, extra_headers=extra or None)
        url = self._url(bucket)
        resp = requests.put(url, data=payload, headers=headers, timeout=30)
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"S3 create_bucket failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Bucket s3://{bucket} created in {region}.",
            data={"bucket": bucket, "region": region},
        )

    def bucket_size(self, bucket: str | None = None) -> ConnectorResult:
        """Return the total byte-size and object count of a bucket.

        Note: this lists up to 1000 objects; for accurate totals on large
        buckets use CloudWatch Storage Metrics instead.

        Args:
            bucket: bucket name; falls back to config ``bucket`` key.
        """
        result = self.list_files(bucket=bucket, max_keys=1000)
        if not result.ok:
            return result
        files = result.data.get("files", [])
        total_bytes = sum(f.get("size", 0) for f in files)
        count = len(files)
        return ConnectorResult(
            ok=True,
            message=f"Bucket s3://{result.data.get('bucket')}: {count} objects, ~{total_bytes:,} bytes total.",
            data={"object_count": count, "total_bytes": total_bytes, "bucket": result.data.get("bucket")},
        )


# ---------------------------------------------------------------------------
# DropboxConnector
# ---------------------------------------------------------------------------

@_storage_register
class DropboxConnector(Connector):
    """Dropbox API v2 connector.

    Implements ``upload``, ``download``, ``list_folder``, ``delete``,
    ``share_link``, and ``search``.

    Requires a long-lived access token (or an offline refresh token managed
    externally). Set ``access_token`` in the ``[dropbox]`` config section.
    """

    name = "storage_dropbox"
    label = "Dropbox"
    config_section = "dropbox"
    required_keys = ("access_token",)
    capabilities = ("storage", "upload", "download")

    _API = "https://api.dropboxapi.com/2"
    _CONTENT = "https://content.dropboxapi.com/2"

    def _headers(self, cfg: dict, content_type: str = "application/json") -> dict[str, str]:
        return {
            "Authorization": f"Bearer {cfg['access_token']}",
            "Content-Type": content_type,
        }

    def upload(
        self,
        local_path: str,
        dropbox_path: str | None = None,
        mode: str = "overwrite",
    ) -> ConnectorResult:
        """Upload a local file to Dropbox.

        Args:
            local_path: path to the local file.
            dropbox_path: absolute Dropbox path (e.g. '/folder/file.pdf').
                          If omitted the file is placed at the root.
            mode: upload mode — ``'overwrite'`` (default), ``'add'``, or ``'update'``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        if dropbox_path is None:
            dropbox_path = "/" + os.path.basename(local_path)
        if not dropbox_path.startswith("/"):
            dropbox_path = "/" + dropbox_path
        try:
            with open(local_path, "rb") as fh:
                payload = fh.read()
        except OSError as exc:
            return ConnectorResult(ok=False, message=f"Dropbox upload: cannot read '{local_path}': {exc}")

        arg = json.dumps({"path": dropbox_path, "mode": mode, "autorename": False, "mute": False})
        resp = requests.post(
            self._CONTENT + "/files/upload",
            headers={
                "Authorization": f"Bearer {cfg['access_token']}",
                "Content-Type": "application/octet-stream",
                "Dropbox-API-Arg": arg,
            },
            data=payload,
            timeout=120,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Dropbox upload failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        meta = resp.json()
        return ConnectorResult(
            ok=True,
            message=f"Uploaded to Dropbox: {meta.get('path_display', dropbox_path)}.",
            data=meta,
        )

    def download(self, dropbox_path: str, local_path: str) -> ConnectorResult:
        """Download a Dropbox file to a local path.

        Args:
            dropbox_path: absolute Dropbox path of the file to download.
            local_path: destination path on disk.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        if not dropbox_path.startswith("/"):
            dropbox_path = "/" + dropbox_path
        arg = json.dumps({"path": dropbox_path})
        resp = requests.post(
            self._CONTENT + "/files/download",
            headers={
                "Authorization": f"Bearer {cfg['access_token']}",
                "Dropbox-API-Arg": arg,
            },
            timeout=120,
            stream=True,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Dropbox download failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        try:
            with open(local_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
        except OSError as exc:
            return ConnectorResult(ok=False, message=f"Dropbox download: cannot write '{local_path}': {exc}")
        size = os.path.getsize(local_path)
        return ConnectorResult(
            ok=True,
            message=f"Downloaded {dropbox_path} -> '{local_path}' ({size} bytes).",
            data={"local_path": local_path, "size": size},
        )

    def list_folder(self, path: str = "", recursive: bool = False) -> ConnectorResult:
        """List the contents of a Dropbox folder.

        Args:
            path: absolute Dropbox folder path; empty string means the root.
            recursive: if True, list all subdirectories recursively.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            self._API + "/files/list_folder",
            headers=self._headers(cfg),
            json={"path": path, "recursive": recursive, "include_media_info": False},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Dropbox list_folder failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        data = resp.json()
        entries = data.get("entries", [])
        return ConnectorResult(
            ok=True,
            message=f"{len(entries)} entries in Dropbox folder '{path or '/'}'.",
            data={"entries": entries, "has_more": data.get("has_more", False), "cursor": data.get("cursor", "")},
        )

    def delete(self, dropbox_path: str) -> ConnectorResult:
        """Delete a file or folder at the given Dropbox path.

        Args:
            dropbox_path: absolute Dropbox path of the file or folder to delete.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        if not dropbox_path.startswith("/"):
            dropbox_path = "/" + dropbox_path
        resp = requests.post(
            self._API + "/files/delete_v2",
            headers=self._headers(cfg),
            json={"path": dropbox_path},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Dropbox delete failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        meta = resp.json().get("metadata", {})
        return ConnectorResult(
            ok=True,
            message=f"Deleted Dropbox path: {meta.get('path_display', dropbox_path)}.",
            data=meta,
        )

    def share_link(self, dropbox_path: str, visibility: str = "public") -> ConnectorResult:
        """Create or retrieve a shared link for a Dropbox file.

        Args:
            dropbox_path: absolute Dropbox path of the file to share.
            visibility: ``'public'`` (default) or ``'team_only'``.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        if not dropbox_path.startswith("/"):
            dropbox_path = "/" + dropbox_path
        resp = requests.post(
            self._API + "/sharing/create_shared_link_with_settings",
            headers=self._headers(cfg),
            json={"path": dropbox_path, "settings": {"requested_visibility": visibility}},
            timeout=30,
        )
        if resp.status_code == 409:
            # Link already exists — retrieve it
            resp2 = requests.post(
                self._API + "/sharing/list_shared_links",
                headers=self._headers(cfg),
                json={"path": dropbox_path, "direct_only": True},
                timeout=30,
            )
            if resp2.status_code >= 400:
                return ConnectorResult(
                    ok=False,
                    message=f"Dropbox share_link (existing) failed HTTP {resp2.status_code}: {resp2.text[:200]}",
                )
            links = resp2.json().get("links", [])
            link_url = links[0].get("url", "") if links else ""
        elif resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Dropbox share_link failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        else:
            link_url = resp.json().get("url", "")
        # Derive direct-download URL
        direct = (
            link_url
            .replace("www.dropbox.com", "dl.dropboxusercontent.com")
            .split("?")[0]
        )
        return ConnectorResult(
            ok=True,
            message=f"Shared link for {dropbox_path}: {link_url}",
            url=link_url,
            data={"url": link_url, "direct_url": direct},
        )

    def search(self, query: str, path: str = "", max_results: int = 20) -> ConnectorResult:
        """Search Dropbox for files matching a query string.

        Args:
            query: search query (filename or content keywords).
            path: optional folder path to restrict the search scope.
            max_results: maximum number of results to return (default 20, max 1000).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        resp = requests.post(
            self._API + "/files/search_v2",
            headers=self._headers(cfg),
            json={
                "query": query,
                "options": {
                    "path": path,
                    "max_results": max_results,
                    "file_status": "active",
                },
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Dropbox search failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        matches = resp.json().get("matches", [])
        files = [m.get("metadata", {}).get("metadata", {}) for m in matches]
        return ConnectorResult(
            ok=True,
            message=f"{len(files)} Dropbox results for '{query}'.",
            data={"results": files, "query": query},
        )


# ---------------------------------------------------------------------------
# BackblazeConnector
# ---------------------------------------------------------------------------

@_storage_register
class BackblazeConnector(Connector):
    """Backblaze B2 connector using the B2 native HTTP API.

    Implements ``upload``, ``download``, ``list_bucket``, ``delete``, and
    ``get_download_url``.

    The ``b2_authorize_account`` handshake is performed on the first request
    and the result is cached for the lifetime of the Python process / connector
    instance to avoid redundant auth calls.
    """

    name = "storage_backblaze"
    label = "Backblaze B2"
    config_section = "backblaze"
    required_keys = ("application_key_id", "application_key")
    capabilities = ("storage", "upload", "download")

    _B2_AUTH_URL = "https://api.backblazeb2.com/b2api/v2/b2_authorize_account"

    # Per-instance auth cache
    _auth_cache: dict[str, Any] | None = None

    def _authorize(self, cfg: dict) -> dict[str, Any] | None:
        """Authorize and return the B2 auth response (cached)."""
        if self._auth_cache:
            return self._auth_cache
        creds = base64.b64encode(
            f"{cfg['application_key_id']}:{cfg['application_key']}".encode()
        ).decode()
        resp = requests.get(
            self._B2_AUTH_URL,
            headers={"Authorization": f"Basic {creds}"},
            timeout=30,
        )
        if resp.status_code >= 400:
            return None
        self._auth_cache = resp.json()
        return self._auth_cache

    def _default_bucket_id(self, cfg: dict) -> str:
        return cfg.get("bucket_id", "")

    def upload(
        self,
        local_path: str,
        file_name: str | None = None,
        bucket_id: str | None = None,
        content_type: str = "b2/x-auto",
    ) -> ConnectorResult:
        """Upload a local file to a Backblaze B2 bucket.

        Args:
            local_path: absolute path to the local file.
            file_name: destination file name in the bucket; defaults to the local filename.
            bucket_id: B2 bucket ID; falls back to config ``bucket_id`` key.
            content_type: MIME type ('b2/x-auto' lets B2 detect it automatically).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        auth = self._authorize(cfg)
        if not auth:
            return ConnectorResult(ok=False, message="Backblaze B2: authorization failed.")
        bid = bucket_id or self._default_bucket_id(cfg)
        if not bid:
            return ConnectorResult(ok=False, message="Backblaze B2 upload: bucket_id not specified.")
        file_name = file_name or os.path.basename(local_path)
        try:
            with open(local_path, "rb") as fh:
                payload = fh.read()
        except OSError as exc:
            return ConnectorResult(ok=False, message=f"Backblaze B2 upload: cannot read file: {exc}")

        # Get an upload URL for this bucket
        up_url_resp = requests.post(
            auth["apiUrl"] + "/b2api/v2/b2_get_upload_url",
            headers={"Authorization": auth["authorizationToken"]},
            json={"bucketId": bid},
            timeout=30,
        )
        if up_url_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Backblaze B2 get_upload_url failed HTTP {up_url_resp.status_code}: {up_url_resp.text[:200]}",
            )
        up_info = up_url_resp.json()
        sha1 = hashlib.sha1(payload).hexdigest()

        up_resp = requests.post(
            up_info["uploadUrl"],
            headers={
                "Authorization": up_info["authorizationToken"],
                "X-Bz-File-Name": urllib.parse.quote(file_name),
                "Content-Type": content_type,
                "Content-Length": str(len(payload)),
                "X-Bz-Content-Sha1": sha1,
            },
            data=payload,
            timeout=120,
        )
        if up_resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Backblaze B2 upload failed HTTP {up_resp.status_code}: {up_resp.text[:200]}",
            )
        meta = up_resp.json()
        download_url = auth.get("downloadUrl", "")
        file_url = (
            f"{download_url}/b2api/v2/b2_download_file_by_id?fileId={meta.get('fileId', '')}"
        )
        return ConnectorResult(
            ok=True,
            message=f"Uploaded '{file_name}' to Backblaze B2 bucket {bid}.",
            url=file_url,
            data=meta,
        )

    def download(self, file_id: str, local_path: str) -> ConnectorResult:
        """Download a B2 file by ``fileId`` to a local path.

        Args:
            file_id: B2 fileId of the object to download.
            local_path: destination path on disk.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        auth = self._authorize(cfg)
        if not auth:
            return ConnectorResult(ok=False, message="Backblaze B2: authorization failed.")
        download_url = auth.get("downloadUrl", "")
        resp = requests.get(
            f"{download_url}/b2api/v2/b2_download_file_by_id",
            params={"fileId": file_id},
            headers={"Authorization": auth["authorizationToken"]},
            timeout=120,
            stream=True,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Backblaze B2 download failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        try:
            with open(local_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
        except OSError as exc:
            return ConnectorResult(ok=False, message=f"Backblaze B2 download: cannot write file: {exc}")
        size = os.path.getsize(local_path)
        return ConnectorResult(
            ok=True,
            message=f"Downloaded B2 file {file_id} -> '{local_path}' ({size} bytes).",
            data={"local_path": local_path, "size": size, "file_id": file_id},
        )

    def list_bucket(
        self,
        bucket_id: str | None = None,
        prefix: str = "",
        max_file_count: int = 1000,
    ) -> ConnectorResult:
        """List files in a B2 bucket.

        Args:
            bucket_id: B2 bucket ID; falls back to config ``bucket_id`` key.
            prefix: optional filename prefix filter.
            max_file_count: maximum number of files to return (max 10000).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        auth = self._authorize(cfg)
        if not auth:
            return ConnectorResult(ok=False, message="Backblaze B2: authorization failed.")
        bid = bucket_id or self._default_bucket_id(cfg)
        if not bid:
            return ConnectorResult(ok=False, message="Backblaze B2 list_bucket: bucket_id not specified.")
        resp = requests.post(
            auth["apiUrl"] + "/b2api/v2/b2_list_file_names",
            headers={"Authorization": auth["authorizationToken"]},
            json={"bucketId": bid, "maxFileCount": max_file_count, "prefix": prefix},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Backblaze B2 list_bucket failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        files = resp.json().get("files", [])
        return ConnectorResult(
            ok=True,
            message=f"{len(files)} files in B2 bucket {bid}.",
            data={"files": files, "bucket_id": bid},
        )

    def delete(self, file_name: str, file_id: str) -> ConnectorResult:
        """Delete a specific version of a B2 file.

        Both ``file_name`` and ``file_id`` are required by the B2 API.

        Args:
            file_name: the name of the file (as stored in B2).
            file_id: the B2 fileId of the specific version to delete.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        auth = self._authorize(cfg)
        if not auth:
            return ConnectorResult(ok=False, message="Backblaze B2: authorization failed.")
        resp = requests.post(
            auth["apiUrl"] + "/b2api/v2/b2_delete_file_version",
            headers={"Authorization": auth["authorizationToken"]},
            json={"fileName": file_name, "fileId": file_id},
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"Backblaze B2 delete failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Deleted B2 file '{file_name}' (fileId={file_id}).",
            data=resp.json(),
        )

    def get_download_url(
        self,
        file_name: str,
        bucket_name: str | None = None,
        authorized: bool = False,
    ) -> ConnectorResult:
        """Return a friendly download URL for a B2 file.

        Works for public buckets. For private buckets set ``authorized=True``
        to generate a URL that works with an authorization token appended.

        Args:
            file_name: name of the file in the bucket.
            bucket_name: friendly bucket name; falls back to config ``bucket_name``.
            authorized: if True, append the authorization token as a query parameter.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        auth = self._authorize(cfg)
        if not auth:
            return ConnectorResult(ok=False, message="Backblaze B2: authorization failed.")
        bucket_name = bucket_name or cfg.get("bucket_name", "")
        if not bucket_name:
            return ConnectorResult(
                ok=False,
                message="Backblaze B2 get_download_url: bucket_name not specified.",
            )
        download_url = auth.get("downloadUrl", "").rstrip("/")
        url = f"{download_url}/file/{urllib.parse.quote(bucket_name)}/{urllib.parse.quote(file_name)}"
        if authorized:
            url += f"?Authorization={auth['authorizationToken']}"
        return ConnectorResult(
            ok=True,
            message=f"Download URL: {url}",
            url=url,
            data={"url": url, "file_name": file_name, "bucket_name": bucket_name},
        )


# ---------------------------------------------------------------------------
# BunnyConnector
# ---------------------------------------------------------------------------

@_storage_register
class BunnyConnector(Connector):
    """BunnyCDN Storage connector.

    Implements ``upload``, ``list_files``, ``delete``, ``get_cdn_url``, and
    ``purge_cache``.

    Config keys
    -----------
    api_key         : Storage API key (BunnyCDN dashboard -> Storage Zone -> FTP & API Access).
    storage_zone    : Name of your storage zone (e.g. ``"my-zone"``).
    pull_zone_url   : Optional CDN pull zone base URL (e.g. ``"https://my-zone.b-cdn.net"``).
    storage_region  : Optional storage region hostname prefix; defaults to ``"storage"``.
                      Examples: ``"ny.storage"``, ``"la.storage"``, ``"sg.storage"``.
    pull_zone_id    : Optional pull zone numeric ID, required for ``purge_cache`` on a zone.
    """

    name = "storage_bunny"
    label = "BunnyCDN"
    config_section = "bunny"
    required_keys = ("api_key", "storage_zone")
    capabilities = ("storage", "upload", "cdn")

    def _storage_base(self) -> str:
        region = self.cfg().get("storage_region", "storage")
        return f"https://{region}.bunnycdn.com"

    def _zone(self) -> str:
        return self.cfg().get("storage_zone", "")

    def _auth_headers(self, cfg: dict) -> dict[str, str]:
        return {"AccessKey": cfg["api_key"]}

    def _cdn_url_for(self, cfg: dict, remote_path: str) -> str:
        pull_zone = cfg.get("pull_zone_url", "").rstrip("/")
        if pull_zone:
            return f"{pull_zone}/{remote_path.lstrip('/')}"
        zone = self._zone()
        return f"https://{zone}.b-cdn.net/{remote_path.lstrip('/')}"

    def upload(
        self,
        local_path: str,
        remote_path: str | None = None,
    ) -> ConnectorResult:
        """Upload a local file to a BunnyCDN storage zone.

        Args:
            local_path: absolute path to the local file.
            remote_path: destination path within the storage zone, e.g.
                         ``'images/photo.jpg'``. Defaults to the local filename.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        zone = self._zone()
        remote_path = (remote_path or os.path.basename(local_path)).lstrip("/")
        try:
            with open(local_path, "rb") as fh:
                payload = fh.read()
        except OSError as exc:
            return ConnectorResult(ok=False, message=f"BunnyCDN upload: cannot read '{local_path}': {exc}")
        url = f"{self._storage_base()}/{zone}/{remote_path}"
        resp = requests.put(
            url,
            headers={**self._auth_headers(cfg), "Content-Type": "application/octet-stream"},
            data=payload,
            timeout=120,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"BunnyCDN upload failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        cdn_url = self._cdn_url_for(cfg, remote_path)
        return ConnectorResult(
            ok=True,
            message=f"Uploaded '{local_path}' to BunnyCDN /{zone}/{remote_path}.",
            url=cdn_url,
            data={"zone": zone, "path": remote_path, "cdn_url": cdn_url},
        )

    def list_files(self, directory: str = "") -> ConnectorResult:
        """List files in a BunnyCDN storage zone directory.

        Args:
            directory: sub-path within the zone to list; empty means the zone root.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        zone = self._zone()
        path = directory.lstrip("/")
        url = f"{self._storage_base()}/{zone}/{path}/" if path else f"{self._storage_base()}/{zone}/"
        resp = requests.get(url, headers=self._auth_headers(cfg), timeout=30)
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"BunnyCDN list_files failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        files: Any = resp.json() if resp.text.strip() else []
        return ConnectorResult(
            ok=True,
            message=f"{len(files)} items in BunnyCDN /{zone}/{path}.",
            data={"files": files, "zone": zone, "directory": path},
        )

    def delete(self, remote_path: str) -> ConnectorResult:
        """Delete a file from a BunnyCDN storage zone.

        Args:
            remote_path: path of the file within the zone (no leading slash).
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        zone = self._zone()
        remote_path = remote_path.lstrip("/")
        url = f"{self._storage_base()}/{zone}/{remote_path}"
        resp = requests.delete(url, headers=self._auth_headers(cfg), timeout=30)
        if resp.status_code not in (200, 204):
            return ConnectorResult(
                ok=False,
                message=f"BunnyCDN delete failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Deleted BunnyCDN /{zone}/{remote_path}.",
            data={"zone": zone, "path": remote_path},
        )

    def get_cdn_url(self, remote_path: str) -> ConnectorResult:
        """Return the public CDN URL for a file in the storage zone.

        Args:
            remote_path: path of the file within the zone.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        url = self._cdn_url_for(cfg, remote_path)
        return ConnectorResult(
            ok=True,
            message=f"CDN URL: {url}",
            url=url,
            data={"cdn_url": url, "path": remote_path.lstrip("/")},
        )

    def purge_cache(
        self,
        url: str | None = None,
        pull_zone_id: str | None = None,
    ) -> ConnectorResult:
        """Purge the BunnyCDN edge cache for a specific URL or an entire pull zone.

        Provide exactly one of ``url`` or ``pull_zone_id``.  Falls back to
        ``pull_zone_id`` from the config section when neither is given.

        Args:
            url: the full CDN URL whose cache entry should be invalidated.
            pull_zone_id: numeric pull zone ID; purges the entire zone if given.
        """
        cfg = self.require()
        if not cfg:
            return self.not_configured()
        headers = {"AccessKey": cfg["api_key"], "Content-Type": "application/json"}
        if url:
            resp = requests.post(
                "https://api.bunny.net/purge",
                headers=headers,
                params={"url": url, "async": "false"},
                timeout=30,
            )
            if resp.status_code >= 400:
                return ConnectorResult(
                    ok=False,
                    message=f"BunnyCDN purge_cache failed HTTP {resp.status_code}: {resp.text[:200]}",
                )
            return ConnectorResult(
                ok=True,
                message=f"Purged BunnyCDN cache for URL: {url}",
                url=url,
                data={"url": url},
            )
        pid = pull_zone_id or cfg.get("pull_zone_id", "")
        if not pid:
            return ConnectorResult(
                ok=False,
                message="BunnyCDN purge_cache: provide 'url' or 'pull_zone_id'.",
            )
        resp = requests.post(
            f"https://api.bunny.net/pullzone/{pid}/purgeCache",
            headers=headers,
            timeout=30,
        )
        if resp.status_code >= 400:
            return ConnectorResult(
                ok=False,
                message=f"BunnyCDN purge pull zone failed HTTP {resp.status_code}: {resp.text[:200]}",
            )
        return ConnectorResult(
            ok=True,
            message=f"Purged entire BunnyCDN pull zone {pid}.",
            data={"pull_zone_id": pid},
        )
