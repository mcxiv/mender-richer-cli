# --------------------------------------------------
# mender-richer-cli - A richer CLI for Mender
# Quentin Dufournet, 2024
# --------------------------------------------------
# Built-in
from src.ui import UI, Colors, Table, ROUNDED, console
import requests
import datetime
import argparse
import sys
import subprocess as sp
import urllib3
import logging
import os

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Local imports


def parse_args():
    """ Parse the CLI arguments

    :return: The parsed arguments
    """
    logger.debug("Parsing CLI arguments")

    parser = argparse.ArgumentParser(
        description='A richer CLI for Mender'
    )

    parser.add_argument(
        'server',
        type=str,
        help='Mender server URL'
    )

    parser.add_argument(
        'token',
        type=str,
        help='Mender API token'
    )

    parser.add_argument(
        '--insecure',
        action='store_true',
        help='Disable SSL certificate verification (not recommended for production)',
        default=False
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging',
        default=False
    )

    args = parser.parse_args()
    logger.debug(
        f"Parsed arguments: server={args.server}, token_length={len(args.token) if args.token else 0}, insecure={args.insecure}, debug={args.debug}")
    return args


def get_devices_list(server, token):
    """ Get the list of devices from the Mender server

    :param token: The Mender API token
    :return: The list of devices
    """
    logger.debug(f"Getting devices list from server: {server}")
    logger.debug(f"Using token with length: {len(token) if token else 0}")

    json_data = {
        'page': 1,
        'per_page': 999999999,
        'filters': [
            {
                'scope': 'identity',
                'attribute': 'status',
                'type': '$eq',
                'value': 'accepted',
            },
        ],
        'attributes': [
            {
                'scope': 'identity',
                'attribute': 'status',
            },
            {
                'scope': 'inventory',
                'attribute': 'artifact_name',
            },
            {
                'scope': 'tags',
                'attribute': 'name',
            },
            {
                'attribute': 'id',
                'scope': 'identity',
            },
            {
                'attribute': 'updated_ts',
                'scope': 'system',
            }
        ],
    }

    logger.debug(f"API request payload: {json_data}")
    logger.debug(
        f"Making POST request to {server}/api/management/v2/inventory/filters/search")

    response = requests.post(
        f'{server}/api/management/v2/inventory/filters/search',
        headers={
            'Authorization': f'Bearer {token}',
        },
        json=json_data,
        verify=False
    )

    logger.debug(f"API response status: {response.status_code}")
    logger.debug(f"API response headers: {dict(response.headers)}")

    if response.status_code != 200:
        logger.debug(f"API request failed with response: {response.text}")
        UI.error_message("Unable to get the list of devices")
        sys.exit(1)

    devices = response.json()
    logger.debug(f"Retrieved {len(devices)} devices from API")
    return devices


def print_welcome():
    """ Print the welcome message """
    UI.welcome_screen()


def print_devices_list(devices):
    """ Print the list of devices

    :param devices: The list of devices
    :return: The filtered devices
    """
    logger.debug(f"Processing {len(devices)} devices for display")

    filtered_devices = []
    for device in devices:
        logger.debug(f"Processing device: {device['id']}")
        device_name = 'Unknown'
        for attribute in device['attributes']:
            if 'timestamp' in attribute:
                device_name = attribute['value']
                logger.debug(f"Found device name: {device_name}")
                break
        device_polling = 'Unknown'
        for attribute in device['attributes']:
            if 'updated_ts' in str(attribute):
                device_polling = attribute['value']
                logger.debug(f"Found polling timestamp: {device_polling}")
                break
        filtered_devices.append(
            {
                'name': device_name,
                'device_id': device['id'],
                'polling': device_polling
            }
        )

    # Sort devices by name
    logger.debug(f"Sorting {len(filtered_devices)} devices by name")
    filtered_devices.sort(key=lambda x: x['name'].lower())

    # Add local IDs
    for i, device in enumerate(filtered_devices, 1):
        device['local_id'] = i

    # Display devices in table
    UI.device_table(filtered_devices)

    logger.debug(f"Finished displaying {len(filtered_devices)} devices")
    return filtered_devices


