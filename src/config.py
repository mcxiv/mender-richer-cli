"""Configuration resolution, private token storage, and per-server favorites."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

DEFAULT_SERVER = "https://hosted.mender.io"


def _first(*values: str | None) -> str | None:
    """Select the first nonempty value in precedence order.

    :param values: Candidate strings, ordered from highest to lowest priority.
    :returns: The first truthy string, or ``None``.
    """
    return next((value for value in values if value), None)


def normalize_server(value: str) -> str:
    """Normalize a server URL, adding HTTPS when no scheme is supplied.

    :param value: Server hostname or HTTP(S) URL.
    :returns: The URL with a trimmed trailing path slash and no fragment.
    :raises ValueError: The URL has no network location or uses a non-HTTP scheme.
    """
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("server URL must use HTTP or HTTPS")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), parsed.query, ""))


def config_candidates() -> list[Path]:
    """List configuration paths in local, user, then system precedence order.

    :returns: The paths to check; the first existing file takes precedence.
    """
    home = Path.home()
    paths = [Path(".mender-richer-clirc"), Path(".mender-clirc"), home / ".mender-richer-clirc",
             home / ".mender-clirc"]
    if sys.platform == "win32":
        if os.environ.get("PROGRAMDATA"):
            paths.append(Path(os.environ["PROGRAMDATA"]) / "mender-richer-cli" / ".mender-richer-clirc")
    else:
        paths.extend([Path("/etc/mender-richer-cli/.mender-richer-clirc"),
                      Path("/etc/mender/.mender-clirc")])
    return paths


def default_token_path() -> Path:
    """Locate the token file using XDG_CACHE_HOME or the platform's user cache.

    :returns: The shared Mender authentication-token path.
    """
    default = (Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
               if sys.platform == "win32" else Path.home() / ".cache")
    cache = Path(os.environ.get("XDG_CACHE_HOME", default))
    return cache / "mender" / "authtoken"


def favorite_path() -> Path:
    """Locate favorites using XDG_CONFIG_HOME or the platform's user configuration directory.

    :returns: The application-specific favorites JSON path.
    """
    default = (Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
               if sys.platform == "win32" else Path.home() / ".config")
    config = Path(os.environ.get("XDG_CONFIG_HOME", default))
    return config / "mender-richer-cli" / "favorites.json"


@dataclass(slots=True)
class Config:
    """Store resolved connection settings and credential-file locations.

    :param server: Normalized Mender server URL.
    :param token: Authentication token, or ``None`` when unavailable.
    :param token_path: Path used to read or save the authentication token.
    :param username: Default login email address, if configured.
    :param password: Default login password, if configured.
    :param skip_verify: Whether to disable TLS certificate verification.
    :param verbose: Whether to log request methods and URLs.
    """

    server: str
    token: str | None
    token_path: Path
    username: str | None
    password: str | None
    skip_verify: bool = False
    verbose: bool = False

    @classmethod
    def resolve(cls, *, server: str | None, token_value: str | None,
                token_path: Path | None, skip_verify: bool, verbose: bool) -> "Config":
        """Resolve explicit settings, environment variables, and the first configuration file.

        :param server: Explicit server URL; otherwise use the environment, file, or hosted default.
        :param token_value: Explicit token; otherwise use the environment, file, or saved token.
        :param token_path: Explicit token-file path, or ``None`` to use the shared cache path.
        :param skip_verify: Whether to disable TLS certificate verification.
        :param verbose: Whether to log request methods and URLs.
        :returns: The resolved settings; an unreadable token file leaves the token unset.
        :raises ValueError: Configuration JSON or the server URL is invalid, or token-file and
            token-value options conflict.
        """
        file_data: dict[str, object] = {}
        for candidate in config_candidates():
            try:
                file_data = json.loads(candidate.read_text(encoding="utf-8"))
                break
            except FileNotFoundError:
                continue
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid configuration {candidate}: {exc}") from exc
        selected_server = normalize_server(_first(server, os.getenv("MENDER_SERVER"),
                                                   str(file_data.get("server") or ""),
                                                   DEFAULT_SERVER) or DEFAULT_SERVER)
        env_token = os.getenv("MENDER_TOKEN")
        if token_path and _first(token_value, env_token):
            raise ValueError("--token-value and --token cannot be used together")
        path = token_path or default_token_path()
        token = _first(token_value, env_token, str(file_data.get("token_value") or ""),
                       str(file_data.get("token-value") or ""))
        if not token:
            try:
                token = path.read_text(encoding="utf-8").strip()
            except OSError:
                token = None
        return cls(selected_server, token, path,
                   _first(os.getenv("MENDER_USERNAME"), str(file_data.get("username") or "")),
                   _first(os.getenv("MENDER_PASSWORD"), str(file_data.get("password") or "")),
                   skip_verify, verbose)

    def save_token(self, token: bytes) -> None:
        """Atomically save a token using POSIX mode 0600 or inherited Windows directory ACLs.

        :param token: Token bytes to store without modification.
        :raises OSError: The token directory or file cannot be created, written, or replaced.
        """
        self.token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".mender-richer-cli-", dir=self.token_path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                if sys.platform != "win32":
                    os.fchmod(output.fileno(), 0o600)
                output.write(token)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.token_path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


class Favorites:
    """Persist one favorite device per server in a JSON file."""

    def __init__(self, path: Path | None = None):
        """Load saved favorites, treating a missing file as an empty collection.

        :param path: Favorites JSON path, or ``None`` for the XDG-based default.
        :raises json.JSONDecodeError: An existing favorites file contains invalid JSON.
        :raises OSError: An existing favorites file cannot be read.
        """
        self.path = path or favorite_path()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        self.servers: dict[str, dict[str, str]] = data.get("servers", {})

    def get(self, server: str) -> dict[str, str] | None:
        """Look up a server's favorite device.

        :param server: Server URL used as the favorites key.
        :returns: The favorite device record, or ``None``.
        """
        return self.servers.get(server)

    def toggle(self, server: str, device: dict[str, str]) -> bool:
        """Select a favorite, or clear it when the same device is selected again.

        :param server: Server URL used as the favorites key.
        :param device: Device record containing ``id`` and ``name``.
        :returns: Whether the device is now the favorite.
        :raises OSError: The updated favorites file cannot be written or replaced.
        """
        if (self.get(server) or {}).get("id") == device["id"]:
            self.servers.pop(server, None)
            selected = False
        else:
            self.servers[server] = device
            selected = True
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".favorites-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                if sys.platform != "win32":
                    os.fchmod(output.fileno(), 0o600)
                json.dump({"servers": self.servers}, output, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return selected
