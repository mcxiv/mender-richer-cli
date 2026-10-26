"""HTTP client, pagination, artifact transfers, and device-management operations."""

from __future__ import annotations

import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import requests

from config import Config

INVENTORY_SEARCH = "/api/management/v2/inventory/filters/search"
INVENTORY_DEVICE = "/api/management/v1/inventory/devices"
DEVICES = "/api/management/v2/devauth/devices"
DEVICE_CONNECT = "/api/management/v1/deviceconnect/devices"
ARTIFACTS = "/api/management/v1/deployments/artifacts"
ARTIFACT_LIST = "/api/management/v1/deployments/artifacts/list"
DEPLOYMENTS = "/api/management/v1/deployments/deployments"
LOGIN = "/api/management/v1/useradm/auth/login"


def device_name(device: dict[str, Any]) -> str:
    """Choose a display name from device tags, inventory, or identity attributes.

    :param device: Device record containing an ID and scoped attributes.
    :returns: The first usable name, or a shortened device ID with a ``device-`` prefix.
    """
    attrs = device.get("attributes", [])
    for scope, name in (("tags", "name"), ("inventory", "hostname"),
                        ("identity", "name"), ("identity", "hostname")):
        for attr in attrs:
            if attr.get("scope") == scope and attr.get("name", attr.get("attribute")) == name:
                value = attr.get("value")
                if isinstance(value, str) and value:
                    return value
    for attr in attrs:
        value = attr.get("value")
        if attr.get("timestamp") and isinstance(value, str) and value:
            return value
    return "device-" + str(device.get("id", ""))[:12]


def device_updated(device: dict[str, Any]) -> str:
    """Find the last-contact timestamp in a device record.

    :param device: Device record containing scoped attributes and timestamps.
    :returns: The system or identity update time, latest attribute timestamp, or ``unknown``.
    """
    attrs = device.get("attributes", [])
    for scope in ("system", "identity"):
        for attr in attrs:
            if attr.get("scope") == scope and attr.get("name", attr.get("attribute")) == "updated_ts":
                return str(attr.get("value", "unknown"))
    return max((str(a.get("timestamp", "")) for a in attrs), default="") or "unknown"


def artifact_kind(artifact: dict[str, Any]) -> str:
    """Read the payload type from supported artifact metadata layouts.

    :param artifact: Artifact metadata returned by the deployments API.
    :returns: The payload type, or ``unknown`` when no type is available.
    """
    kind = (artifact.get("type_info") or {}).get("type")
    kind = kind or (artifact.get("info") or {}).get("type_info", {}).get("type")
    updates = artifact.get("updates") or []
    if not kind and updates:
        kind = (updates[0].get("type_info") or {}).get("type")
    return kind or "unknown"


