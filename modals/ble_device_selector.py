"""BLE device selector modal screen for Meshtastic TUI."""

import asyncio
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Button, Label, ListItem, ListView, Static
from textual.screen import ModalScreen
from textual import on
from bleak import BleakScanner


class BleDeviceSelectorScreen(ModalScreen):
    """Modal screen for scanning and selecting a BLE device to connect to."""

    BINDINGS = [
        ("escape", "dismiss_dialog", "Cancel"),
        ("enter", "select_device", "Select"),
        ("up", "focus_previous", "Up"),
        ("down", "focus_next", "Down"),
        ("r", "rescan", "Rescan"),
    ]

    def __init__(self):
        """Initialize the BLE device selector."""
        super().__init__()
        self.device_list = []  # List of BLE devices
        self.device_info = {}  # Map address to device info
        self.is_scanning = False

    def compose(self) -> ComposeResult:
        """Create the BLE device selector dialog."""
        with Container(id="ble-selector-dialog"):
            yield Label("Scanning for BLE devices...", id="ble-selector-title")
            yield Static("Press 'r' to rescan", id="ble-help-text")
            
            # Create scrollable list view
            list_view = ListView(id="ble-list")
            list_view.can_focus = True
            yield list_view
            
            # Add cancel button
            yield Button("Cancel", id="cancel-button", variant="default")

    def on_mount(self) -> None:
        """Start BLE scan when mounted."""
        self.run_worker(self.scan_ble_devices(), exclusive=False)

    async def scan_ble_devices(self) -> None:
        """Scan for BLE devices."""
        if self.is_scanning:
            return
            
        self.is_scanning = True
        title = self.query_one("#ble-selector-title", Label)
        title.update("Scanning for BLE devices...")
        
        # Clear previous results
        list_view = self.query_one("#ble-list", ListView)
        list_view.clear()
        self.device_list = []
        self.device_info = {}
        
        try:
            # Scan for 10 seconds
            devices = await BleakScanner.discover(timeout=10.0)
            
            # Filter out devices without names and collect valid ones
            for device in devices:
                # Only include devices with a name
                if device.address and device.name and device.name.strip():
                    self.device_list.append(device)
                    self.device_info[device.address] = device
            
            # Sort by signal strength (RSSI) - strongest first (higher RSSI = better signal)
            self.device_list.sort(key=lambda d: getattr(d, 'rssi', -999), reverse=True)
            
            # Update title with results
            if self.device_list:
                title.update(f"Found {len(self.device_list)} BLE device(s)")
                
                # Populate list
                for idx, device in enumerate(self.device_list):
                    # Create informative label
                    if device.name and device.name.strip():
                        label_text = f"{device.name} ({device.address})"
                    else:
                        label_text = f"Unknown Device ({device.address})"
                    
                    # Show signal strength if available
                    if hasattr(device, 'rssi') and device.rssi:
                        label_text += f" [RSSI: {device.rssi}]"
                    
                    list_view.append(ListItem(Label(label_text), id=f"ble_{idx}"))
                
                # Focus the list view
                self.set_focus(list_view)
            else:
                title.update("No BLE devices found")
                list_view.append(ListItem(Label("No devices detected. Press 'r' to rescan.")))
        
        except Exception as e:
            title.update(f"Scan error: {e}")
        
        finally:
            self.is_scanning = False

    @on(ListView.Selected)
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle device selection from ListView."""
        if event.item and event.item.id and event.item.id.startswith("ble_"):
            # Extract index from ID
            device_index = int(event.item.id.replace("ble_", ""))
            if device_index < len(self.device_list):
                selected_device = self.device_list[device_index]
                self.dismiss(selected_device.address)

    @on(Button.Pressed, "#cancel-button")
    def on_cancel_button_pressed(self) -> None:
        """Handle cancel button press."""
        self.dismiss(False)

    def action_select_device(self) -> None:
        """Select the currently highlighted device (for Enter key binding)."""
        if not self.device_list:
            self.dismiss(False)
            return
            
        list_view = self.query_one("#ble-list", ListView)
        if list_view.highlighted_child and list_view.highlighted_child.id:
            if list_view.highlighted_child.id.startswith("ble_"):
                device_index = int(list_view.highlighted_child.id.replace("ble_", ""))
                if device_index < len(self.device_list):
                    selected_device = self.device_list[device_index]
                    self.dismiss(selected_device.address)

    def action_dismiss_dialog(self) -> None:
        """Dismiss dialog without selecting."""
        self.dismiss(False)

    def action_focus_previous(self) -> None:
        """Move selection up in the list."""
        if self.device_list:
            list_view = self.query_one("#ble-list", ListView)
            list_view.action_cursor_up()

    def action_focus_next(self) -> None:
        """Move selection down in the list."""
        if self.device_list:
            list_view = self.query_one("#ble-list", ListView)
            list_view.action_cursor_down()

    def action_rescan(self) -> None:
        """Rescan for BLE devices."""
        if not self.is_scanning:
            self.run_worker(self.scan_ble_devices(), exclusive=False)
