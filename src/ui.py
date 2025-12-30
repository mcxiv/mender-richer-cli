# --------------------------------------------------
# mender-richer-cli - UI Components
# Quentin Dufournet, 2024
# --------------------------------------------------

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.text import Text
from rich.box import ROUNDED
import datetime

# Export classes and constants for use in other modules
__all__ = ['UI', 'Colors', 'Table', 'ROUNDED', 'console']

console = Console()


# Color Scheme
class Colors:
    """Color palette for Mender Richer CLI"""
    PRIMARY = "#2E86AB"      # blue (primary brand color)
    SECONDARY = "#A23B72"    # purple (secondary brand color)
    SUCCESS = "#06A77D"      # Success green
    WARNING = "#F18805"      # Warning orange
    ERROR = "#C73E1D"        # Error red
    INFO = "#3A8FB7"         # Info blue
    TEXT_PRIMARY = "#E0E0E0"  # Primary text (light)
    TEXT_SECONDARY = "#B0B0B0"  # Secondary text (medium)
    TEXT_TERTIARY = "#808080"  # Tertiary text (dark)
    BORDER = "#3A8FB7"       # Border color
    BACKGROUND = "#1E1E1E"   # Background color


class UI:
    """UI components for Mender Richer CLI"""

    @staticmethod
    def header(title: str, subtitle: str = ""):
        """Display the header"""
        header_text = Text()
        header_text.append(f"{title}\n", style=f"bold {Colors.PRIMARY}")
        if subtitle:
            header_text.append(
                f"{subtitle}", style=f"italic {Colors.TEXT_SECONDARY}")

        console.print(Panel(header_text,
                            title="[bold]Mender Richer CLI[/bold]",
                            title_align="left",
                            border_style=Colors.BORDER,
                            padding=(1, 2)))

    @staticmethod
    def section_header(title: str):
        """Display a section header"""
        console.print(
            f"\n[bold {Colors.SECONDARY}]{title}[/bold {Colors.SECONDARY}]")
        console.print(
            f"[{Colors.BORDER}]{"=" * (len(title) + 4)}[/{Colors.BORDER}]")

    @staticmethod
    def info_message(message: str):
        """Display an informational message"""
        console.print(f"[✓] [{Colors.INFO}]{message}[/{Colors.INFO}]")

    @staticmethod
    def success_message(message: str):
        """Display a success message"""
        console.print(f"[✓] [{Colors.SUCCESS}]{message}[/{Colors.SUCCESS}]")

    @staticmethod
    def warning_message(message: str):
        """Display a warning message"""
        console.print(f"[!] [{Colors.WARNING}]{message}[/{Colors.WARNING}]")

    @staticmethod
    def error_message(message: str):
        """Display an error message"""
        console.print(f"[✗] [{Colors.ERROR}]{message}[/{Colors.ERROR}]")

    @staticmethod
    def progress_spinner(message: str = "Processing..."):
        """Display a progress spinner"""
        with Progress(
            SpinnerColumn(style=Colors.PRIMARY),
            TextColumn("[progress.description]{task.description}"),
            transient=True
        ) as progress:
            progress.add_task(description=message, total=None)
            yield

    @staticmethod
    def device_table(devices: list):
        """Display devices in a table format"""
        table = Table(
            title="\nAvailable Devices",
            title_style=f"bold {Colors.PRIMARY}",
            border_style=Colors.BORDER,
            box=ROUNDED,
            show_header=True,
            header_style=f"bold {Colors.SECONDARY}"
        )

        # Calculate dynamic width for Name column based on actual device names
        # Start with minimum width and expand as needed
        name_width = min(max(len(device['name']) for device in devices) + 2, 80) if devices else 30
        
        # Add columns with proper styling and optimized widths
        table.add_column("ID", style=f"bold {Colors.SECONDARY}", width=6, no_wrap=True)
        table.add_column("Name", style=Colors.TEXT_PRIMARY, width=name_width, no_wrap=True, overflow="fold")  # Dynamic width based on actual names
        table.add_column("Device ID", style=Colors.TEXT_SECONDARY, width=36, no_wrap=True)
        table.add_column("Last Polling", style=f"italic {Colors.TEXT_TERTIARY}", width=20, no_wrap=True)

        for device in devices:
            try:
                # Parse and format the polling timestamp
                polling = datetime.datetime.strptime(
                    device['polling'], '%Y-%m-%dT%H:%M:%S.%fZ'
                ) if device['polling'] else datetime.datetime.now()
                polling = polling + datetime.timedelta(hours=2)
                polling_str = polling.strftime('%Y-%m-%d %H:%M:%S')
            except:
                polling_str = "Unknown"

            # Add row - styling is applied at column level
            table.add_row(
                str(device['local_id']),
                device['name'],
                device['device_id'],
                polling_str
            )

        from rich import print as rprint
        rprint(table)

    @staticmethod
    def command_menu():
        """Display the command menu"""
        menu_table = Table(
            title="\nAvailable Commands",
            title_style=f"bold {Colors.PRIMARY}",
            border_style=Colors.BORDER,
            box=ROUNDED,
            show_header=False
        )

        menu_table.add_column(
            "Option", style=f"bold {Colors.SECONDARY}", width=10)
        menu_table.add_column(
            "Command", style=f"bold {Colors.TEXT_PRIMARY}", width=20)
        menu_table.add_column("Description", style=Colors.TEXT_SECONDARY)

        commands = [
            ("1", "terminal", "Open a reverse shell on the device"),
            ("2", "port-forward", "Forward a port from the device to your machine"),
            ("3", "upload-file", "Upload a file to the device"),
            ("4", "download-file", "Download a file from the device"),
            ("5", "deploy-artifact", "Deploy an artifact to the device"),
            ("6", "inventory", "View full device inventory details")
        ]

        for option, command, description in commands:
            menu_table.add_row(option, command, description)

        console.print(menu_table)

    @staticmethod
    def input_prompt(prompt: str, default: str = ""):
        """Display an input prompt"""
        if default:
            prompt_text = f"[{Colors.TEXT_PRIMARY}]{prompt} [{Colors.TEXT_SECONDARY}](default: {default})[/{Colors.TEXT_SECONDARY}]: [/{Colors.TEXT_PRIMARY}]"
        else:
            prompt_text = f"[{Colors.TEXT_PRIMARY}]{prompt}: [/{Colors.TEXT_PRIMARY}]"

        return console.input(prompt_text)

    @staticmethod
    def confirmation_prompt(prompt: str):
        """Display a confirmation prompt"""
        while True:
            response = UI.input_prompt(f"{prompt} (y/n)").lower()
            if response in ['y', 'yes']:
                return True
            elif response in ['n', 'no']:
                return False
            else:
                UI.warning_message("Please enter 'y' or 'n'")

    @staticmethod
    def welcome_screen():
        """Display the welcome screen"""
        welcome_text = Text()
        welcome_text.append("🚀 Mender Richer CLI\n",
                            style=f"bold {Colors.PRIMARY}")
        welcome_text.append("Mender CLI, but a little bit richer\n",
                            style=f"italic {Colors.TEXT_SECONDARY}")
        welcome_text.append("v2.0", style=f"{Colors.TEXT_TERTIARY}")

        console.print(Panel(welcome_text,
                            border_style=Colors.BORDER,
                            padding=(2, 3),
                            expand=False))

    @staticmethod
    def status_bar(message: str):
        """Display a status bar message"""
        console.print(f"\n[{Colors.BORDER}] {message}[/{Colors.BORDER}]")

    @staticmethod
    def error_panel(message: str, details: str = ""):
        """Display an error in a panel"""
        error_text = Text()
        error_text.append(f"❌ {message}\n", style=f"bold {Colors.ERROR}")
        if details:
            error_text.append(
                f"Details: {details}", style=Colors.TEXT_SECONDARY)

        console.print(Panel(error_text,
                            title="[bold]Error[/bold]",
                            title_align="left",
                            border_style=Colors.ERROR,
                            padding=(1, 2)))
