# Meshtastic Scripts

A collection of utility scripts for working with Meshtastic devices. These scripts allow you to monitor chat messages, broadcast messages with acknowledgment tracking, and interact with Meshtastic mesh networks.

## Scripts

### 1. Chat Monitor (`chat_monitor.py`)
A terminal UI for monitoring Meshtastic messages in real-time with the ability to send replies.

**Features:**
- Real-time message display with timestamps
- Interactive UI using Rich library
- Press 's' to send a message
- Press 'q' to quit
- Automatic message history with configurable limits

### 2. Broadcast Until Acked (`broadcast_until_acked.py`)
Sends messages to Meshtastic nodes and monitors for acknowledgments. Runs in a loop until an acknowledgment is received. Use sparingly to avoid flooding the network.

**Features:**
- Configurable destination (broadcast or specific node)
- Automatic retry with configurable intervals
- Packet monitoring and acknowledgment tracking
- Detailed logging of send/receive events

## Prerequisites

- Python 3.8 or higher
- A Meshtastic device connected via USB/serial
- [uv](https://github.com/astral-sh/uv) - Fast Python package installer and resolver

## Installation

### 1. Install uv (if not already installed)

On macOS/Linux:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or using Homebrew:
```bash
brew install uv
```

For other installation methods, see [uv documentation](https://github.com/astral-sh/uv).

### 2. Clone or download this repository

```bash
cd /path/to/meshtastic_scripts
```

### 3. Install dependencies with uv

```bash
uv sync
```

This will:
- Create a virtual environment (if needed)
- Install all required dependencies
- Make the scripts available for execution

## Usage

### Running with uv

The recommended way to run these scripts is using `uv run`:

#### Chat Monitor
```bash
uv run python chat_monitor.py
```

#### Broadcast Until Acked
```bash
uv run python broadcast_until_acked.py
```

### Running with the virtual environment

Alternatively, you can activate the virtual environment and run the scripts directly:

```bash
# Activate the virtual environment (uv creates it in .venv by default)
source .venv/bin/activate

# Run the scripts
python chat_monitor.py
python broadcast_until_acked.py

# When done, deactivate
deactivate
```

## Contributing

Feel free to submit issues or pull requests to improve these scripts!

## Useful Commands

```bash
# Install/sync dependencies
uv sync

# Run a script
uv run python chat_monitor.py

# Add a new dependency
uv add package-name

# Update dependencies
uv sync --upgrade

# Show installed packages
uv pip list

# Create a requirements.txt (if needed for compatibility)
uv pip freeze > requirements.txt
```
