"""Serial port selector modal screen for Meshtastic TUI."""

import serial.tools.list_ports
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Button, Label, ListItem, ListView
from textual.screen import ModalScreen
from textual import on, events


class SerialPortSelectorScreen(ModalScreen):
    """Modal screen for selecting a serial port to connect to."""

    BINDINGS = [
        ("escape", "dismiss_dialog", "Cancel"),
        ("enter", "select_port", "Select"),
        ("up", "focus_previous", "Up"),
        ("down", "focus_next", "Down"),
    ]

    def __init__(self):
        """Initialize the serial port selector."""
        super().__init__()
        self.port_list = []  # List of port device paths
        self.port_info = {}  # Map device path to full port info

    def compose(self) -> ComposeResult:
        """Create the serial port selector dialog."""
        with Container(id="port-selector-dialog"):
            yield Label("Select serial port:", id="port-selector-title")
            
            # Enumerate available serial ports
            ports = serial.tools.list_ports.comports()
            
            # Filter out typical non-device ports
            filtered_ports = [
                p for p in ports 
                if not any(skip in p.device.lower() for skip in ['bluetooth', 'debug'])
            ]
            
            # Store port information
            for port in filtered_ports:
                self.port_list.append(port.device)
                self.port_info[port.device] = port
            
            # If no ports available, show message
            if not self.port_list:
                yield Label("No serial ports detected", id="no-ports-message")
                yield Button("Cancel", id="cancel-button", variant="default")
            else:
                # Create scrollable list view
                list_view = ListView(id="port-list")
                list_view.can_focus = True
                yield list_view
                
                # Add option to use auto-detection (None)
                yield Button("Auto-detect (None)", id="auto-button", variant="default")

    def on_mount(self) -> None:
        """Populate the list and focus it when mounted."""
        if self.port_list:
            list_view = self.query_one("#port-list", ListView)
            # Add all list items
            for device_path in self.port_list:
                port_info = self.port_info[device_path]
                
                # Create informative label
                if port_info.description and port_info.description != 'n/a':
                    label_text = f"{device_path} - {port_info.description}"
                elif port_info.manufacturer:
                    label_text = f"{device_path} - {port_info.manufacturer}"
                else:
                    label_text = device_path
                
                list_view.append(ListItem(Label(label_text), id=f"port_{self.port_list.index(device_path)}"))
            
            # Focus the list view
            self.set_focus(list_view)

    @on(ListView.Selected)
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle port selection from ListView."""
        if event.item and event.item.id:
            # Extract index from ID
            port_index = int(event.item.id.replace("port_", ""))
            selected_port = self.port_list[port_index]
            self.dismiss(selected_port)

    @on(Button.Pressed, "#auto-button")
    def on_auto_button_pressed(self) -> None:
        """Handle auto-detect button press."""
        self.dismiss(None)

    @on(Button.Pressed, "#cancel-button")
    def on_cancel_button_pressed(self) -> None:
        """Handle cancel button press."""
        self.dismiss(False)

    def action_select_port(self) -> None:
        """Select the currently highlighted port (for Enter key binding)."""
        if not self.port_list:
            self.dismiss(False)
            return
            
        list_view = self.query_one("#port-list", ListView)
        if list_view.highlighted_child:
            port_index = int(list_view.highlighted_child.id.replace("port_", ""))
            selected_port = self.port_list[port_index]
            self.dismiss(selected_port)

    def action_dismiss_dialog(self) -> None:
        """Dismiss dialog without selecting."""
        self.dismiss(False)

    def action_focus_previous(self) -> None:
        """Move selection up in the list."""
        if self.port_list:
            list_view = self.query_one("#port-list", ListView)
            list_view.action_cursor_up()

    def action_focus_next(self) -> None:
        """Move selection down in the list."""
        if self.port_list:
            list_view = self.query_one("#port-list", ListView)
            list_view.action_cursor_down()