def validate_artifact(path: Path) -> None:
    """Check that an archive starts with a Mender artifact version entry.

    :param path: Local artifact archive to inspect.
    :raises ValueError: The archive cannot be read or its version entry does not identify the Mender
        format.
    """
    try:
        with tarfile.open(path, "r:*") as archive:
            first = archive.next()
            if first is None or first.name != "version":
                raise ValueError("invalid artifact format: the `version` entry is missing")
            stream = archive.extractfile(first)
            if stream is None:
                raise ValueError("invalid artifact version entry")
            version = json.load(stream)
            if version.get("format") != "mender":
                raise ValueError("invalid artifact format")
    except (tarfile.TarError, OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Mender artifact: {exc}") from exc


class MenderAPI:
    """Access Mender management endpoints using a shared HTTP session."""

    def __init__(self, config: Config):
        """Create an HTTP session with the configured token and TLS policy.

        :param config: Resolved server, authentication, and request settings.
        """
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "mender-richer-cli/0.1.1"})
        if config.token:
            self.session.headers["Authorization"] = f"Bearer {config.token}"
        self.verify = not config.skip_verify

    def request(self, method: str, path: str, *, expected: tuple[int, ...] = (200,),
                **kwargs: Any) -> requests.Response:
        """Send a management request and check its HTTP status.

        :param method: HTTP method to send.
        :param path: Management endpoint path or absolute HTTP URL.
        :param expected: Accepted HTTP response status codes.
        :param kwargs: Additional arguments passed to ``requests.Session.request``; timeout defaults
            to 15 seconds to connect and 120 seconds to read.
        :returns: The response, which the caller must close when finished.
        :raises RuntimeError: The server returns a status outside ``expected``.
        :raises requests.RequestException: The request fails at the transport layer.
        """
        url = path if path.startswith(("http://", "https://")) else self.config.server + path
        timeout = kwargs.pop("timeout", (15, 120))
        response = self.session.request(method, url, timeout=timeout, verify=self.verify, **kwargs)
        if response.status_code not in expected:
            body = response.text.strip()[:500]
            request_id = response.headers.get("X-MEN-RequestID")
            suffix = f" (request-id: {request_id})" if request_id else ""
            hint = " Verify the selected server and run `python src/main.py login` again." if response.status_code == 401 else ""
            response.close()
            raise RuntimeError(f"Mender returned HTTP {response.status_code} for {url}{suffix}: {body}.{hint}")
        if self.config.verbose:
            print(f"{method} {url}", file=sys.stderr)
        return response

    def json(self, method: str, path: str, *, expected: tuple[int, ...] = (200,),
             body: Any = None, params: dict[str, Any] | None = None) -> tuple[Any, requests.Response]:
        """Send a request and decode its JSON response.

        :param method: HTTP method to send.
        :param path: Management endpoint path or absolute HTTP URL.
        :param expected: Accepted HTTP response status codes.
        :param body: JSON request body, if any.
        :param params: URL query parameters, if any.
        :returns: A pair containing the decoded value and response; the value is ``None`` for an
            empty response.
        :raises RuntimeError: The response status is unexpected or its content is invalid JSON.
        """
        response = self.request(method, path, expected=expected, json=body, params=params)
        if response.status_code == 204 or not response.content:
            return None, response
        try:
            return response.json(), response
        except requests.JSONDecodeError as exc:
            raise RuntimeError("invalid JSON response from Mender") from exc

    def login(self, username: str, password: str, code: str | None = None) -> bytes:
        """Exchange account credentials for an authentication token.

        :param username: Account email address.
        :param password: Account password.
        :param code: Optional two-factor authentication code.
        :returns: The token as returned by the server, in bytes.
        :raises RuntimeError: Authentication fails or the server returns an empty token.
        """
        response = requests.post(self.config.server + LOGIN, auth=(username, password),
                                 json={"token2fa": code} if code else None,
                                 timeout=(15, 15), verify=self.verify)
        if response.status_code != 200:
            raise RuntimeError(f"login failed: HTTP {response.status_code}: {response.text[:500]}")
        if not response.content.strip():
            raise RuntimeError("server returned an empty token")
        return response.content

    def list_page(self, path: str, page: int, per_page: int,
                  body: dict[str, Any] | None = None,
                  params: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], int | None]:
        """Fetch one page using GET or an inventory search POST.

        :param path: Management endpoint to query.
        :param page: One-based page number.
        :param per_page: Maximum records requested per page.
        :param body: Search body; supplying it selects POST and places pagination in the body.
        :param params: Extra GET query parameters; unused when a search body is supplied.
        :returns: The records and total record count, or ``None`` when the count is unavailable.
        :raises ValueError: The page number or page size is less than one.
        """
        if page < 1 or per_page < 1:
            raise ValueError("page and per-page must be greater than zero")
        query = {"page": page, "per_page": per_page, **(params or {})}
        data, response = self.json("POST" if body is not None else "GET", path,
                                   body={**body, "page": page, "per_page": per_page} if body is not None else None,
                                   params=None if body is not None else query)
        try:
            total = int(response.headers["X-Total-Count"])
        except (KeyError, ValueError):
            total = None
        return data or [], total

    def all_pages(self, path: str, body: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Collect records in batches of 100 until pagination is exhausted.

        :param path: Management endpoint to query.
        :param body: Optional search body for a POST endpoint.
        :returns: All records in server pagination order.
        """
        result: list[dict[str, Any]] = []
        page = 1
        while True:
            rows, total = self.list_page(path, page, 100, body)
            result.extend(rows)
            if not rows or (total is not None and page * 100 >= total) or (total is None and len(rows) < 100):
                return result
            page += 1

    @staticmethod
    def inventory_search_body() -> dict[str, Any]:
        """Build the accepted-device inventory search and requested attributes.

        :returns: The filters and attribute projection used by the interactive picker.
        """
        attributes = [{"scope": scope, "attribute": name} for scope, name in (
            ("identity", "status"), ("inventory", "artifact_name"),
            ("inventory", "hostname"), ("tags", "name"),
            ("identity", "id"), ("system", "updated_ts"))]
        return {"filters": [{"scope": "identity", "attribute": "status", "type": "$eq", "value": "accepted"}],
                "attributes": attributes}

    def devices(self) -> list[dict[str, Any]]:
        """Fetch all accepted devices and sort them by display name.

        :returns: Accepted device records sorted without regard to letter case.
        """
        return sorted(self.all_pages(INVENTORY_SEARCH, self.inventory_search_body()),
                      key=lambda d: device_name(d).casefold())

    def inventory(self, device_id: str) -> dict[str, Any]:
        """Fetch the full inventory record for a device.

        :param device_id: Mender device identifier.
        :returns: The device inventory record.
        """
        data, _ = self.json("GET", f"{INVENTORY_DEVICE}/{quote(device_id, safe='')}")
        return data

    def connected(self, device_id: str) -> bool:
        """Check the device-connect status of a device.

        :param device_id: Mender device identifier.
        :returns: Whether the reported status is ``connected``.
        """
        data, _ = self.json("GET", f"{DEVICE_CONNECT}/{quote(device_id, safe='')}")
        return str(data.get("status", "")).casefold() == "connected"

    def display_name(self, device_id: str) -> str:
        """Look up a device name, falling back to its ID if lookup fails.

        :param device_id: Mender device identifier.
        :returns: The inventory display name, or the unchanged device ID.
        """
        try:
            return device_name(self.inventory(device_id))
        except Exception:
            return device_id

    def artifacts(self) -> list[dict[str, Any]]:
        """Fetch every page of artifact metadata.

        :returns: All artifact records returned by the server.
        """
        return self.all_pages(ARTIFACT_LIST)

    def upload_artifact(self, path: Path, description: str = "", progress: bool = True) -> None:
        """Validate and stream an artifact to the server.

        :param path: Local Mender artifact archive.
        :param description: Description attached to the uploaded artifact.
        :param progress: Whether to report upload progress on standard error.
        :raises ValueError: The file fails artifact validation.
        """
        validate_artifact(path)
        self._multipart(ARTIFACTS, "POST", path, "artifact",
                        {"size": str(path.stat().st_size), "description": description}, progress,
                        "application/vnd.mender-artifact")

    def _multipart(self, endpoint: str, method: str, path: Path, field: str,
                   fields: dict[str, str], progress: bool,
                   mime: str = "application/octet-stream") -> None:
        """Stream a local file as a multipart request without buffering it in memory.

        :param endpoint: Management endpoint receiving the upload.
        :param method: HTTP method used for the upload.
        :param path: Local file to read.
        :param field: Multipart field name for the file.
        :param fields: Additional multipart text fields.
        :param progress: Whether to report uploaded bytes on standard error.
        :param mime: Media type assigned to the file part.
        :raises RuntimeError: Streaming dependencies are missing or the server rejects the upload.
        """
        try:
            from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor
        except ImportError as exc:
            raise RuntimeError("streaming upload requires requests-toolbelt; install project dependencies") from exc
        with path.open("rb") as source:
            encoder = MultipartEncoder({**fields, field: (path.name, source, mime)})
            last = 0
            def report(monitor: Any) -> None:
                """Report upload progress after another 256 KiB has been read.

                :param monitor: Multipart monitor exposing bytes read and total encoded length.
                """
                nonlocal last
                if progress and monitor.bytes_read - last >= 256 * 1024:
                    last = monitor.bytes_read
                    print(f"\rUploading {last}/{monitor.len} bytes", end="", file=sys.stderr)
            monitor = MultipartEncoderMonitor(encoder, report)
            response = self.request(method, endpoint, expected=(201,), data=monitor,
                                    headers={"Content-Type": monitor.content_type, "Content-Length": str(monitor.len)})
            response.close()
        if progress:
            print(file=__import__("sys").stderr)

    def deploy(self, artifact_name: str, device_ids: list[str], name: str | None = None) -> None:
        """Create a deployment for an artifact and explicit device IDs.

        :param artifact_name: Artifact name to deploy.
        :param device_ids: Target device identifiers.
        :param name: Deployment name; generated from the artifact and targets when omitted.
        :raises ValueError: No target devices are supplied.
        """
        if not device_ids:
            raise ValueError("at least one device is required for a deployment")
        if not name:
            target = device_ids[0] if len(device_ids) == 1 else f"{len(device_ids)} devices"
            name = f"Deployment of {artifact_name} to {target}"
        self.json("POST", DEPLOYMENTS, expected=(201,),
                  body={"name": name, "artifact_name": artifact_name, "devices": device_ids})

    def deployments(self, page: int, per_page: int, status: str | None = None,
                    search: str | None = None) -> tuple[list[dict[str, Any]], int | None]:
        """Fetch a page of deployments with optional status and text filters.

        :param page: One-based page number.
        :param per_page: Maximum records requested per page.
        :param status: Optional deployment status filter.
        :param search: Optional search text for deployment names and descriptions.
        :returns: The deployment records and total count, or ``None`` if no valid count is provided.
        """
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if status:
            params["status"] = status
        if search:
            params["search"] = search
        data, response = self.json("GET", DEPLOYMENTS, params=params)
        try:
            total = int(response.headers["X-Total-Count"])
        except (KeyError, ValueError):
            total = None
        return data or [], total

    def deployment_details(self, deployment_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Fetch a deployment and its device-state statistics.

        :param deployment_id: Deployment identifier.
        :returns: The deployment record and statistics mapping.
        """
        path = f"{DEPLOYMENTS}/{quote(deployment_id, safe='')}"
        deployment, _ = self.json("GET", path)
        stats, _ = self.json("GET", path + "/statistics")
        return deployment, stats

    def cancel_deployment(self, deployment_id: str) -> None:
        """Request that a deployment be aborted.

        :param deployment_id: Deployment identifier.
        """
        self.json("PUT", f"{DEPLOYMENTS}/{quote(deployment_id, safe='')}/status",
                  expected=(204,), body={"status": "aborted"})

    def delete_artifact(self, artifact_id: str) -> None:
        """Delete an artifact by its identifier.

        :param artifact_id: Artifact identifier.
        """
        self.request("DELETE", f"{ARTIFACTS}/{quote(artifact_id, safe='')}", expected=(204,)).close()

    def download_artifact(self, artifact_id: str, destination: Path, progress: bool = True) -> Path:
        """Resolve an artifact download link and save its contents.

        :param artifact_id: Artifact identifier.
        :param destination: Output filename or existing directory; directories use the artifact name
            with a ``.mender`` suffix.
        :param progress: Whether to report downloaded bytes on standard error.
        :returns: The final output path.
        """
        path = f"{ARTIFACTS}/{quote(artifact_id, safe='')}"
        link, _ = self.json("GET", path + "/download")
        artifact, _ = self.json("GET", path)
        filename = Path(artifact.get("name", "artifact") + ".mender").name
        destination = self._destination(destination, filename)
        return self._download(urljoin(self.config.server + "/", link["uri"]), destination,
                              int(artifact.get("size", 0)), progress, authenticated=False)

    def upload_file(self, device_id: str, local: Path, remote: str) -> None:
        """Upload a file to a connected device, including its local mode.

        :param device_id: Target device identifier.
        :param local: Local file to upload.
        :param remote: Destination path on the device.
        :raises RuntimeError: The device is disconnected or the server rejects the transfer.
        """
        self.require_connected(device_id)
        mode = f"{local.stat().st_mode:o}"
        self._multipart(f"{DEVICE_CONNECT}/{quote(device_id, safe='')}/upload", "PUT", local,
                        "file", {"path": remote, "mode": mode}, True)

    def download_file(self, device_id: str, remote: str, destination: Path) -> Path:
        """Download a file from a connected device.

        :param device_id: Source device identifier.
        :param remote: Source path on the device.
        :param destination: Output filename or existing directory.
        :returns: The final local output path.
        :raises RuntimeError: The device is disconnected, the transfer fails, or the byte count is
            incorrect.
        """
        self.require_connected(device_id)
        destination = self._destination(destination, Path(remote).name)
        response = self.request("GET", f"{DEVICE_CONNECT}/{quote(device_id, safe='')}/download",
                                params={"path": remote}, stream=True)
        try:
            size = int(response.headers.get("X-MEN-FILE-SIZE", response.headers.get("Content-Length", 0)))
            return self._stream_download(response, destination, size, True)
        finally:
            response.close()

    def require_connected(self, device_id: str) -> None:
        """Reject operations on a device without an active connection.

        :param device_id: Device identifier to check.
        :raises RuntimeError: The device is not connected.
        """
        if not self.connected(device_id):
            raise RuntimeError(f"device {device_id} is not connected")

    @staticmethod
    def _destination(destination: Path, filename: str) -> Path:
        """Resolve a filename against an existing destination directory.

        :param destination: Output filename or existing directory.
        :param filename: Filename to append when the destination is a directory.
        :returns: The resolved output filename.
        """
        if destination.is_dir():
            return destination / filename
        return destination

    def _download(self, url: str, destination: Path, size: int, progress: bool,
                  authenticated: bool = True) -> Path:
        """Download a URL using authenticated or independent HTTP requests.

        :param url: Absolute URL to fetch.
        :param destination: Final local output filename.
        :param size: Expected byte count, or zero to skip size validation.
        :param progress: Whether to report progress on standard error.
        :param authenticated: Whether to reuse the authenticated session; false avoids sending its
            credentials.
        :returns: The final local output path.
        :raises RuntimeError: The server does not return HTTP 200 or size validation fails.
        """
        headers = {"Authorization": f"Bearer {self.config.token}"} if authenticated and self.config.token else {}
        if authenticated:
            response = self.session.get(url, headers=headers, stream=True, timeout=(15, 120), verify=self.verify)
        else:
            response = requests.get(url, stream=True, timeout=(15, 120), verify=self.verify)
        if response.status_code != 200:
            response.close()
            raise RuntimeError(f"download failed: HTTP {response.status_code}")
        try:
            return self._stream_download(response, destination, size, progress)
        finally:
            response.close()

    @staticmethod
    def _stream_download(response: requests.Response, destination: Path,
                         size: int, progress: bool) -> Path:
        """Write a response to a temporary file, then atomically replace the destination.

        :param response: Streaming HTTP response; its lifetime remains the caller's responsibility.
        :param destination: Final local output filename.
        :param size: Expected byte count, or zero to skip size validation.
        :param progress: Whether to report progress on standard error.
        :returns: The final local output path.
        :raises RuntimeError: The downloaded byte count differs from a nonzero expected size.
        :raises OSError: The destination cannot be written or replaced.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".mender-download-", dir=destination.parent)
        received = 0
        try:
            with os.fdopen(fd, "wb") as output:
                for chunk in response.iter_content(1024 * 128):
                    if not chunk:
                        continue
                    output.write(chunk)
                    received += len(chunk)
                    if progress and received % (1024 * 1024) < len(chunk):
                        print(f"\rDownloading {received}/{size or '?'} bytes", end="", file=sys.stderr)
                output.flush()
                os.fsync(output.fileno())
            if size and received != size:
                raise RuntimeError(f"download size mismatch: received {received} bytes, expected {size}")
            os.replace(temporary, destination)
            if progress:
                print(file=__import__("sys").stderr)
            return destination
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
