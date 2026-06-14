# Installation

VoiceUse can be installed in several ways depending on your needs. The full voice assistant requires audio, STT, TTS, and LLM dependencies. The lightweight MCP server only needs core OS control libraries.

## Quick Install

=== "pipx (Recommended)"

    ```bash
    # Full voice assistant
    pipx install "voice-computer-use-agent[all]"

    # MCP server only
    pipx install voice-computer-use-agent
    ```

=== "uv"

    ```bash
    # Full voice assistant
    uv tool install "voice-computer-use-agent[all]"

    # MCP server only
    uv tool install voice-computer-use-agent
    ```

=== "pip"

    ```bash
    # Full voice assistant
    pip install "voice-computer-use-agent[all]"

    # MCP server only
    pip install voice-computer-use-agent
    ```

## Development Install

Clone the repository and install in editable mode:

```bash
git clone https://github.com/jarmen423/voice-computer-use-agent.git
cd voice-computer-use-agent

# Create virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"
```

## Per-OS Setup

### Windows (Primary)

Windows is the primary supported platform with full feature parity.

**Prerequisites:**

- Python 3.10+ from [python.org](https://python.org)
- `pywin32` is installed automatically via pip
- **ffmpeg** for TTS playback (optional but recommended)

**If `pyaudio` fails to install:**

```powershell
pip install pipwin
pipwin install pyaudio
```

**Install ffmpeg:**

1. Download from [ffmpeg.org](https://ffmpeg.org/download.html)
2. Add `bin` folder to your PATH
3. Verify: `ffmpeg -version`

### Linux

=== "Debian / Ubuntu"

    ```bash
    sudo apt-get update
    sudo apt-get install -y \
        python3-pyaudio portaudio19-dev \
        python3-xlib xdotool wmctrl ffmpeg
    ```

=== "Arch"

    ```bash
    sudo pacman -S python-pyaudio portaudio xdotool wmctrl ffmpeg
    ```

!!! warning "Wayland Support"
    `xdotool` may not work on Wayland. Switch to X11 or use XWayland for full compatibility.

### macOS (Best-Effort)

```bash
brew install portaudio ffmpeg
```

- Window management uses AppleScript and Quartz APIs
- `afplay` is used as a TTS playback fallback

## PyInstaller Binaries

Standalone executables are available from [GitHub Releases](https://github.com/jarmen423/voice-computer-use-agent/releases):

| Platform | File | Size |
|----------|------|------|
| Windows | `VoiceUse-Windows-x64.exe` | ~45 MB |
| macOS | `VoiceUse-macOS-universal.app.zip` | ~52 MB |
| Linux | `VoiceUse-Linux-x86_64` | ~48 MB |

No Python installation required for these builds.

## Verify Installation

```bash
# Check all dependencies
python -m voiceuse --check-install

# Dry-run mode (no API calls)
python -m voiceuse --dry-run

# Show help
python -m voiceuse --help
```

## Available Commands

After installation, two console commands are available:

| Command | Purpose |
|---------|---------|
| `voiceuse` | Main voice assistant |
| `voiceuse-computer-control-mcp` | MCP server for Codex/other agents |
