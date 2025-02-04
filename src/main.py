# --------------------------------------------------
# mender-richer-cli - A richer CLI for Mender
# Quentin Dufournet, 2024
# --------------------------------------------------
# Built-in
import requests
from rich import print as rprint
import datetime
import argparse
import sys
import subprocess as sp
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 3rd party


def parse_args():
    """ Parse the CLI arguments

    :return: The parsed arguments
    """

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

    return parser.parse_args()


def get_devices_list(server, token):
    """ Get the list of devices from the Mender server

    :param token: The Mender API token
    :return: The list of devices
    """

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

    response = requests.post(
        f'{server}/api/management/v2/inventory/filters/search',
        headers={
            'Authorization': f'Bearer {token}',
        },
        json=json_data,
        verify=False
    )

    if response.status_code != 200:
        rprint('[bold][red]Error[/bold]: Unable to get the list of devices[/red]')
        sys.exit(1)

    return response.json()


def print_welcome():
    """ Print the welcome message """

    rprint('[#7289DA]' + '='*81 + '\n' +
           '|                [bold][#E01E5A]mender-richer-cli[/bold][/#E01E5A] - A richer CLI for mender-cli                |')


def print_devices_list(devices):
    """ Print the list of devices

    :param devices: The list of devices
    :return: The filtered devices
    """

    rprint('[#7289DA]' + '='*81 + '\n' +
           '================================[bold][#E01E5A] Devices list [/bold][/#E01E5A]===================================')
    filtered_devices = []
    for device in devices:
        device_name = 'Unknown'
        for attribute in device['attributes']:
            if 'timestamp' in attribute:
                device_name = attribute['value']
                break
        device_polling = 'Unknown'
        for attribute in device['attributes']:
            if 'updated_ts' in str(attribute):
                device_polling = attribute['value']
                break
        filtered_devices.append(
            {
                'name': device_name,
                'device_id': device['id'],
                'polling': device_polling
            }
        )

    # Sort devices by name
    filtered_devices.sort(key=lambda x: x['name'].lower())

    # Print the devices
    id = 0
    for device in filtered_devices:
        id += 1
        device['local_id'] = id
        device_name = device['name']
        device_id = device['device_id']
        try:
            polling = datetime.datetime.strptime(
                device['polling'], '%Y-%m-%dT%H:%M:%S.%fZ')
            polling = polling + datetime.timedelta(hours=2)
            polling = polling.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            polling = datetime.datetime.strptime(
                device['polling'], '%Y-%m-%dT%H:%M:%SZ')
            polling = polling + datetime.timedelta(hours=2)
            polling = polling.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            polling = 'Unknown'

        rprint(
            f'[#7289DA]|  [#E01E5A][bold]{id}[/#E01E5A] - {device_name}[/bold] - [italic][#65656b]({
                device_id} - {polling})[/italic][/#65656b]'
        )

    return filtered_devices


def print_device_choice(devices):
    """ Print the device choice and ask the user to choose one

    :return: The device number
    """

    rprint('[#7289DA]' + '='*81 + '\n' +
           'Enter the device number you want to interact with: ')
    device_number = int(input())
    if device_number not in [device['local_id'] for device in devices]:
        rprint('[bold][red]Error[/bold]: Invalid device number')
        sys.exit(1)

    return device_number


def print_command():
    """ Print the command choice and ask the user to choose one

    :return: The command number
    """

    rprint('[#7289DA]' + '='*81 + '\n' +
           'Enter the command you want to run: ')
    rprint('  [#7289DA][#E01E5A]1[/#E01E5A] - [bold]terminal[/bold] - ' +
           '[italic][#65656b]Open a reverse shell on the device[/italic][/#65656b]')
    rprint('  [#7289DA][#E01E5A]2[/#E01E5A] - [bold]port-forward[/bold] - ' +
           '[italic][#65656b]Forward a port from the device to your machine[/italic][/#65656b]')
    command = int(input())
    if command not in [1, 2]:
        rprint('[bold][red]Error[/bold]: Invalid command')
        sys.exit(1)

    return command


def print_port_forward():
    """ Print the port forward choices, and ask the user to choose the
    local and remote ip:port to forward.

    :return: The local and remote ports"""

    rprint('[#7289DA]' + '='*81 + '\n' +
           'Enter the remote ip:port you want to forward: ')
    remote = input()
    try:
        remote_int = int(remote)
        if remote_int > 65535:
            rprint('[bold][red]Error[/bold]: Invalid port number')
            sys.exit(1)
    except ValueError:
        remote_address = remote.split(':')
        if len(remote_address) == 2:
            if int(remote_address[1]) > 65535:
                rprint('[bold][red]Error[/bold]: Invalid port number')
                sys.exit(1)
            if len(remote_address[0].split('.')) != 4:
                rprint('[bold][red]Error[/bold]: Invalid IP address')
                sys.exit(1)

    rprint('[#7289DA]Enter the local ip:port you want to forward to: ')
    local = input()
    try:
        if int(remote) > 65535:
            rprint('[bold][red]Error[/bold]: Invalid port number')
            sys.exit(1)
    except ValueError:
        remote_address_port = remote.split(':')
        if len(remote_address_port) == 2:
            if int(remote_address_port[1]) > 65535:
                rprint('[bold][red]Error[/bold]: Invalid port number')
                sys.exit(1)
            if len(remote_address_port[0].split('.')) != 4:
                rprint('[bold][red]Error[/bold]: Invalid IP address')
                sys.exit(1)

    return local, remote


def check_mender_cli():
    """ Check if the mender-cli is installed """

    output = sp.getoutput('mender-cli --version')
    if 'mender-cli version' not in output:
        rprint('[bold][red]Error[/bold]: mender-cli is not installed. ' +
               'Head to https://github.com/mendersoftware/mender-cli to install it![/red]')
        sys.exit(1)


def main():
    """ Main function """

    # Parse the arguments (server URL and API token)
    args = parse_args()

    # Check if the mender-cli is installed
    check_mender_cli()

    # Print the welcome message
    print_welcome()

    # Main loop to interact with the devices
    try:
        while 1:
            # Get the list of devices
            devices = get_devices_list(args.server, args.token)

            # Print the devices list
            filtered_devices = print_devices_list(devices)

            # Ask the user to choose a device
            device = print_device_choice(filtered_devices)

            # Ask the user to choose a command
            command = print_command()

            while 1:
                # Run the reverse shell command
                if command == 1:
                    rprint('[#7289DA]' + '='*81)
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
                    break

                # Run the port forward command
                elif command == 2:
                    try:
                        local, remote = print_port_forward()
                    except KeyboardInterrupt:
                        # So the user can go back to the device list, without quitting
                        # the program
                        break
                    rprint('[#7289DA]' + '='*81)
                    try:
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
                    except KeyboardInterrupt:
                        # So the user can go back to the device list, without quitting
                        # the program
                        break
    except KeyboardInterrupt:
        rprint('[bold][red]\nGoodbye![/bold][/red]')
        sys.exit(0)


if __name__ == '__main__':
    main()