def print_device_choice(devices):
    """ Print the device choice and ask the user to choose one

    :return: The device number
    """
    logger.debug(
        f"Waiting for user to select device from {len(devices)} options")
    logger.debug(
        f"Available device IDs: {[device['local_id'] for device in devices]}")

    while True:
        device_input = UI.input_prompt(
            "Enter the device number you want to interact with")
        try:
            device_number = int(device_input)
            logger.debug(f"User selected device number: {device_number}")

            if device_number not in [device['local_id'] for device in devices]:
                logger.debug(
                    f"Invalid device number selected: {device_number}")
                UI.error_message(
                    f"Invalid device number. Please choose from {', '.join(str(d['local_id']) for d in devices)}")
                continue

            logger.debug(f"Valid device selection: {device_number}")
            return device_number
        except ValueError as e:
            logger.debug(f"Invalid input for device selection: {e}")
            UI.error_message("Please enter a valid number")


def get_file_path(prompt, is_local=True, is_download=False):
    """ Get a file path from user input with validation

    :param prompt: The prompt to display
    :param is_local: Whether this is a local file path
    :param is_download: Whether this is for a download operation
    :return: Valid file path
    """
    logger.debug(
        f"Getting file path with prompt: {prompt}, is_local={is_local}, is_download={is_download}")

    while True:
        file_path = UI.input_prompt(prompt).strip()
        logger.debug(f"User entered file path: '{file_path}'")

        if not file_path:
            logger.debug("Empty file path entered")
            UI.error_message("File path cannot be empty")
            continue

        # For upload operations, check if local file exists
        if is_local and not is_download and not os.path.exists(file_path):
            logger.debug(f"File does not exist: {file_path}")
            UI.error_message(f"File {file_path} does not exist")
            continue

        # For download operations, check if local path is a directory
        if is_local and is_download:
            logger.debug(f"Processing download path: {file_path}")
            if os.path.isdir(file_path):
                logger.debug(
                    f"Path is directory, will use as destination: {file_path}")
                # If it's a directory, we'll use it as the destination directory
                # and keep the original filename
                return file_path
            elif os.path.exists(file_path):
                logger.debug(
                    f"File exists, asking for overwrite confirmation: {file_path}")
                # If file exists, ask for confirmation to overwrite
                if UI.confirmation_prompt(f'File {file_path} already exists. Overwrite?'):
                    return file_path
                else:
                    continue
            else:
                logger.debug(
                    f"File doesn't exist, checking parent directory: {file_path}")
                # If file doesn't exist, check if parent directory exists
                parent_dir = os.path.dirname(file_path)
                if parent_dir and not os.path.exists(parent_dir):
                    logger.debug(
                        f"Parent directory does not exist: {parent_dir}")
                    UI.error_message(
                        f"Parent directory {parent_dir} does not exist")
                    continue

        logger.debug(f"Valid file path accepted: {file_path}")
        return file_path


def get_artifact_name(prompt):
    """ Get an artifact name from user input

    :param prompt: The prompt to display
    :return: Artifact name
    """
    logger.debug(f"Getting artifact name with prompt: {prompt}")

    while True:
        artifact_name = UI.input_prompt(prompt).strip()
        logger.debug(f"User entered artifact name: '{artifact_name}'")

        if not artifact_name:
            logger.debug("Empty artifact name entered")
            UI.error_message("Artifact name cannot be empty")
            continue

        logger.debug(f"Valid artifact name accepted: {artifact_name}")
        return artifact_name


