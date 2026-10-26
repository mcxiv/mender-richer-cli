"""Command-line entry point and interactive Mender device workflows."""

from __future__ import annotations

import json
import ntpath
import os
import sys
import time
from pathlib import Path
from typing import Any

import click
from rich.console import Console

from api import (ARTIFACTS, ARTIFACT_LIST, DEPLOYMENTS, DEVICES, INVENTORY_DEVICE,
                  MenderAPI, artifact_kind, device_name, device_updated)
from config import Config, Favorites
from display import confirm, flatten_inventory, human_size, pick, print_json, table
import remote

__version__ = "0.1.1"

CONSOLE = Console(no_color=True, highlight=False)


class Context:
    """Share resolved configuration and an API client across CLI commands."""

    def __init__(self, config: Config):
        """Create the API client used by commands in this invocation.

        :param config: Resolved connection and authentication settings.
        """
        self.config = config
        self.api = MenderAPI(config)


def _say(text: str) -> None:
    """Print a status message on standard output.

    :param text: Message to print.
    """
    print(text)


def _deployment_table(deployment: dict[str, Any], stats: dict[str, Any]) -> None:
    """Print deployment metadata, aggregate progress, and device-state counts.

    :param deployment: Deployment record returned by the API.
    :param stats: Mapping of device state names to counts.
    """
    table(["Deployment", "Value"], [
        ["Name", deployment.get("name", "")], ["ID", deployment.get("id", "")],
        ["Artifact", deployment.get("artifact_name", "")], ["Status", deployment.get("status", "")],
        ["Created", deployment.get("created", "")], ["Finished", deployment.get("finished", "—") or "—"],
        ["Devices", deployment.get("device_count", 0)],
    ])
    terminal = sum(int(stats.get(key, 0) or 0) for key in
                   ("success", "failure", "noartifact", "already-installed", "already_installed", "aborted"))
    active = sum(int(stats.get(key, 0) or 0) for key in
                 ("downloading", "installing", "rebooting", "pause_before_installing",
                  "pause_before_rebooting", "pause_before_committing"))
    total = int(deployment.get("device_count", 0) or 0)
    percent = min(100, round(terminal * 100 / total)) if total else 0
    print(f"Progress: {terminal}/{total} ({percent}%) · {active} active")
    table(["Device state", "Count"], [[key, value] for key, value in sorted(stats.items())])


def _require_ids(ids: tuple[str, ...]) -> list[str]:
    """Require at least one device ID and convert the tuple to a list.

    :param ids: Device identifiers supplied by the caller.
    :returns: The supplied identifiers as a list.
    :raises click.ClickException: No device identifiers were supplied.
    """
    if not ids:
        raise click.ClickException("at least one device ID is required")
    return list(ids)


