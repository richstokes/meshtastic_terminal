"""Node detail modal screen for viewing complete node metadata in Meshtastic TUI."""

from datetime import datetime
from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.widgets import Label, Static, Link
from textual.screen import ModalScreen


class NodeDetailScreen(ModalScreen):
    """Modal screen for displaying detailed information about a specific node."""

    BINDINGS = [
        ("escape", "dismiss_dialog", "Back"),
        ("q", "dismiss_dialog", "Back"),
    ]

    def __init__(self, node_id: str, node_data: dict, is_my_node: bool = False):
        """
        Initialize the node detail screen.
        
        Args:
            node_id: The node ID
            node_data: The complete node data dictionary
            is_my_node: Whether this is the user's own node
        """
        super().__init__()
        self.node_id = node_id
        self.node_data = node_data
        self.is_my_node = is_my_node
        self.maps_url = None  # Will store Google Maps URL if location available

    def compose(self) -> ComposeResult:
        """Create the node detail dialog."""
        with Container(id="node-detail-dialog"):
            with VerticalScroll(id="node-detail-scroll"):
                yield Label("Node Details", id="node-detail-title")
                
                # Build the detailed information display (this sets self.maps_url if location exists)
                info_text = self._build_node_info()
                yield Static(info_text, id="node-detail-content", markup=False)
                
                # Add clickable link inline with position data if we have a location
                if self.maps_url:
                    yield Link("View on Google Maps", url=self.maps_url, id="maps-link")
            
            yield Static("Press ESC or Q to go back", id="node-detail-footer")

    def _build_node_info(self) -> str:
        """Build a formatted string with all available node information."""
        lines = []
        
        # Header
        user = self.node_data.get("user", {})
        long_name = user.get("longName", "Unknown")
        short_name = user.get("shortName", "")
        
        if self.is_my_node:
            lines.append(f"★ {long_name}")
        else:
            lines.append(long_name)
        
        if short_name and short_name != long_name:
            lines.append(f"Short Name: {short_name}")
        
        lines.append(f"Node ID: {self.node_id}")
        lines.append("")
        
        # Hardware Information
        lines.append("═══ HARDWARE ═══")
        hw_model = user.get("hwModel", "")
        if hw_model:
            hw_model_names = {
                0: "UNSET", 1: "TLORA_V2", 2: "TLORA_V1", 3: "TLORA_V2_1_1P6",
                4: "TBEAM", 5: "HELTEC_V2_0", 6: "TBEAM_V0P7", 7: "T_ECHO",
                8: "TLORA_V1_1P3", 9: "RAK4631", 10: "HELTEC_V2_1", 11: "HELTEC_V1",
                12: "LILYGO_TBEAM_S3_CORE", 13: "RAK11200", 14: "NANO_G1",
                15: "TLORA_V2_1_1P8", 16: "TLORA_T3_S3", 17: "NANO_G1_EXPLORER",
                18: "NANO_G2_ULTRA", 25: "STATION_G1", 26: "RAK11310",
                29: "SENSELORA_RP2040", 31: "SENSELORA_S3", 32: "CANARYONE",
                33: "RP2040_LORA", 39: "HELTEC_V3", 41: "HELTEC_WSL_V3",
                42: "BETAFPV_2400_TX", 43: "BETAFPV_900_NANO_TX", 47: "RPI_PICO",
                48: "HELTEC_WIRELESS_TRACKER", 49: "HELTEC_WIRELESS_PAPER",
                50: "T_DECK", 51: "T_WATCH_S3", 52: "PICOMPUTER_S3", 53: "HELTEC_HT62",
                61: "UNPHONE", 64: "TD_LORAC", 65: "CDEBYTE_EORA_S3", 66: "TWC_MESH_V4",
                67: "NRF52_UNKNOWN", 68: "PORTDUINO", 69: "ANDROID_SIM", 70: "DIY_V1",
                71: "NRF52840_PCA10059", 72: "DR_DEV", 73: "M5STACK", 74: "HELTEC_V2",
                75: "HELTEC_V1", 76: "LILYGO_TBEAM_V1_1", 77: "TBEAM_V1",
                78: "LILYGO_TBEAM_S3_CORE", 200: "WIO_WM1110", 201: "RAK2560",
                254: "PRIVATE_HW", 255: "UNSET",
            }
            if isinstance(hw_model, int):
                hw_model = hw_model_names.get(hw_model, f"Unknown ({hw_model})")
            elif hasattr(hw_model, "name"):
                hw_model = hw_model.name
            lines.append(f"Model: {hw_model}")
        else:
            lines.append("Model: N/A")
        
        # MAC address
        mac_addr = user.get("macaddr")
        if mac_addr:
            lines.append(f"MAC Address: {mac_addr}")
        
        lines.append("")
        
        # Role and Configuration
        lines.append("═══ CONFIGURATION ═══")
        role = user.get("role", None)
        if role is not None:
            # Role is typically a string, but handle int for backwards compatibility
            if isinstance(role, int):
                role_names = {
                    0: "CLIENT", 1: "CLIENT_MUTE", 2: "ROUTER", 3: "ROUTER_CLIENT",
                    4: "REPEATER", 5: "TRACKER", 6: "SENSOR", 7: "TAK",
                    8: "CLIENT_HIDDEN", 9: "LOST_AND_FOUND", 10: "TAK_TRACKER",
                    11: "ROUTER_LATE", 12: "CLIENT_BASE",
                }
                role = role_names.get(role, f"Unknown ({role})")
            elif hasattr(role, "name"):
                role = role.name
            # else role is already a string, use as-is
            lines.append(f"Role: {role}")
        else:
            # Default to CLIENT when role is not specified (most common default)
            lines.append("Role: CLIENT (default)")
        
        # Public key
        public_key = user.get("publicKey")
        if public_key:
            # Display abbreviated key
            if isinstance(public_key, bytes):
                key_hex = public_key.hex()
                lines.append(f"Public Key: {key_hex[:16]}...{key_hex[-16:]}")
            else:
                lines.append(f"Public Key: {str(public_key)[:32]}...")
        
        lines.append("")
        
        # Position Information
        position = self.node_data.get("position", {})
        if position:
            lines.append("═══ POSITION ═══")
            
            latitude = position.get("latitude") or position.get("latitudeI")
            longitude = position.get("longitude") or position.get("longitudeI")
            altitude = position.get("altitude")
            
            # Convert coordinates and track if we have valid lat/lon
            has_valid_location = False
            lat_float = None
            lon_float = None
            
            if latitude is not None:
                # Handle integer format (degrees * 1e-7)
                if isinstance(latitude, int) and abs(latitude) > 180:
                    latitude = latitude / 1e7
                lat_float = float(latitude)
                lines.append(f"Latitude: {latitude:.6f}°")
                has_valid_location = True
            
            if longitude is not None:
                if isinstance(longitude, int) and abs(longitude) > 180:
                    longitude = longitude / 1e7
                lon_float = float(longitude)
                lines.append(f"Longitude: {longitude:.6f}°")
            
            if altitude is not None:
                lines.append(f"Altitude: {altitude} m")
            
            precision = position.get("precisionBits")
            if precision is not None:
                lines.append(f"Precision: {precision} bits")
            
            # Time of position
            pos_time = position.get("time")
            if pos_time:
                try:
                    dt = datetime.fromtimestamp(pos_time)
                    lines.append(f"Position Time: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                except Exception:
                    pass
            
            # Store Google Maps URL if we have both lat and lon (will be rendered as clickable link)
            # Using zoom level 13 for a neighborhood/area view (ranges: 1=world, 5=continent, 10=city, 15=streets, 20=buildings)
            if has_valid_location and lat_float is not None and lon_float is not None:
                self.maps_url = f"https://www.google.com/maps/@{lat_float},{lon_float},13z"
            
            lines.append("")
        
        # Device Metrics
        device_metrics = self.node_data.get("deviceMetrics", {})
        if device_metrics:
            lines.append("═══ DEVICE METRICS ═══")
            
            battery_level = device_metrics.get("batteryLevel")
            if battery_level is not None:
                if battery_level > 100:
                    lines.append("Power: External (Powered)")
                else:
                    lines.append(f"Battery: {battery_level}%")
            
            voltage = device_metrics.get("voltage")
            if voltage is not None:
                lines.append(f"Voltage: {voltage:.2f} V")
            
            channel_util = device_metrics.get("channelUtilization")
            if channel_util is not None:
                lines.append(f"Channel Utilization: {channel_util:.1f}%")
            
            air_util_tx = device_metrics.get("airUtilTx")
            if air_util_tx is not None:
                lines.append(f"Air Utilization (TX): {air_util_tx:.1f}%")
            
            uptime = device_metrics.get("uptimeSeconds")
            if uptime is not None:
                # Format uptime
                days = uptime // 86400
                hours = (uptime % 86400) // 3600
                minutes = (uptime % 3600) // 60
                if days > 0:
                    lines.append(f"Uptime: {days}d {hours}h {minutes}m")
                elif hours > 0:
                    lines.append(f"Uptime: {hours}h {minutes}m")
                else:
                    lines.append(f"Uptime: {minutes}m")
            
            lines.append("")
        
        # Connection Quality
        lines.append("═══ CONNECTION ═══")
        
        snr = self.node_data.get("snr")
        if snr is not None:
            lines.append(f"SNR: {snr:.1f} dB")
        
        rssi = self.node_data.get("rssi")
        if rssi is not None:
            lines.append(f"RSSI: {rssi} dBm")
        
        last_heard = self.node_data.get("lastHeard")
        if last_heard:
            try:
                dt = datetime.fromtimestamp(last_heard)
                now = datetime.now()
                delta = now - dt
                
                # Absolute time
                lines.append(f"Last Heard: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                
                # Relative time
                if delta.total_seconds() < 60:
                    time_str = "just now"
                elif delta.total_seconds() < 3600:
                    mins = int(delta.total_seconds() / 60)
                    time_str = f"{mins} minute{'s' if mins != 1 else ''} ago"
                elif delta.total_seconds() < 86400:
                    hours = int(delta.total_seconds() / 3600)
                    time_str = f"{hours} hour{'s' if hours != 1 else ''} ago"
                else:
                    days = int(delta.total_seconds() / 86400)
                    time_str = f"{days} day{'s' if days != 1 else ''} ago"
                lines.append(f"({time_str})")
            except Exception:
                lines.append("Last Heard: Unknown")
        else:
            lines.append("Last Heard: Never")
        
        hops_away = self.node_data.get("hopsAway")
        if hops_away is not None:
            lines.append(f"Hops Away: {hops_away}")
        
        lines.append("")
        
        # Additional metadata
        lines.append("═══ METADATA ═══")
        
        num = self.node_data.get("num")
        if num is not None:
            lines.append(f"Node Number: {num}")
        
        via_mqtt = self.node_data.get("viaMqtt")
        if via_mqtt is not None:
            lines.append(f"Via MQTT: {via_mqtt}")
        
        return "\n".join(lines)

    def action_dismiss_dialog(self) -> None:
        """Dismiss the dialog and return to node list."""
        self.dismiss()