def upload_file_to_device(server, token, device_id, local_path, remote_path, args):
    """ Upload a file to a device using mender-cli cp command

    :param server: Mender server URL
    :param token: Mender API token
    :param device_id: Device ID
    :param local_path: Local file path
    :param remote_path: Remote file path on device
    :param args: Command line arguments for SSL configuration
    """
    logger.debug(
        f"Starting upload_file_to_device: server={server}, device_id={device_id}, local_path={local_path}, remote_path={remote_path}")

    # Check if file exists
    if not os.path.exists(local_path):
        logger.debug(f"File does not exist: {local_path}")
        UI.error_message(f"File {local_path} does not exist")
        return False

    logger.debug(
        f"Uploading file {local_path} to {remote_path} on device {device_id} using mender-cli cp")

    # Use mender-cli cp command for file upload
    UI.info_message(
        f"Initiating file upload from {local_path} to {device_id}:{remote_path}")

    # Confirm operation
    if not UI.confirmation_prompt('Proceed with file upload?'):
        logger.debug("File upload cancelled by user")
        UI.info_message("File upload cancelled")
        return False

    try:
        logger.debug("Executing mender-cli cp command for file upload")
        # Execute mender-cli cp command
        result = sp.run(
            [
                'mender-cli',
                'cp',
                local_path,
                f'{device_id}:{remote_path}',
                '--token-value',
                token,
                '--server',
                server,
                '-k'
            ],
            capture_output=True,
            text=True
        )

        logger.debug(
            f"mender-cli cp command completed with return code: {result.returncode}")
        logger.debug(f"Command stdout: {result.stdout}")
        logger.debug(f"Command stderr: {result.stderr}")

        if result.returncode == 0:
            UI.success_message(
                f"File uploaded successfully from {local_path} to {device_id}:{remote_path}")
            logger.debug(
                f"File upload successful: {local_path} -> {device_id}:{remote_path}")
            return True
        else:
            UI.error_message("File upload failed")
            if result.stderr:
                UI.error_message(f"Error details: {result.stderr.strip()}")
            logger.debug(f"File upload failed: {result.stderr}")
            return False

    except Exception as e:
        logger.debug(f"Exception during file upload: {e}")
        UI.error_message(f"File upload failed: {e}")
        logger.debug(f"File upload failed: {e}")
        return False


def download_file_from_device(server, token, device_id, remote_path, local_path, args):
    """ Download a file from a device using mender-cli cp command

    :param server: Mender server URL
    :param token: Mender API token
    :param device_id: Device ID
    :param remote_path: Remote file path on device
    :param local_path: Local file path to save to
    :param args: Command line arguments for SSL configuration
    """
    logger.debug(
        f"Starting download_file_from_device: server={server}, device_id={device_id}, remote_path={remote_path}, local_path={local_path}")
    logger.debug(
        f"Downloading file {remote_path} from device {device_id} to {local_path} using mender-cli cp")

    # Use mender-cli cp command for file download
    UI.info_message(
        f"Initiating file download from {device_id}:{remote_path} to {local_path}")

    # Confirm operation
    if not UI.confirmation_prompt('Proceed with file download?'):
        logger.debug("File download cancelled by user")
        UI.info_message("File download cancelled")
        return False

    try:
        logger.debug("Executing mender-cli cp command for file download")
        # Execute mender-cli cp command
        result = sp.run(
            [
                'mender-cli',
                'cp',
                f'{device_id}:{remote_path}',
                local_path,
                '--token-value',
                token,
                '--server',
                server,
                '-k'
            ],
            capture_output=True,
            text=True
        )

        logger.debug(
            f"mender-cli cp command completed with return code: {result.returncode}")
        logger.debug(f"Command stdout: {result.stdout}")
        logger.debug(f"Command stderr: {result.stderr}")

        if result.returncode == 0:
            UI.success_message(
                f"File downloaded successfully from {device_id}:{remote_path} to {local_path}")
            logger.debug(
                f"File download successful: {device_id}:{remote_path} -> {local_path}")
            return True
        else:
            UI.error_message("File download failed")
            if result.stderr:
                UI.error_message(f"Error details: {result.stderr.strip()}")
            logger.debug(f"File download failed: {result.stderr}")
            return False

    except Exception as e:
        logger.debug(f"Exception during file download: {e}")
        UI.error_message(f"File download failed: {e}")
        logger.debug(f"File download failed: {e}")
        return False


