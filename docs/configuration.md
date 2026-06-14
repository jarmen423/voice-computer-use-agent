# Configuration

VoiceUse is configured through a `config.yaml` file in the working directory. A default file is generated automatically on first run if one doesn't exist.

## Configuration File Location

The config file is loaded from:

```
./config.yaml          # current working directory
```

## Full Configuration Reference

```yaml
# ============================================
# Audio Settings
# ============================================
audio:
  sample_rate: 16000              # Audio capture sample rate
  hotkey: "right ctrl"            # Key combination to hold for voice input
  wake_word: "computer"           # Free Porcupine keyword (computer, jarvis, alexa, etc.)
  wake_word_model_path: null      # Custom wake word model file

# ============================================
# Speech-to-Text
# ============================================
stt:
  provider: groq                  # STT provider
  model: whisper-large-v3         # Whisper model to use
  api_key: null                   # Falls back to GROQ_API_KEY env var

# ============================================
# LLM Orchestration
# ============================================
llm:
  provider: groq                  # Primary provider: groq, cerebras, or openai
  model: llama-3.3-70b-versatile  # Primary model
  api_key: null                   # Falls back to provider-specific env var
  fallback_provider: openai       # Fallback provider
  fallback_model: gpt-4o-mini     # Fallback model
  fallback_api_key: null          # Falls back to OPENAI_API_KEY env var
  cerebras_api_key: null          # Falls back to CEREBRAS_API_KEY env var

# ============================================
# Text-to-Speech
# ============================================
tts:
  provider: edge                  # edge or pyttsx3
  voice: en-US-AriaNeural         # Voice identifier
  enabled: true                   # Enable/disable TTS

# ============================================
# Computer Vision
# ============================================
computer_use:
  provider: codex                 # codex or anthropic
  api_key: null                   # Only needed for anthropic; codex uses OAuth

# ============================================
# Agent Backend
# ============================================
agent:
  backend: external_agent         # native or external_agent
  runner: codex_cli               # External runner implementation
  command: codex                  # Command to run
  working_directory: "."
  timeout_seconds: 300
  model: null
  sandbox: null
  skip_git_repo_check: true

# ============================================
# Safety & Confirmation
# ============================================
safety:
  confirm_destructive: true       # Enable spoken confirmation
  destructive_keywords:           # Words triggering confirmation
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

# ============================================
# Application Preferences
# ============================================
app:
  preferred_browser: chrome       # Default browser for web actions
  preferred_terminal: cmd         # Default terminal
  codex_app_name: Codex           # Codex window title
  dry_run: false                  # Mock mode (overridden by --dry-run)
  aliases:                        # Spoken name → exact app name
    comet: "Comet Browser"
    # vscode: "Visual Studio Code"
    # edge: "Microsoft Edge"

# ============================================
# Plugins
# ============================================
plugins:
  grok_voice:
    enabled: false
    api_key: null                 # Falls back to XAI_API_KEY env var
    model: grok-voice-think-fast-1.0
    voice: Eve                    # Eve, Ara, Leo, Rex, Sal
    instructions: "You are a desktop voice assistant..."
    sample_rate: 24000
    turn_detection_type: server_vad
    input_audio_transcription_model: grok-2-audio
```

## Environment Variables

All `api_key: null` values in config fall back to environment variables:

| Variable | Used By |
|----------|---------|
| `GROQ_API_KEY` | STT + primary LLM |
| `OPENAI_API_KEY` | Fallback LLM |
| `CEREBRAS_API_KEY` | Cerebras LLM |
| `ANTHROPIC_API_KEY` | Anthropic vision provider |
| `XAI_API_KEY` | Grok Voice plugin |
| `PORCUPINE_ACCESS_KEY` | Custom wake word models |

!!! tip "Environment Setup"
    Set these in your shell profile or a `.env` file loaded by your environment manager.

## App Aliases

Aliases help the LLM resolve spoken nicknames to exact application names:

```yaml
app:
  aliases:
    vscode: "Visual Studio Code"
    edge: "Microsoft Edge"
    terminal: "Windows Terminal"
```

This is especially useful when STT makes transcription errors. The LLM receives your open windows list plus aliases, so it can fuzzy-match "comment" to "Comet Browser".

## Backend Selection

### Native Backend

Uses the built-in Brain LLM orchestrator with direct tool dispatch:

```yaml
agent:
  backend: native
```

### External Agent Backend

Sends desktop work to an MCP-capable action agent (e.g., Codex CLI):

```yaml
agent:
  backend: external_agent
  runner: codex_cli
```

VoiceUse acts as the voice shell; the external agent handles the observe-reason-act-verify loop.
