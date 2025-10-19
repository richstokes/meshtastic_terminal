"""Frequency slot selector modal screen for Meshtastic TUI."""

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Button, Input, Label
from textual.screen import ModalScreen


class FrequencySlotSelectorScreen(ModalScreen):
    """Modal screen for selecting frequency slot."""

    BINDINGS = [
        ("escape", "dismiss_dialog", "Cancel"),
        ("enter", "select_button", "Select"),
        ("up", "focus_previous", "Up"),
        ("down", "focus_next", "Down"),
        ("left", "focus_previous", "Left"),
        ("right", "focus_next", "Right"),
    ]

    def __init__(self, current_slot: int = None):
        super().__init__()
        self.current_slot = current_slot
        self.button_list = []

    def compose(self) -> ComposeResult:
        """Create the frequency slot selector dialog."""
        with Container(id="frequency-dialog"):
            current_text = (
                f" (Current: {self.current_slot})"
                if self.current_slot is not None
                else ""
            )
            yield Label(f"Select Frequency Slot{current_text}", id="frequency-title")
            yield Label(
                "Valid slots: 0-83 (depends on region)", id="frequency-subtitle"
            )
            with Container(id="frequency-input-container"):
                yield Input(
                    placeholder="Enter slot number (0-83)",
                    id="frequency-input",
                    type="integer",
                )
                with Container(id="frequency-buttons"):
                    yield Button("Set Slot", id="set-slot-button", variant="primary")
                    yield Button("Cancel", id="cancel-slot-button")

    def on_mount(self) -> None:
        """Focus input when mounted."""
        freq_input = self.query_one("#frequency-input", Input)
        self.set_focus(freq_input)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press."""
        if event.button.id == "set-slot-button":
            freq_input = self.query_one("#frequency-input", Input)
            if freq_input.value:
                try:
                    slot = int(freq_input.value)
                    if 0 <= slot <= 83:
                        self.dismiss(slot)
                    else:
                        # Could add error message here
                        pass
                except ValueError:
                    pass
        elif event.button.id == "cancel-slot-button":
            self.dismiss(None)

    def action_select_button(self) -> None:
        """Handle enter key - same as clicking Set Slot button."""
        freq_input = self.query_one("#frequency-input", Input)
        if freq_input.value:
            try:
                slot = int(freq_input.value)
                if 0 <= slot <= 83:
                    self.dismiss(slot)
            except ValueError:
                pass

    def action_focus_previous(self) -> None:
        """Move focus to the previous element."""
        self.focus_previous()

    def action_focus_next(self) -> None:
        """Move focus to the next element."""
        self.focus_next()

    def action_dismiss_dialog(self) -> None:
        """Dismiss dialog without selecting."""
        self.dismiss(None)