def get_available_artifacts(server, token, args):
    """ Get list of available artifacts from Mender server

    :param server: Mender server URL
    :param token: Mender API token
    :param args: Command line arguments for SSL configuration
    :return: List of artifacts or None if failed
    """
    logger.debug(f"Getting available artifacts from server: {server}")

    try:
        verify_ssl = not args.insecure if hasattr(args, 'insecure') else True
        logger.debug(f"SSL verification: {verify_ssl}")

        # Get list of available artifacts
        logger.debug(
            f"Making GET request to {server}/api/management/v1/deployments/artifacts")
        response = requests.get(
            f'{server}/api/management/v1/deployments/artifacts',
            headers={
                'Authorization': f'Bearer {token}',
            },
            verify=verify_ssl,
            timeout=30
        )

        logger.debug(f"API response status: {response.status_code}")
        logger.debug(f"API response headers: {dict(response.headers)}")

        if response.status_code != 200:
            logger.debug(
                f"Failed to get artifacts list: {response.status_code} - {response.text}")
            UI.error_message(
                f"Failed to get artifacts list. Status: {response.status_code}")
            return None

        artifacts = response.json()
        logger.debug(f"Retrieved {len(artifacts)} artifacts from API")
        logger.debug(f"Available artifacts: {artifacts}")
        return artifacts

    except requests.exceptions.RequestException as e:
        logger.debug(f"Failed to get artifacts list: {e}")
        UI.error_message(f"Failed to get artifacts list: {e}")
        return None


def print_artifact_list(artifacts):
    """ Print the list of available artifacts

    :param artifacts: List of artifacts
    :return: Selected artifact name or None
    """
    logger.debug(f"Displaying {len(artifacts)} artifacts for user selection")

    if not artifacts:
        logger.debug("No artifacts available")
        UI.error_message("No artifacts available")
        return None

    artifact_list = []
    for i, artifact in enumerate(artifacts, 1):
        artifact_name = artifact.get('name', 'Unknown')
        artifact_id = artifact.get('id', 'Unknown')
        artifact_description = artifact.get('description', 'No description')

        logger.debug(
            f"Processing artifact {i}: {artifact_name} ({artifact_id})")

        artifact_list.append({
            'id': i,
            'name': artifact_name,
            'artifact_id': artifact_id,
            'description': artifact_description
        })

        # Create an artifact table
        artifact_table = Table(
            title=f"Artifact {i}: {artifact_name}",
            title_style=f"bold {Colors.PRIMARY}",
            border_style=Colors.BORDER,
            box=ROUNDED,
            show_header=False
        )

        artifact_table.add_column(
            "Property", style=f"bold {Colors.SECONDARY}", width=20)
        artifact_table.add_column("Value", style=Colors.TEXT_PRIMARY)

        artifact_table.add_row("ID", artifact_id)
        artifact_table.add_row("Name", artifact_name)
        artifact_table.add_row("Description", artifact_description)

        console.print(artifact_table)
        console.print()  # Add some spacing

    while True:
        choice_input = UI.input_prompt(
            "Enter the artifact number you want to deploy (or 0 to cancel)")
        try:
            choice = int(choice_input)
            logger.debug(f"User selected artifact number: {choice}")
            if choice == 0:
                logger.debug("User cancelled artifact selection")
                return None
            if choice < 1 or choice > len(artifact_list):
                logger.debug(f"Invalid artifact number selected: {choice}")
                UI.error_message(
                    f"Invalid artifact number. Please choose from 1-{len(artifact_list)}")
                continue

            selected_artifact = artifact_list[choice - 1]
            logger.debug(
                f"Selected artifact: {selected_artifact['name']} ({selected_artifact['artifact_id']})")
            return selected_artifact['name']

        except ValueError as e:
            logger.debug(f"Invalid input for artifact selection: {e}")
            UI.error_message("Please enter a valid number")


