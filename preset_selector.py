"""Preset selector modal screen for Meshtastic TUI."""

from textual.app import ComposeResult
from textual.containers import Container, Grid
from textual.widgets import Button, Label
from textual.screen import ModalScreen

# Radio presets mapping
RADIO_PRESETS = {
    "LONG_FAST": 0,
    "LONG_SLOW": 1,
    "VERY_LONG_SLOW": 2,
    "MEDIUM_SLOW": 3,
    "MEDIUM_FAST": 4,
    "SHORT_SLOW": 5,
    "SHORT_FAST": 6,
    "LONG_MODERATE": 7,
}


class PresetSelectorScreen(ModalScreen):
    """Modal screen for selecting radio presets."""

    BINDINGS = [
        ("escape", "dismiss_dialog", "Cancel"),
        ("enter", "select_button", "Select"),
        ("up", "focus_previous", "Up"),
        ("down", "focus_next", "Down"),
        ("left", "focus_previous", "Left"),
        ("right", "focus_next", "Right"),
    ]

    def __init__(self, current_preset: str = None):
        super().__init__()
        self.current_preset = current_preset
        self.button_list = []

    def compose(self) -> ComposeResult:
        """Create the preset selector dialog."""
        with Container(id="preset-dialog"):
            current_text = (
                f" (Current: {self.current_preset})" if self.current_preset else ""
            )
            yield Label(f"Select Radio Preset{current_text}", id="preset-title")
            with Grid(id="preset-buttons") as grid:
                for preset_name in RADIO_PRESETS.keys():
                    button = Button(preset_name, id=f"preset-{preset_name}")
                    button.can_focus = True
                    yield button

    def on_mount(self) -> None:
        """Focus first button when mounted."""
        # Store button list for navigation
        self.button_list = list(self.query(Button))
        if self.button_list:
            # Focus the first button
            self.set_focus(self.button_list[0])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        preset_name = event.button.id.replace("preset-", "")
        self.dismiss(preset_name)

    def action_select_button(self) -> None:
        """Select the currently focused button."""
        focused = self.focused
        if isinstance(focused, Button):
            preset_name = focused.id.replace("preset-", "")
            self.dismiss(preset_name)

    def action_focus_previous(self) -> None:
        """Move focus to the previous button."""
        if not self.button_list:
            return

        focused = self.focused
        if focused in self.button_list:
            current_index = self.button_list.index(focused)
            # Move to previous button (wrap around to end if at start)
            previous_index = (current_index - 1) % len(self.button_list)
            self.set_focus(self.button_list[previous_index])

    def action_focus_next(self) -> None:
        """Move focus to the next button."""
        if not self.button_list:
            return

        focused = self.focused
        if focused in self.button_list:
            current_index = self.button_list.index(focused)
            # Move to next button (wrap around to start if at end)
            next_index = (current_index + 1) % len(self.button_list)
            self.set_focus(self.button_list[next_index])

    def action_dismiss_dialog(self) -> None:
        """Dismiss dialog without selecting."""
        self.dismiss(None)
