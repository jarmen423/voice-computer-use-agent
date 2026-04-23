# VoiceUse

A local desktop voice agent that controls your computer hands-free. All AI inference is cloud-based; the agent itself runs natively on your machine and controls the OS.

## Features

- **Wake word activation** ("Hey computer", configurable) or **hotkey** (hold Right Ctrl to talk, release to submit)
- **Voice Activity Detection (VAD)** knows when you stop speaking
- **Speaks back** with TTS for confirmations, errors, and status updates
- **Cross-platform** window control, typing, and screenshotting (Windows primary, Linux secondary, macOS best-effort)
- **Multi-monitor support** — screenshots only the monitor containing the target window, never the full virtual desktop
- **Safety layer** — asks for spoken confirmation before destructive actions (close, quit, delete, system commands, etc.)
- **Vision-powered clicking** — uses Codex CLI or Anthropic Computer Use API to locate UI elements from screenshots

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│ InputManager│────▶│     Brain    │────▶│   OSController  │
│ (hotkey/VAD │     │ (LLM planner │     │ (windows/input/ │
│  / wake word│     │  + safety)    │     │  screenshots)    │
└─────────────┘     └──────────────┘     └─────────────────┘
                            │                      │
                            ▼                      ▼
                     ┌──────────────┐     ┌─────────────────┐
                     │  TTSManager  │     │  VisionBridge   │
                     │ (edge-tts /  │     │ (Codex CLI /    │
                     │  pyttsx3)    │     │  Anthropic API) │
                     └──────────────┘     └─────────────────┘
```

**Cloud model stack**
- **STT**: Groq Whisper API (`whisper-large-v3`)
- **LLM / Command Parser**: Groq (`llama-3.3-70b-versatile`) with tool calling. Fallback to OpenAI `gpt-4o-mini`.
- **TTS**: `edge-tts` (free Microsoft cloud voices). Local fallback `pyttsx3`.
- **Computer Use / Vision**: Coding agent CLIs handle the screenshot→coordinate loop:
  - **Codex CLI** (`codex exec -i screenshot.png --json`) for non-interactive automation
  - **Anthropic Computer Use API** (`computer_20241022` tool) for programmatic screenshot→action loops

## Quick Start

### 1. Clone / download

```bash
cd voiceuse
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 3. Set API keys via environment variables

```bash
export GROQ_API_KEY="gsk_..."
export OPENAI_API_KEY="sk-..."      # fallback LLM only
export ANTHROPIC_API_KEY="sk-ant-..." # only if using Anthropic for vision
```

On Windows (PowerShell):
```powershell
$env:GROQ_API_KEY="gsk_..."
$env:OPENAI_API_KEY="sk-..."
```

### 4. (Optional) Install Codex CLI for vision mode

If you want to use the **Codex CLI** vision provider:

```bash
# macOS / Linux
brew install openai/codex/codex
# or via npm
npm install -g @openai/codex
```

Verify with `codex --version`.

### 5. Run

```bash
python -m voiceuse
```

The first run creates a default `config.yaml` in the working directory.

## Configuration (`config.yaml`)

All settings live in `config.yaml` in the directory where you run the agent.

```yaml
audio:
  sample_rate: 16000
  chunk_duration_ms: 30
  frame_duration_ms: 30
  silence_timeout_ms: 1500
  vad_aggressiveness: 2
  hotkey: "right ctrl"
  wake_word: "hey computer"
  wake_word_model_path: null

stt:
  provider: groq
  model: whisper-large-v3
  api_key: null          # uses GROQ_API_KEY env var if null
  language: en

llm:
  provider: groq
  model: llama-3.3-70b-versatile
  api_key: null          # uses GROQ_API_KEY env var if null
  fallback_provider: openai
  fallback_model: gpt-4o-mini
  fallback_api_key: null # uses OPENAI_API_KEY env var if null
  temperature: 0.1
  max_tokens: 1024

tts:
  provider: edge
  voice: en-US-AriaNeural
  enabled: true
  speed: "+0%"

computer_use:
  provider: codex          # or "anthropic"
  api_key: null            # uses ANTHROPIC_API_KEY env var if null
  model: claude-3-5-sonnet-20241022
  confidence_threshold: 0.8

safety:
  confirm_destructive: true
  destructive_keywords:
    - close
    - quit
    - delete
    - remove
    - kill
    - terminate
    - shutdown
    - reboot
    - format
    - rm -rf
    - type password
    - enter password
    - input password
  confirmation_timeout_seconds: 10

app:
  preferred_browser: chrome
  preferred_terminal: cmd      # linux: gnome-terminal
  codex_app_name: Codex
```

## Per-OS Setup

### Windows (Primary)

**Required OS tools**
- `ffmpeg` (optional, for `ffplay` audio playback) — [download](https://ffmpeg.org/download.html) and add to PATH
- `Codex CLI` (optional, for vision) — see Quick Start step 4

**Python specifics**
- `pywin32` is required for robust window management; it is installed automatically on Windows via `requirements.txt` (`sys_platform == "win32"` marker).
- If you get a `pyaudio` build error, install the pre-built wheel:
  ```powershell
  pip install pipwin
  pipwin install pyaudio
  ```

### Linux (Secondary)

**Required system packages**
```bash
# Debian / Ubuntu
sudo apt-get update
sudo apt-get install -y \
    python3-pyaudio \
    portaudio19-dev \
    python3-xlib \
    xdotool \
    wmctrl \
    ffmpeg

# Arch
sudo pacman -S python-pyaudio portaudio xdotool wmctrl ffmpeg
```

**Notes**
- `xdotool` and `wmctrl` are used for window management and tiling.
- `ffmpeg` provides `ffplay` for TTS audio playback.
- If you run a Wayland session, `xdotool` may not work; switch to X11 or use an XWayland compatibility layer.

### macOS (Best-effort)

**Required system tools**
```bash
brew install portaudio ffmpeg
```

**Notes**
- Window management is done via AppleScript and Quartz APIs (if `pyobjc-framework-Quartz` is installed). It is less reliable than Windows/Linux.
- `afplay` is used as a TTS playback fallback on macOS.

## Usage Examples

Say any of these after pressing the hotkey or the wake word:

| Command | What happens |
|---------|--------------|
| "Open the Codex app" | Launches or brings OpenAI Codex to foreground, clicks its input box |
| "Choose the last chat that was in the agentic-memory repo" | Focuses Codex sidebar, searches for "agentic-memory", opens it |
| "Open 2 Codex app instances in split view" | Opens two Codex windows side-by-side |
| "Focus my cursor on Chrome" | Brings Chrome to foreground, clicks the address bar |
| "Open Chrome and search for large language models" | Opens Chrome, focuses address bar, types query, submits |
| "Click the submit button" | Takes a screenshot, asks Codex/Anthropic where the submit button is, clicks it |
| "Type hello world into the terminal" | Focuses terminal, types the text |

## Safety

Before any action that matches a destructive keyword (close, quit, delete, system commands, password fields, etc.), the agent:
1. Speaks: "You asked me to [action]. Are you sure? Say yes or no."
2. Listens for your spoken response.
3. Only proceeds if you say "yes", "yep", "yeah", or "sure".
4. Cancels on "no", "nope", "cancel", timeout (10 s), or any other response.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `pyaudio` install fails | Install PortAudio system library first (see per-OS setup) |
| No audio playback | Install `ffmpeg` (for `ffplay`) or `mpv` |
| Wake word not detected | Ensure `PORCUPINE_ACCESS_KEY` env var is set if using a custom `.ppn` model; otherwise the built-in "computer" keyword is free |
| Codex CLI not found | Install with `npm install -g @openai/codex` or `brew install openai/codex/codex` |
| Window focus fails on Linux | Make sure `xdotool` is installed and you are on X11 (not Wayland) |
| Low confidence on clicks | Increase lighting, reduce monitor scaling, or rephrase the description |

## License

MIT