class MenderGroup(click.Group):
    """Accept a leading server/token pair alongside standard Click command syntax."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        """Translate positional credentials into global options before Click parsing.

        :param ctx: Click context being populated.
        :param args: Arguments excluding the executable name.
        :returns: Unconsumed arguments returned by the Click group parser.
        :raises click.UsageError: A positional server is supplied without a token.
        """
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            if len(args) < 2 or args[1].startswith("-"):
                raise click.UsageError("provide both SERVER and TOKEN: mrc SERVER TOKEN", ctx)
            server, token, *remaining = args
            args = [f"--server={server}", f"--token-value={token}", *remaining]
        return super().parse_args(ctx, args)


@click.group(cls=MenderGroup, invoke_without_command=True,
             context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="mender-richer-cli")
@click.option("--server", envvar="MENDER_SERVER", help="Mender server URL.")
@click.option("--token", "token_path", type=click.Path(path_type=Path), help="File containing the JWT token.")
@click.option("--token-value", envvar="MENDER_TOKEN", hide_input=True, help="JWT token or API key.")
@click.option("-k", "--skip-verify", is_flag=True, help="Disable TLS certificate verification.")
@click.option("-v", "--verbose", is_flag=True, help="Enable diagnostic request logging.")
@click.option("--favorite", "open_favorite", is_flag=True, help="Open a remote terminal on the favorite device.")
@click.pass_context
def cli(ctx: click.Context, server: str | None, token_path: Path | None, token_value: str | None,
        skip_verify: bool, verbose: bool, open_favorite: bool) -> None:
    """A native Python CLI for Mender device operations.

    Use mrc SERVER TOKEN to open the interactive picker, or
    mrc SERVER TOKEN COMMAND to run a command with those credentials.

    \f

    :param ctx: Click invocation context receiving the shared application object.
    :param server: Explicit server URL, if supplied.
    :param token_path: Optional path to an authentication-token file.
    :param token_value: Explicit authentication token, if supplied.
    :param skip_verify: Whether to disable TLS certificate verification.
    :param verbose: Whether to log request methods and URLs.
    :param open_favorite: Whether to open the favorite device terminal when no command is given.
    :raises click.ClickException: Configuration is invalid, authentication is missing, or no
        favorite exists when requested.
    """
    try:
        config = Config.resolve(server=server, token_value=token_value, token_path=token_path,
                                skip_verify=skip_verify, verbose=verbose)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    ctx.obj = Context(config)
    if ctx.invoked_subcommand not in {"login", "completion"} and not config.token:
        raise click.ClickException("no token is available; run `python src/main.py login` or use --token-value")
    if ctx.invoked_subcommand is None:
        if open_favorite:
            favorite = Favorites().get(config.server)
            if not favorite:
                raise click.ClickException("no favorite device for this server; mark one in the interactive device list")
            _run(lambda: remote.terminal(ctx.obj.api, favorite["id"]))
        else:
            _run(lambda: interactive(ctx.obj))


def _run(fn: Any) -> Any:
    """Translate expected operational failures into concise Click errors.

    :param fn: Zero-argument operation to execute.
    :returns: The operation's return value.
    :raises click.ClickException: The operation raises RuntimeError, ValueError, OSError, or
        TimeoutError.
    """
    try:
        return fn()
    except (RuntimeError, ValueError, OSError, TimeoutError) as exc:
        raise click.ClickException(str(exc)) from exc


@cli.command("interactive")
@click.pass_obj
def interactive_command(app: Context) -> None:
    """Open the searchable interactive interface.

    \f

    :param app: Shared application configuration and API client.
    """
    _run(lambda: interactive(app))


@cli.command("login")
@click.option("--username", help="Account email.")
@click.option("--password", hide_input=True, help="Account password (prefer hidden prompt).")
@click.option("--2fa-code", "two_factor_code", help="Two-factor authentication code.")
@click.pass_obj
def login_command(app: Context, username: str | None, password: str | None,
                  two_factor_code: str | None) -> None:
    """Authenticate and save the token locally.

    \f

    :param app: Shared application configuration and API client.
    :param username: Login email; prompted for when neither supplied nor configured.
    :param password: Login password; prompted for with hidden input when unavailable.
    :param two_factor_code: Optional two-factor authentication code.
    """
    username = username or app.config.username
    password = password or app.config.password
    if not username:
        username = click.prompt("Email address")
    if not password:
        password = click.prompt("Password", hide_input=True)
    token = _run(lambda: app.api.login(username, password, two_factor_code))
    app.config.save_token(token)
    _say(f"Login successful; token saved to {app.config.token_path}.")


@cli.group("devices")
def devices_group() -> None:
    """Browse devices and their inventory."""


@devices_group.command("list")
@click.option("-N", "--per-page", type=click.IntRange(min=1), default=20, show_default=True)
@click.option("-P", "--page", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("-r", "--raw", is_flag=True, help="Output JSON.")
@click.pass_obj
def devices_list(app: Context, per_page: int, page: int, raw: bool) -> None:
    """List devices.

    \f

    :param app: Shared application configuration and API client.
    :param per_page: Maximum records requested per page.
    :param page: One-based page number.
    :param raw: Whether to print JSON instead of formatted output.
    """
    rows, _ = _run(lambda: app.api.list_page(DEVICES, page, per_page))
    if raw:
        print_json(rows)
    else:
        table(["Device ID", "Status", "Identity", "Updated"], [
            [row.get("id", ""), row.get("status", ""),
             json.dumps(row.get("identity_data", {}), separators=(",", ":")), row.get("updated_ts", "")]
            for row in rows])


@devices_group.command("inventory")
@click.argument("device_id")
@click.option("--raw", is_flag=True, help="Output JSON.")
@click.pass_obj
def inventory_command(app: Context, device_id: str, raw: bool) -> None:
    """Show the complete inventory for a device.

    \f

    :param app: Shared application configuration and API client.
    :param device_id: Target device identifier.
    :param raw: Whether to print JSON instead of formatted output.
    """
    inventory = _run(lambda: app.api.inventory(device_id))
    if raw:
        print_json(inventory)
    elif sys.stdin.isatty() and sys.stdout.isatty():
        rows = flatten_inventory(inventory)
        pick(f"Inventory for {device_id}", ["Attribute", "Value"], rows, viewer=True)
    else:
        table(["Attribute", "Value"], flatten_inventory(inventory))


@cli.group("artifacts")
def artifacts_group() -> None:
    """Manage artifacts and create deployments."""


@artifacts_group.command("list")
@click.option("-N", "--per-page", type=click.IntRange(min=1), default=20, show_default=True)
@click.option("-P", "--page", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("-r", "--raw", is_flag=True, help="Output JSON.")
@click.option("--sort", type=click.Choice(["name", "type"], case_sensitive=False), default="name")
@click.pass_obj
def artifacts_list(app: Context, per_page: int, page: int, raw: bool, sort: str) -> None:
    """List available artifacts.

    \f

    :param app: Shared application configuration and API client.
    :param per_page: Maximum records requested per page.
    :param page: One-based page number.
    :param raw: Whether to print JSON instead of formatted output.
    :param sort: Artifact sort key: name or type.
    """
    rows, _ = _run(lambda: app.api.list_page(ARTIFACT_LIST, page, per_page))
    if raw:
        print_json(rows)
        return
    rows.sort(key=lambda a: ((artifact_kind(a).casefold(), a.get("name", "").casefold())
                             if sort.casefold() == "type" else (a.get("name", "").casefold(),)))
    table(["Name", "Type", "Artifact ID", "Size", "Description"], [
        [r.get("name", ""), artifact_kind(r), r.get("id", ""), human_size(int(r.get("size", 0))), r.get("description", "—")]
        for r in rows])


@artifacts_group.command("upload")
@click.argument("artifact", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--description", default="", help="Artifact description.")
@click.option("--no-progress", is_flag=True, help="Hide upload progress.")
@click.pass_obj
def artifacts_upload(app: Context, artifact: Path, description: str, no_progress: bool) -> None:
    """Upload an artifact to the server.

    \f

    :param app: Shared application configuration and API client.
    :param artifact: Local Mender artifact archive to upload.
    :param description: Description attached to the artifact.
    :param no_progress: Whether to suppress transfer progress.
    """
    _run(lambda: app.api.upload_artifact(artifact, description, not no_progress))
    _say("Artifact uploaded.")


@artifacts_group.command("download")
@click.argument("artifact_id")
@click.option("--destination-path", type=click.Path(path_type=Path), default=Path("."), show_default=True)
@click.option("--no-progress", is_flag=True, help="Hide download progress.")
@click.pass_obj
def artifacts_download(app: Context, artifact_id: str, destination_path: Path, no_progress: bool) -> None:
    """Download an artifact by ID.

    \f

    :param app: Shared application configuration and API client.
    :param artifact_id: Artifact identifier.
    :param destination_path: Local output filename or existing directory.
    :param no_progress: Whether to suppress transfer progress.
    """
    path = _run(lambda: app.api.download_artifact(artifact_id, destination_path, not no_progress))
    _say(f"Artifact saved to {path}.")


@artifacts_group.command("delete")
@click.argument("artifact_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_obj
def artifacts_delete(app: Context, artifact_id: str, yes: bool) -> None:
    """Delete an artifact.

    \f

    :param app: Shared application configuration and API client.
    :param artifact_id: Artifact identifier.
    :param yes: Whether to skip the confirmation prompt.
    """
    if confirm(f"Permanently delete artifact {artifact_id}?", yes):
        _run(lambda: app.api.delete_artifact(artifact_id))
        _say("Artifact deleted.")


@artifacts_group.command("deploy")
@click.argument("artifact_name")
@click.argument("device_ids", nargs=-1, required=True)
@click.option("--name", "deployment_name", help="Deployment name.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_obj
def artifacts_deploy(app: Context, artifact_name: str, device_ids: tuple[str, ...],
                     deployment_name: str | None, yes: bool) -> None:
    """Create a deployment for one or more devices.

    \f

    :param app: Shared application configuration and API client.
    :param artifact_name: Artifact name to deploy.
    :param device_ids: Identifiers of the devices receiving the deployment.
    :param deployment_name: Optional deployment name; generated when omitted.
    :param yes: Whether to skip the confirmation prompt.
    """
    ids = list(device_ids)
    target = app.api.display_name(ids[0]) + f" ({ids[0]})" if len(ids) == 1 else f"{len(ids)} devices"
    if confirm(f"Deploy {artifact_name} to {target}?", yes):
        _run(lambda: app.api.deploy(artifact_name, ids, deployment_name))
        _say("Deployment created.")


@cli.group("deployments")
def deployments_group() -> None:
    """Inspect, follow, and cancel deployments."""


@deployments_group.command("list")
@click.option("-N", "--per-page", type=click.IntRange(min=1), default=20, show_default=True)
@click.option("-P", "--page", type=click.IntRange(min=1), default=1, show_default=True)
@click.option("-r", "--raw", is_flag=True, help="Output JSON.")
@click.option("--status", type=click.Choice(["scheduled", "pending", "inprogress", "finished"]))
@click.option("--search", help="Search deployment names and descriptions.")
@click.pass_obj
def deployments_list(app: Context, per_page: int, page: int, raw: bool,
                     status: str | None, search: str | None) -> None:
    """List deployments.

    \f

    :param app: Shared application configuration and API client.
    :param per_page: Maximum records requested per page.
    :param page: One-based page number.
    :param raw: Whether to print JSON instead of formatted output.
    :param status: Optional deployment status filter.
    :param search: Optional deployment search text.
    """
    rows, total = _run(lambda: app.api.deployments(page, per_page, status, search))
    if raw:
        print_json(rows)
        return
    table(["Name", "Artifact", "Status", "Devices", "Created", "Deployment ID"], [
        [r.get("name", ""), r.get("artifact_name", ""), r.get("status", ""),
         r.get("device_count", 0), r.get("created", ""), r.get("id", "")] for r in rows])
    if total is not None:
        print(f"Page {page} · {len(rows)} shown · {total} total")


@deployments_group.command("show")
@click.argument("deployment_id")
@click.option("--raw", is_flag=True, help="Output JSON.")
@click.pass_obj
def deployments_show(app: Context, deployment_id: str, raw: bool) -> None:
    """Show deployment details and device statistics.

    \f

    :param app: Shared application configuration and API client.
    :param deployment_id: Deployment identifier.
    :param raw: Whether to print JSON instead of formatted output.
    """
    deployment, stats = _run(lambda: app.api.deployment_details(deployment_id))
    if raw:
        print_json({"deployment": deployment, "statistics": stats})
    else:
        _deployment_table(deployment, stats)


@deployments_group.command("watch")
@click.argument("deployment_id")
@click.option("--interval", type=click.IntRange(min=1), default=2, show_default=True)
@click.pass_obj
def deployments_watch(app: Context, deployment_id: str, interval: int) -> None:
    """Refresh deployment progress until it finishes or Ctrl+C is pressed.

    \f

    :param app: Shared application configuration and API client.
    :param deployment_id: Deployment identifier.
    :param interval: Seconds between progress refreshes.
    """
    try:
        while True:
            deployment, stats = _run(lambda: app.api.deployment_details(deployment_id))
            if sys.stdout.isatty():
                print("\033[H\033[2J", end="")
            _deployment_table(deployment, stats)
            if deployment.get("finished") or str(deployment.get("status", "")).casefold() == "finished":
                _say("Deployment finished.")
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        _say("Watch stopped; the deployment is still running.")


@deployments_group.command("cancel")
@click.argument("deployment_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_obj
def deployments_cancel(app: Context, deployment_id: str, yes: bool) -> None:
    """Cancel an unfinished deployment.

    \f

    :param app: Shared application configuration and API client.
    :param deployment_id: Deployment identifier.
    :param yes: Whether to skip the confirmation prompt.
    """
    deployment, _ = _run(lambda: app.api.deployment_details(deployment_id))
    if deployment.get("finished") or str(deployment.get("status", "")).casefold() == "finished":
        _say("This deployment is already finished and cannot be cancelled.")
        return
    if confirm(f"Cancel deployment {deployment.get('name', '')} ({deployment_id})?", yes):
        _run(lambda: app.api.cancel_deployment(deployment_id))
        _say("Deployment cancellation requested.")


@cli.command("terminal")
@click.argument("device_id")
@click.pass_obj
def terminal_command(app: Context, device_id: str) -> None:
    """Open a native remote terminal.

    \f

    :param app: Shared application configuration and API client.
    :param device_id: Target device identifier.
    """
    _run(lambda: remote.terminal(app.api, device_id))


@cli.command("port-forward")
@click.argument("device_id")
@click.argument("mappings", nargs=-1, required=True)
@click.option("--bind", "bind_host", default="127.0.0.1", show_default=True)
@click.pass_obj
def port_forward_command(app: Context, device_id: str, mappings: tuple[str, ...], bind_host: str) -> None:
    """Forward local TCP or UDP ports to a device.

    \f

    :param app: Shared application configuration and API client.
    :param device_id: Target device identifier.
    :param mappings: Port forwarding specifications accepted by :func:`remote.parse_mapping`.
    :param bind_host: Local interface address for forwarded ports.
    """
    _run(lambda: remote.port_forward(app.api, device_id, bind_host, list(mappings)))


@cli.command("cp")
@click.argument("source")
@click.argument("destination")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_obj
def cp_command(app: Context, source: str, destination: str, yes: bool) -> None:
    """Copy a local file to or from DEVICE_ID:PATH.

    \f

    :param app: Shared application configuration and API client.
    :param source: Local source filename or remote source in ``DEVICE_ID:PATH`` form.
    :param destination: Local destination or remote destination in ``DEVICE_ID:PATH`` form.
    :param yes: Whether to skip the confirmation prompt.
    :raises click.ClickException: Neither endpoint is remote, both are remote, or the transfer
        fails.
    """
    source_remote = _parse_remote(source)
    destination_remote = _parse_remote(destination)
    if source_remote and destination_remote:
        raise click.ClickException("copying between two devices is not supported")
    if not source_remote and not destination_remote:
        raise click.ClickException("the source or destination must use DEVICE_ID:PATH")
    if confirm(f"Copy {source} to {destination}?", yes):
        if destination_remote:
            device_id, path = destination_remote
            _run(lambda: app.api.upload_file(device_id, Path(source), path))
            _say("File uploaded.")
        else:
            device_id, path = source_remote  # type: ignore[misc]
            result = _run(lambda: app.api.download_file(device_id, path, Path(destination)))
            _say(f"File saved to {result}.")


def _parse_remote(value: str) -> tuple[str, str] | None:
    """Split a remote file operand at its first colon.

    :param value: Candidate operand in ``DEVICE_ID:PATH`` form.
    :returns: The nonempty device ID and path, or ``None`` for a local or incomplete operand.
    """
    if ntpath.splitdrive(value)[0]:
        return None
    device, separator, path = value.partition(":")
    if "/" in device or "\\" in device:
        return None
    return (device, path) if separator and device and path else None


@cli.command("completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish", "powershell", "elvish"]))
def completion_command(shell: str) -> None:
    """Generate a shell completion script.

    \f

    :param shell: Shell for which to generate a completion script.
    """
    if shell == "elvish":
        commands = " ".join(["interactive", "login", "devices", "artifacts", "deployments",
                              "terminal", "port-forward", "cp", "completion"])
        print("set edit:completion:arg-completer[mender-richer-cli] = {|@words|\n"
              f"  put {commands}\n"
              "}")
        return
    from click.shell_completion import get_completion_class
    cls = get_completion_class(shell)
    if cls is None:
        raise click.ClickException(f"completion is not available for {shell}")
    comp = cls(cli, {}, "mender-richer-cli", "mender-richer-cli")
    print(comp.source())


def interactive(app: Context) -> None:
    """Browse accepted devices and dispatch device actions or group deployments.

    :param app: Shared application configuration and API client.
    """
    devices = _run(app.api.devices)
    favorites = Favorites()
    favorite = favorites.get(app.config.server)
    initial = next((i for i, d in enumerate(devices) if favorite and d.get("id") == favorite.get("id")), -1)
    rows = [[device_name(d), str(d.get("id", "")), device_updated(d)] for d in devices]
    if not rows:
        _say("No accepted devices were found.")
        return
    while True:
        def toggle(index: int) -> None:
            """Persist a favorite toggle for a device row.

            :param index: Original index of the device in the picker list.
            """
            device = devices[index]
            favorites.toggle(app.config.server, {"id": str(device["id"]), "name": device_name(device)})
        selected, initial, _, multi = pick("Select a device", ["Name", "Device ID", "Last contact"], rows,
                                           device_mode=True, favorite=initial, toggle_favorite=toggle)
        if not selected:
            _say("Goodbye.")
            return
        if multi:
            ids = [str(devices[i]["id"]) for i in selected]
            _interactive_deploy(app, ids)
            continue
        _device_menu(app, devices[selected[0]], devices)


def _device_menu(app: Context, device: dict[str, Any], devices: list[dict[str, Any]]) -> None:
    """Run the selected device's action menu until the user returns or quits.

    :param app: Shared application configuration and API client.
    :param device: Selected device record.
    :param devices: Device list passed by the picker; currently unused by the menu.
    :raises SystemExit: The user selects Quit.
    """
    device_id = str(device["id"])
    actions = ["Remote terminal", "TCP port forwarding", "Upload a file", "Download a file",
               "Deploy an artifact", "Show inventory", "Choose another device", "Quit"]
    while True:
        choice, _, _, _ = pick(f"{device_name(device)} ({device_id})", ["Action"], [[x] for x in actions])
        if not choice or choice[0] == 6:
            return
        if choice[0] == 7:
            raise SystemExit(0)
        try:
            if choice[0] == 0:
                remote.terminal(app.api, device_id)
            elif choice[0] == 1:
                mapping = click.prompt("Local port[:remote host:remote port]", default="8000:8000")
                remote.port_forward(app.api, device_id, "127.0.0.1", [mapping])
            elif choice[0] == 2:
                local = Path(click.prompt("Local file", type=click.Path(exists=True, dir_okay=False, path_type=Path)))
                path = click.prompt("Remote path")
                if confirm(f"Upload {local} to {device_name(device)}:{path}?", False):
                    app.api.upload_file(device_id, local, path)
            elif choice[0] == 3:
                path = click.prompt("Remote path")
                destination = Path(click.prompt("Local destination", default="."))
                if confirm(f"Download {device_name(device)}:{path} to {destination}?", False):
                    print(f"File saved to {app.api.download_file(device_id, path, destination)}.")
            elif choice[0] == 4:
                _interactive_deploy(app, [device_id])
            elif choice[0] == 5:
                inventory = app.api.inventory(device_id)
                pick(f"Inventory for {device_name(device)} ({device_id})", ["Attribute", "Value"],
                     flatten_inventory(inventory), viewer=True)
        except (RuntimeError, ValueError, OSError) as exc:
            _say(f"Error: {exc}")


def _interactive_deploy(app: Context, device_ids: list[str]) -> None:
    """Prompt for an artifact, confirm, and deploy to the selected devices.

    :param app: Shared application configuration and API client.
    :param device_ids: Identifiers of the selected deployment targets.
    """
    artifacts = _run(app.api.artifacts)
    if not artifacts:
        _say("No artifacts are available.")
        return
    order, _, _, _ = pick("Order artifacts", ["Order"], [["Name"], ["Type"]])
    if not order:
        return
    key = "type" if order[0] == 1 else "name"
    artifacts.sort(key=(lambda a: (artifact_kind(a).casefold(), a.get("name", "").casefold()))
                   if key == "type" else (lambda a: (a.get("name", "").casefold(),)))
    rows = [[a.get("name", ""), artifact_kind(a), a.get("description", "")] for a in artifacts]
    selected, _, _, _ = pick("Select an artifact", ["Name", "Type", "Description"], rows)
    if not selected:
        return
    artifact = artifacts[selected[0]]
    if confirm(f"Deploy {artifact.get('name')} to {len(device_ids)} devices?", False):
        _run(lambda: app.api.deploy(artifact["name"], device_ids))
        _say("Deployment created.")


def main() -> None:
    """Run the CLI and render expected failures without a traceback.

    :raises SystemExit: An operational or Click error determines a nonzero exit status.
    """
    try:
        cli(standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from exc
    except (RuntimeError, ValueError, OSError) as exc:
        click.ClickException(str(exc)).show()
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