def deploy_artifact_to_device(server, token, device_id, artifact_name, args):
    """ Deploy an artifact to a device using Mender API

    :param server: Mender server URL
    :param token: Mender API token
    :param device_id: Device ID
    :param artifact_name: Artifact name to deploy
    :param args: Command line arguments for SSL configuration
    """
    logger.debug(
        f"Starting deploy_artifact_to_device: server={server}, device_id={device_id}, artifact_name={artifact_name}")
    logger.debug(
        f"Attempting to deploy artifact {artifact_name} to device {device_id}")

    try:
        verify_ssl = not args.insecure if hasattr(args, 'insecure') else True
        logger.debug(f"SSL verification: {verify_ssl}")

        # Create deployment
        deployment_data = {
            'name': f'Deployment of {artifact_name} to {device_id}',
            'artifact_name': artifact_name,
            'devices': [device_id]
        }

        logger.debug(f"Deployment data: {deployment_data}")
        logger.debug(
            f"Making POST request to {server}/api/management/v1/deployments/deployments")

        response = requests.post(
            f'{server}/api/management/v1/deployments/deployments',
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            },
            json=deployment_data,
            verify=verify_ssl,
            timeout=30
        )

        logger.debug(f"API response status: {response.status_code}")
        logger.debug(f"API response headers: {dict(response.headers)}")
        logger.debug(f"API response body: {response.text}")

        if response.status_code != 201:
            logger.debug(
                f"Failed to create deployment: {response.status_code} - {response.text}")
            UI.error_message(
                f"Failed to create deployment. Status: {response.status_code}")
            if response.status_code == 404:
                UI.warning_message(
                    "Artifact may not exist or device may not be compatible")
            return False

        UI.success_message(
            f"Deployment created successfully: {artifact_name} to {device_id}")
        logger.debug(
            f"Deployment successful for artifact {artifact_name} to device {device_id}")
        return True

    except requests.exceptions.RequestException as e:
        logger.debug(f"Deployment failed: {e}")
        UI.error_message(f"Deployment failed: {e}")
        return False


def get_device_inventory(server, token, device_id):
    """ Get the full inventory details for a specific device

    :param server: Mender server URL
    :param token: Mender API token
    :param device_id: Device ID to get inventory for
    :return: Device inventory data or None if failed
    """

    logger.debug(
        f"Getting inventory for device {device_id} from server: {server}")

    try:
        logger.debug(
            f"Making GET request to {server}/api/management/v1/inventory/devices/{device_id}")

        response = requests.get(
            f'{server}/api/management/v1/inventory/devices/{device_id}',
            headers={
                'Authorization': f'Bearer {token}',
            },
            verify=False
        )

        logger.debug(f"API response status: {response.status_code}")
        logger.debug(f"API response headers: {dict(response.headers)}")

        if response.status_code != 200:
            logger.debug(
                f"Failed to get device inventory: {response.status_code} - {response.text}")
            UI.error_message(
                f"Failed to get device inventory. Status: {response.status_code}")
            return None

        inventory_data = response.json()
        logger.debug(f"Retrieved inventory data for device {device_id}")
        logger.debug(
            f"Inventory data structure: {list(inventory_data.keys())}")
        return inventory_data

    except requests.exceptions.RequestException as e:
        logger.debug(f"Failed to get device inventory: {e}")
        UI.error_message(f"Failed to get device inventory: {e}")
        return None


def print_device_inventory(inventory_data, device_name, device_id):
    """ Print the device inventory in a nice table format

    :param inventory_data: The inventory data from the API
    :param device_name: The device name for display
    :param device_id: The device ID for display
    """
    logger.debug(
        f"Displaying inventory for device {device_name} ({device_id})")

    if not inventory_data:
        logger.debug("No inventory data available")
        UI.error_message("No inventory data available")
        return

    # Create main inventory table
    inventory_table = Table(
        title=f"Device Inventory: {device_name} - {device_id}",
        title_style=f"bold {Colors.PRIMARY}",
        border_style=Colors.BORDER,
        box=ROUNDED,
        show_header=True,
        header_style=f"bold {Colors.SECONDARY}"
    )

    inventory_table.add_column(
        "Scope", style=f"bold {Colors.SECONDARY}", width=15)
    inventory_table.add_column(
        "Attribute", style=f"bold {Colors.SECONDARY}", width=25)
    inventory_table.add_column("Value", style=Colors.TEXT_PRIMARY)

    # Process each scope in the inventory data
    for _, attributes in inventory_data.items():
        logger.debug(f"Processing attributes: {len(attributes)} attributes")
        if isinstance(attributes, list):
            # Handle list format
            for attribute in attributes:
                attr_name = attribute.get(
                    'name', attribute.get('attribute', 'unknown'))
                attr_value = attribute.get(
                    'value', attribute.get('data', 'N/A'))
                attr_scope = attribute.get(
                    'scope', attribute.get('scope', 'unknown'))
                inventory_table.add_row(attr_scope, attr_name, str(attr_value))

    # Display the inventory table
    console.print(inventory_table)


