# mender-richer-cli

This is just Mender from your terminal, but *richer*.

Manage devices, artifacts, and deployments from an interactive terminal UI or the command line.

Originally a wrapper around `mender-cli`, it now talks directly to Mender's HTTP and WebSocket APIs. You no longer need to install `mender-cli`.

## Installation

Requires Python 3.10 or newer. Supports Linux and Windows 10/11. On Windows,
installation includes `windows-curses` for the interactive picker.

With [pipx](https://pipx.pypa.io/):

```bash
git clone https://github.com/mcxiv/mender-richer-cli.git
cd mender-richer-cli
pipx install .
```

Or, from the repository, install into a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

On Windows, use PowerShell:

```powershell
git clone https://github.com/mcxiv/mender-richer-cli.git
cd mender-richer-cli
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\mrc.exe --help
```

You can also use `pipx install .` on Windows to make `mrc` available outside the
virtual environment. Run the interactive UI and remote shell in a console such
as Windows Terminal or PowerShell.

## Usage

Pass your Mender server URL and API token:

```bash
mrc "$SERVER_MENDER_CLOUD" "$TOKEN_MENDER_CLOUD"
```

In PowerShell, environment variables use the `$env:` prefix:

```powershell
mrc "$env:SERVER_MENDER_CLOUD" "$env:TOKEN_MENDER_CLOUD"
```

This opens the interactive device picker. You can also run commands directly:

```bash
mrc "$SERVER_MENDER_CLOUD" "$TOKEN_MENDER_CLOUD" devices list --raw
mrc --help
mrc devices --help
```

You can also use named options:

```bash
mrc --server https://hosted.mender.io --token-value "$MENDER_API_TOKEN"
```

Global options go before the command, or after the positional server and token:

```bash
mrc "$SERVER_MENDER_CLOUD" "$TOKEN_MENDER_CLOUD" -v devices list
```

Use `-v` / `--verbose` to log request methods and URLs. For development servers, `-k` / `--skip-verify` disables TLS certificate verification.

## Features

- **Terminal UI** — Searchable lists with keyboard navigation.
- **Device management** — Browse devices, inspect inventory, and save a favorite.
- **Artifact management** — List, sort, upload, download, and delete artifacts.
- **Deployments** — Deploy to one or several devices, filter deployments, watch progress, and cancel them.
- **Terminal access** — Open a remote terminal on a connected device. Press Ctrl+] to exit.
- **Port forwarding** — Forward TCP and UDP ports from devices to your machine.
- **File transfer** — Upload files to devices and download them to your machine.
- **Authentication** — API tokens, login prompts, two-factor authentication, and private token storage.
- **Scripting** — JSON output and shell completion for Bash, Zsh, Fish, PowerShell, and Elvish.

Remote terminal and forwarding require a connected device and server-side support.

## Keyboard controls

| Key | Action |
| --- | --- |
| Arrows, Page Up / Page Down | Move through the list |
| Type | Filter results |
| Enter | Select an item |
| Space | Mark devices for a group deployment |
| Shift+F | Toggle the favorite device |
| Esc | Go back |

## Authentication and configuration

Log in to save a token for later commands:

```bash
mrc --server https://hosted.mender.io login --username you@example.com
mrc
```

For a different server, keep passing `--server` or set `MENDER_SERVER` so later commands use that server too.

You can also set credentials through environment variables:

```bash
export MENDER_SERVER="https://hosted.mender.io"
export MENDER_TOKEN="$MENDER_API_TOKEN"
mrc devices list --raw
```

Tokens can come from the positional token, `--token-value`, `MENDER_TOKEN`, a token file passed with `--token`, or the saved token. The app also reads `MENDER_USERNAME` and `MENDER_PASSWORD`.

Configuration files are checked in order: `./.mender-richer-clirc`, `./.mender-clirc`, `$HOME/.mender-richer-clirc`, `$HOME/.mender-clirc`, `/etc/mender-richer-cli/.mender-richer-clirc`, and `/etc/mender/.mender-clirc`.

Saved tokens are stored in `$XDG_CACHE_HOME/mender/authtoken`, falling back to `$HOME/.cache/mender/authtoken`. Favorites are stored in `$XDG_CONFIG_HOME/mender-richer-cli/favorites.json`, falling back to `$HOME/.config/mender-richer-cli/favorites.json`.

On Windows, the defaults are `%LOCALAPPDATA%\mender\authtoken` and
`%APPDATA%\mender-richer-cli\favorites.json`. XDG variables still take precedence
when set. System configuration is read from
`%PROGRAMDATA%\mender-richer-cli\.mender-richer-clirc` instead of `/etc`.
Linux token and favorites files use mode `0600`; Windows files inherit access
permissions from their parent directory in your user profile.

## Examples

With authentication configured as above:

```bash
mrc                                      # interactive device picker
mrc devices list --raw
mrc devices inventory DEVICE_ID
mrc artifacts list --sort type
mrc artifacts upload update.mender --description "Release 2.4"
mrc artifacts deploy release-2.4 DEVICE_ID_1 DEVICE_ID_2 --yes
mrc deployments list --status inprogress --search release
mrc deployments watch DEPLOYMENT_ID
mrc terminal DEVICE_ID
mrc port-forward DEVICE_ID 8080:80 udp/5353:53
mrc cp ./config.toml DEVICE_ID:/etc/my-app/config.toml --yes
mrc cp DEVICE_ID:/var/log/my-app.log . --yes
mrc completion zsh
```

Run `mrc COMMAND --help` for command options.

Windows drive-letter and UNC paths work as local file operands. Remote paths
still use `DEVICE_ID:PATH`. Files uploaded from Windows use mode `0644` on the
device; Linux uploads retain their local permissions.

```powershell
mrc cp 'C:\Users\you\config.toml' DEVICE_ID:/etc/my-app/config.toml --yes
mrc cp DEVICE_ID:/var/log/my-app.log 'C:\Users\you\Downloads' --yes
```

## Development

Run the regression suite on either platform:

```bash
python -m unittest discover -s tests -v
```

To check native Windows console input and the interactive picker, run this in
a Windows console. It queues test key events and exits automatically:

```powershell
python tests/windows_console_smoke.py
```

## Screenshots

### Devices

![Devices listing](./docs/devices.png)

### Filtering

![Devices listing with filter](./docs/filter.png)

### Device commands

![Commands listing](./docs/commands.png)

### Inventory

![Inventory listing](./docs/inventory.png)

### Artifacts

![Artifacts listing](./docs/artifact.png)

## License

Apache-2.0. See [LICENSE](LICENSE).
