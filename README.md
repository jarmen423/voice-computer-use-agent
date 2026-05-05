# VoiceUse

A local desktop voice agent that controls your computer hands-free. All AI inference is cloud-based; the agent itself runs natively on your machine and controls the OS.

## Features

- **Wake word** ("Computer") or **hotkey** (hold Right Ctrl) activation
- **Voice Activity Detection** — knows when you stop speaking
- **Speaks back** with TTS for confirmations, errors, and status updates
- **Cross-platform** window control, typing, and screenshots (Windows primary, Linux secondary, macOS best-effort)
- **Multi-monitor support** — screenshots only the monitor containing the target window
- **Safety layer** — spoken confirmation before destructive actions (close, quit, delete, system commands, etc.)
- **Vision-powered clicking** — uses Codex CLI or Anthropic Computer Use API to locate UI elements from screenshots
- **Grok Voice plugin** — optional end-to-end voice via the xAI Realtime API (replaces the default STT→LLM→TTS pipeline)

## Quick Start

### 1. Prerequisites

- **Python 3.10+**
- API keys for the cloud services you plan to use:
  - `GROQ_API_KEY` — required for STT and primary LLM
  - `OPENAI_API_KEY` — optional fallback LLM
  - `CEREBRAS_API_KEY` — optional, for using Cerebras as primary or fallback LLM
  - `ANTHROPIC_API_KEY` — optional, only if using Anthropic for vision
  - `XAI_API_KEY` — optional, only if using the Grok Voice plugin

### 2. Install

```bash
# Clone or download the repository
cd voiceuse

# Create a virtual environment (recommended)
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# Install the package and all runtime dependencies
pip install -e .

# Or install with dev dependencies (tests, lint, type-check)
pip install -e ".[dev]"
```

### 3. Set API keys

**Linux / macOS:**
```bash
export GROQ_API_KEY="gsk_..."
export OPENAI_API_KEY="sk-..."        # optional fallback
export CEREBRAS_API_KEY="csk_..."     # optional Cerebras LLM
export ANTHROPIC_API_KEY="sk-ant-..." # optional vision
export XAI_API_KEY="xai-..."          # optional Grok Voice
```

**Windows (PowerShell):**
```powershell
$env:GROQ_API_KEY="gsk_..."
$env:OPENAI_API_KEY="sk-..."
```

### 4. Run

```bash
# Normal run
python -m voiceuse

# Dry-run mode — no API calls, uses mock responses (great for first-time validation)
python -m voiceuse --dry-run

# Check that all dependencies are present
python -m voiceuse --check-install

# Enable rotating file logs
python -m voiceuse --log-file voiceuse.log

# Verbose debug output
python -m voiceuse --verbose
```

The first run creates a default `config.yaml` in the working directory if one does not exist.

### 5. Using the agent

1. **Hold Right Ctrl** and speak, then release to submit.
2. Or say **"Computer"** (if wake word is enabled) and speak until VAD detects silence.
3. The agent transcribes your command, plans actions with the LLM, executes them, and speaks the result.

## Configuration (`config.yaml`)

All runtime settings live in `config.yaml`. A default file is generated automatically.

```yaml
audio:
  sample_rate: 16000
  hotkey: "right ctrl"
  wake_word: "computer"        # free Porcupine keywords: computer, jarvis, alexa, etc.
  wake_word_model_path: null

stt:
  provider: groq
  model: whisper-large-v3
  api_key: null          # falls back to GROQ_API_KEY env var

llm:
  provider: groq          # "groq", "cerebras", or "openai"
  model: llama-3.3-70b-versatile
  api_key: null           # falls back to GROQ_API_KEY env var
  fallback_provider: openai
  fallback_model: gpt-4o-mini
  fallback_api_key: null  # falls back to OPENAI_API_KEY env var
  cerebras_api_key: null  # falls back to CEREBRAS_API_KEY env var

tts:
  provider: edge
  voice: en-US-AriaNeural
  enabled: true

computer_use:
  provider: codex          # "codex" (Codex CLI, OAuth) or "anthropic" (API key)
  api_key: null            # only needed for anthropic; codex uses `codex login`

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
  preferred_terminal: cmd
  codex_app_name: Codex
  dry_run: false           # overridden by --dry-run CLI flag
  aliases:                 # spoken name → exact Windows app name
    comet: "Comet Browser"
    # vscode: "Visual Studio Code"
    # edge: "Microsoft Edge"

plugins:
  grok_voice:
    enabled: false
    api_key: null          # falls back to XAI_API_KEY env var
    model: grok-voice-think-fast-1.0
    voice: Eve
    instructions: "You are a desktop voice assistant..."
    sample_rate: 24000
    turn_detection_type: server_vad
    input_audio_transcription_model: grok-2-audio
```