def print_command():
    """ Print the command choice and ask the user to choose one

    :return: The command number
    """
    logger.debug("Displaying command menu and waiting for user selection")

    UI.command_menu()

    while True:
        command_input = UI.input_prompt("Enter the command you want to run")
        try:
            command = int(command_input)
            logger.debug(f"User selected command: {command}")
            if command not in [1, 2, 3, 4, 5, 6]:
                logger.debug(f"Invalid command selected: {command}")
                UI.error_message("Invalid command. Please choose from 1-6")
                continue

            logger.debug(f"Valid command selected: {command}")
            return command
        except ValueError as e:
            logger.debug(f"Invalid input for command selection: {e}")
            UI.error_message("Please enter a valid number")


def print_port_forward():
    """ Print the port forward choices, and ask the user to choose the
    local and remote ip:port to forward.

    :return: The local and remote ports"""
    logger.debug("Getting port forwarding configuration from user")

    def validate_port_input(input_str, input_type):
        """Validate port input which can be just a port number or ip:port"""
        if ':' in input_str:
            # ip:port format
            parts = input_str.split(':')
            if len(parts) != 2:
                return None, f"Invalid {input_type} format. Use 'port' or 'ip:port'"

            ip_part, port_part = parts

            # Validate IP address (basic validation)
            if not ip_part:
                return None, f"IP address cannot be empty in {input_type}"

            # Validate port
            try:
                port = int(port_part)
                if port < 1 or port > 65535:
                    return None, f"Invalid port number in {input_type}. Please enter a value between 1 and 65535"
                return input_str, None
            except ValueError:
                return None, f"Invalid port number in {input_type}. Please enter a valid number"
        else:
            # Just port number
            try:
                port = int(input_str)
                if port < 1 or port > 65535:
                    return None, f"Invalid port number in {input_type}. Please enter a value between 1 and 65535"
                return input_str, None
            except ValueError:
                return None, f"Invalid port number in {input_type}. Please enter a valid number"

    while True:
        remote_input = UI.input_prompt(
            "Enter the remote port you want to forward (format: port or ip:port)")
        remote_validated, error = validate_port_input(
            remote_input, "remote port")
        if remote_validated is not None:
            logger.debug(f"User entered remote port: {remote_validated}")
            remote = remote_validated
            break
        else:
            logger.debug(f"Invalid remote port input: {error}")
            UI.error_message(error)
            continue

    while True:
        local_input = UI.input_prompt(
            "Enter the local port you want to forward to (format: port or ip:port)")
        local_validated, error = validate_port_input(local_input, "local port")
        if local_validated is not None:
            logger.debug(f"User entered local port: {local_validated}")
            local = local_validated
            break
        else:
            logger.debug(f"Invalid local port input: {error}")
            UI.error_message(error)
            continue

    logger.debug(f"Port forwarding configuration: {local}:{remote}")
    return local, remote


def check_mender_cli():
    """ Check if the mender-cli is installed """
    logger.debug("Checking if mender-cli is installed")

    output = sp.getoutput('mender-cli --version')
    logger.debug(f"mender-cli version check output: {output}")
    if 'mender-cli version' not in output:
        logger.debug("mender-cli not found or invalid version")
        UI.error_message(
            "mender-cli is not installed. Head to https://github.com/mendersoftware/mender-cli to install it!")
        sys.exit(1)

    logger.debug("mender-cli is properly installed")
    UI.success_message("mender-cli is properly installed")


