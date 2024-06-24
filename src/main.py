# --------------------------------------------------
# mender-richer-cli - A richer CLI for Mender
# Quentin Dufournet, 2024
# --------------------------------------------------
# Built-in
import requests
from rich import print as rprint
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
        ],
    }

    response = requests.post(
        f'{server}/api/management/v2/inventory/filters/search',
        headers={'Authorization': f'Bearer {token}'},
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
    """

    rprint('[#7289DA]' + '='*81 + '\n' +
           '================================[bold][#E01E5A] Devices list [/bold][/#E01E5A]===================================')
    filtered_devices = []
    id = 0
    for device in devices:
        id += 1
        if 'timestamp' in device['attributes'][0]:
            device_name = device['attributes'][0]['value']
        elif 'timestamp' in device['attributes'][1]:
            device_name = device['attributes'][1]['value']
        elif 'timestamp' in device['attributes'][2]:
            device_name = device['attributes'][2]['value']
        else:
            device_name = 'Unknown'
        device_id = device['id']
        filtered_devices.append(
            {
                'local_id': id,
                'name': device_name,
                'device_id': device_id,
                'name_size': len(device_name)
            }
        )
        lenght_of_string = len(str(id)) + len(device_name) + len(device_id)
        space_to_add = 69 - lenght_of_string
        if space_to_add < 0:
            device_id = device_id[:-space_to_add*-1] + '...'
            space_to_add = 1
        rprint(
            f'[#7289DA]|  [#E01E5A][bold]{id}[/#E01E5A] - {device_name}[/bold] - [italic][#65656b]({
                device_id})[/italic][/#65656b]' + ' ' * space_to_add + '|'
        )


def print_device_choice():
    """ Print the device choice and ask the user to choose one

    :return: The device number
    """

    rprint('[#7289DA]' + '='*81 + '\n' +
           'Enter the device number you want to interact with: ')
    device_number = int(input())

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

    return command


def print_port_forward():
    """ Print the port forward choices, and ask the user to choose the
    local and remote ports

    :return: The local and remote ports"""

    rprint('[#7289DA]' + '='*81 + '\n' +
           'Enter the remote port you want to forward: ')
    remote = int(input())
    rprint('[#7289DA]Enter the local port you want to forward to: ')
    local = int(input())

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

    args = parse_args()
    check_mender_cli()
    print_welcome()
    devices = get_devices_list(args.server, args.token)

    while 1:
        print_devices_list(devices)
        device = print_device_choice()
        command = print_command()
        try:
            if command == 1:
                sp.run(
                    [
                        'mender-cli',
                        'terminal',
                        devices[device-1]['id'],
                        '--token-value',
                        args.token,
                        '--server',
                        args.server,
                        '-k'
                    ]
                )
            elif command == 2:
                local, remote = print_port_forward()
                sp.run(
                    [
                        'mender-cli',
                        'port-forward',
                        devices[device-1]['id'],
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
            pass


if __name__ == '__main__':
    main()