**Key points:**
- `api_key: null` means "read from the environment variable."
- `--dry-run` on the CLI forces `app.dry_run: true` for that run.
- `plugins.grok_voice.enabled: true` replaces the default STT→LLM→TTS pipeline with the xAI Realtime WebSocket.

## App Aliases

VoiceUse passes your **currently open windows** and **app aliases** to the LLM before every command. This means the LLM knows what's running and can resolve nicknames like "comet" → "Comet Browser".

**Add aliases in `config.yaml`:**
```yaml
app:
  aliases:
    comet: "Comet Browser"
    vscode: "Visual Studio Code"
    edge: "Microsoft Edge"
```

**How it works:**
1. You say: *"Open Comet"*
2. Whisper transcribes: *"Open comment"* (STT error)
3. Cerebras receives your open windows list + aliases
4. Cerebras knows "comment" is close to "Comet Browser" and emits `open_app("Comet Browser")`
5. `find_window` uses fuzzy matching (`difflib`) as a final safety net

## Grok Voice Plugin (Optional)

The Grok Voice plugin uses the **xAI Realtime API** to stream audio end-to-end (STT + LLM + TTS in one WebSocket). When enabled, the default Brain/Whisper/edge-tts pipeline is disabled.

**To enable:**
1. Set `XAI_API_KEY` environment variable.
2. Edit `config.yaml`:
   ```yaml
   plugins:
     grok_voice:
       enabled: true
       voice: Eve   # Eve, Ara, Leo, Rex, Sal
   ```
3. Run `python -m voiceuse` as normal.

The plugin streams **24 kHz PCM** audio to xAI and plays back assistant responses directly via PyAudio. It supports the same OS control tools as the default pipeline.

## Per-OS Setup Notes

### Windows (Primary)

- `pywin32` is required for robust window management and is installed automatically via `requirements.txt` on Windows.
- Install **ffmpeg** and add it to PATH for `ffplay` TTS playback (optional but recommended).
- If `pyaudio` fails to install, use a pre-built wheel:
  ```powershell
  pip install pipwin
  pipwin install pyaudio
  ```

### Linux

```bash
# Debian / Ubuntu
sudo apt-get update
sudo apt-get install -y \
    python3-pyaudio portaudio19-dev \
    python3-xlib xdotool wmctrl ffmpeg

# Arch
sudo pacman -S python-pyaudio portaudio xdotool wmctrl ffmpeg
```

- `xdotool` and `wmctrl` are used for window management.
- If you run **Wayland**, `xdotool` may not work; switch to X11 or use XWayland.

### macOS (Best-effort)

```bash
brew install portaudio ffmpeg
```

- Window management uses AppleScript and Quartz APIs (if `pyobjc-framework-Quartz` is installed).
- `afplay` is used as a TTS playback fallback.

## Vision Setup (Optional)

VoiceUse can click UI elements described in natural language using computer vision.

**Codex CLI** (default provider):
```bash
# macOS / Linux
brew install openai/codex/codex
# or
npm install -g @openai/codex
```
- Authenticate with `codex login` (uses your ChatGPT Plus/Pro subscription via OAuth).
- **No API key needed** — `computer_use.api_key` should stay `null`.

**Anthropic** (alternative provider):
- Set `ANTHROPIC_API_KEY`.
- Change `computer_use.provider` to `anthropic` in `config.yaml`.

## Safety

Before any destructive action (close, quit, delete, system commands, password fields, etc.), the agent:
1. Speaks a confirmation prompt.
2. Listens for your spoken response.
3. Proceeds only if you say **yes**, **yep**, **yeah**, or **sure**.
4. Cancels on **no**, **nope**, **cancel**, timeout (10 s), or any other response.

System commands run through an allow-list by default (`shell=False`). If a command is not in the allow-list, it is blocked with an error message.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `pyaudio` install fails | Install PortAudio system library first (see per-OS setup) |
| No audio playback | Install `ffmpeg` (for `ffplay`) or `mpv` |
| Wake word not detected | Set `PORCUPINE_ACCESS_KEY` if using a custom model; built-in "computer" keyword works without a key |
| Codex CLI not found | Install with `npm install -g @openai/codex` or `brew install openai/codex/codex` |
| Window focus fails on Linux | Make sure `xdotool` is installed and you are on X11 (not Wayland) |
| Low confidence on clicks | Increase lighting, reduce monitor scaling, or rephrase the description |
| STT / LLM calls hang | Check your API keys and network connection; run with `--verbose` for details |
| Grok Voice plugin won't start | Ensure `XAI_API_KEY` is set and `websockets` is installed |

## Development

```bash
# Run tests
pytest -v

# Lint
ruff check voiceuse tests
ruff format voiceuse tests

# Type check
mypy voiceuse

# Build wheel
python -m build
```

## License

MIT