def main():
    """ Main function """
    logger.debug("Starting main function")

    # Parse the arguments (server URL and API token)
    args = parse_args()

    # Configure logging level
    if hasattr(args, 'debug') and args.debug:
        logger.setLevel(logging.DEBUG)
        logger.debug("Debug logging enabled")
    else:
        logger.debug("Debug logging disabled")

    # Print the welcome message
    logger.debug("Displaying welcome message")
    print_welcome()

    # Check if the mender-cli is installed
    logger.debug("Checking mender-cli installation")
    check_mender_cli()

    # Main loop to interact with the devices
    try:
        logger.debug("Entering main device interaction loop")
        while 1:
            logger.debug("Starting new device interaction cycle")
            # Get the list of devices
            logger.debug("Fetching devices list from server")
            devices = get_devices_list(args.server, args.token)

            # Print the devices list
            logger.debug("Displaying devices list to user")
            filtered_devices = print_devices_list(devices)

            # Ask the user to choose a device
            logger.debug("Waiting for user to select device")
            device = print_device_choice(filtered_devices)
            logger.debug(f"User selected device: {device}")

            # Ask the user to choose a command
            logger.debug("Displaying command menu")
            command = print_command()
            logger.debug(f"User selected command: {command}")

            while 1:
                logger.debug(
                    f"Executing command {command} for device {device}")
                # Run the reverse shell command
                if command == 1:
                    logger.debug("Starting terminal session")
                    sp.run(
                        [
                            'mender-cli',
                            'terminal',
                            filtered_devices[device-1]['device_id'],
                            '--token-value',
                            args.token,
                            '--server',
                            args.server,
                            '-k'
                        ]
                    )
                    logger.debug("Terminal session completed")
                    break

                # Run the port forward command
                elif command == 2:
                    logger.debug("Setting up port forwarding")
                    try:
                        local, remote = print_port_forward()
                        logger.debug(
                            f"Port forwarding configuration: {local}:{remote}")
                    except KeyboardInterrupt:
                        logger.debug(
                            "Port forwarding setup interrupted by user")
                        # So the user can go back to the device list, without quitting
                        # the program
                        break
                    try:
                        logger.debug("Starting port forwarding session")
                        sp.run(
                            [
                                'mender-cli',
                                'port-forward',
                                filtered_devices[device-1]['device_id'],
                                f'{local}:{remote}',
                                '--token-value',
                                args.token,
                                '--server',
                                args.server,
                                '-k',
                            ]
                        )
                        logger.debug("Port forwarding session completed")
                    except KeyboardInterrupt:
                        logger.debug(
                            "Port forwarding session interrupted by user")
                        # So the user can go back to the device list, without quitting
                        # the program
                        break

                # Run file upload command
                elif command == 3:
                    logger.debug("Starting file upload process")
                    device_name = filtered_devices[device-1]['name']
                    device_id = filtered_devices[device-1]['device_id']
                    logger.debug(
                        f"Uploading to device: {device_name} ({device_id})")

                    # Get local and remote file paths
                    logger.debug("Getting file paths from user")
                    local_path = get_file_path(
                        'Enter local file path to upload: ')
                    remote_path = get_file_path(
                        'Enter remote file path on device: ', is_local=False)

                    # Confirm operation
                    UI.info_message(
                        f"Confirm file upload to {device_name} ({device_id})")
                    UI.info_message(f"Local: {local_path}")
                    UI.info_message(f"Remote: {remote_path}")
                    if not UI.confirmation_prompt('Proceed with file upload?'):
                        logger.debug("File upload cancelled by user")
                        UI.info_message("File upload cancelled")
                        continue

                    # Execute upload
                    logger.debug("Executing file upload")
                    success = upload_file_to_device(
                        args.server,
                        args.token,
                        device_id,
                        local_path,
                        remote_path,
                        args
                    )

                    if success:
                        logger.debug("File upload completed successfully")
                        UI.success_message("File upload operation completed")
                    else:
                        logger.debug("File upload failed")
                        UI.error_message("File upload failed")

                    break

                # Run file download command
                elif command == 4:
                    logger.debug("Starting file download process")
                    device_name = filtered_devices[device-1]['name']
                    device_id = filtered_devices[device-1]['device_id']
                    logger.debug(
                        f"Downloading from device: {device_name} ({device_id})")

                    # Get remote and local file paths
                    logger.debug("Getting file paths from user")
                    remote_path = get_file_path(
                        'Enter remote file path on device: ', is_local=False)
                    local_path = get_file_path(
                        'Enter local file path to save to: ', is_local=True, is_download=True)

                    # Confirm operation
                    UI.info_message(
                        f"Confirm file download from {device_name} ({device_id})")
                    UI.info_message(f"Remote: {remote_path}")
                    UI.info_message(f"Local: {local_path}")
                    if not UI.confirmation_prompt('Proceed with file download?'):
                        logger.debug("File download cancelled by user")
                        UI.info_message("File download cancelled")
                        continue

                    # Execute download
                    logger.debug("Executing file download")
                    success = download_file_from_device(
                        args.server,
                        args.token,
                        device_id,
                        remote_path,
                        local_path,
                        args
                    )

                    if success:
                        logger.debug("File download completed successfully")
                        UI.success_message("File download operation completed")
                    else:
                        logger.debug("File download failed")
                        UI.error_message("File download failed")

                    break

                # Run artifact deployment command
                elif command == 5:
                    logger.debug("Starting artifact deployment process")
                    device_name = filtered_devices[device-1]['name']
                    device_id = filtered_devices[device-1]['device_id']
                    logger.debug(
                        f"Deploying to device: {device_name} ({device_id})")

                    # Get available artifacts
                    logger.debug("Fetching available artifacts")
                    artifacts = get_available_artifacts(
                        args.server, args.token, args)
                    if artifacts is None:
                        logger.debug("Failed to get artifacts list")
                        UI.error_message("Cannot proceed with deployment")
                        continue

                    # Show artifact list and let user select
                    logger.debug("Displaying artifact list for user selection")
                    artifact_name = print_artifact_list(artifacts)
                    if artifact_name is None:
                        logger.debug("Artifact deployment cancelled by user")
                        UI.info_message("Artifact deployment cancelled")
                        continue

                    # Confirm operation
                    UI.info_message(
                        f"Confirm artifact deployment to {device_name} ({device_id})")
                    UI.info_message(f"Artifact: {artifact_name}")
                    UI.info_message(f"Server: {args.server}")
                    if not UI.confirmation_prompt('Proceed with artifact deployment?'):
                        logger.debug("Artifact deployment cancelled by user")
                        UI.info_message("Artifact deployment cancelled")
                        continue

                    # Execute deployment
                    logger.debug("Executing artifact deployment")
                    success = deploy_artifact_to_device(
                        args.server,
                        args.token,
                        device_id,
                        artifact_name,
                        args
                    )

                    if success:
                        logger.debug(
                            "Artifact deployment initiated successfully")
                        UI.success_message(
                            f"Artifact deployment initiated: {artifact_name} to {device_name}")
                    else:
                        logger.debug("Artifact deployment failed")
                        UI.error_message("Artifact deployment failed")

                    break

                # Run inventory command
                elif command == 6:
                    logger.debug("Starting inventory display process")
                    device_name = filtered_devices[device-1]['name']
                    device_id = filtered_devices[device-1]['device_id']
                    logger.debug(
                        f"Displaying inventory for device: {device_name} ({device_id})")

                    # Get device inventory
                    logger.debug("Fetching device inventory data")
                    inventory_data = get_device_inventory(
                        args.server, args.token, device_id)
                    if inventory_data is None:
                        logger.debug("Failed to get inventory data")
                        UI.error_message("Cannot display inventory")
                        continue

                    # Display inventory
                    logger.debug("Displaying inventory data")
                    print_device_inventory(
                        inventory_data, device_name, device_id)

                    break
    except KeyboardInterrupt:
        logger.debug("Main loop interrupted by user")
        UI.info_message("\nGoodbye!")
        sys.exit(0)
    except Exception as e:
        logger.debug(f"Unexpected error in main loop: {e}")
        UI.error_message(f"An unexpected error occurred: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
