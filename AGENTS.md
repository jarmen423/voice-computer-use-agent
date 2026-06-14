# VoiceUse Agent Documentation

This file contains build, test, and development instructions for AI agents
working on the VoiceUse codebase.

## Project Overview

VoiceUse is a local desktop voice agent that controls your OS hands-free.
It supports:
- **Hotkey/wake-word triggered voice input**
- **STT** via Groq Whisper (with `asyncio.to_thread` to avoid blocking)
- **LLM orchestration** via Groq primary + OpenAI fallback
- **TTS** via edge-tts primary + pyttsx3 fallback + multi-backend playback
- **OS control** via pyautogui / MSS / platform-specific window APIs
- **Computer vision** via Codex CLI or Anthropic Computer Use API
- **Safety guard** with destructive-keyword detection and spoken confirmation
- **Plugin architecture** with Grok Voice Realtime API support

## Repository Layout

```
voiceuse/
  __init__.py
  main.py              # Entry point, subsystem wiring, graceful shutdown
  brain.py             # LLM orchestrator, tool schemas, safety dispatch
  config.py            # Pydantic YAML config with env var resolution
  input_manager.py     # pynput hotkeys, porcupine wake word, webrtcvad, Groq STT
  os_controller.py     # Cross-platform window/input/screenshot logic
  vision_bridge.py     # Screenshot → Codex/Anthropic → coordinate → click
  tts_manager.py       # edge-tts + pyttsx3 + multi-backend playback queue
  safety.py            # Destructive keyword detection + spoken confirmation
  models.py            # Shared dataclasses
  retry.py             # Exponential backoff decorator for API calls
  health.py            # Startup dependency checker
  plugins/
    __init__.py        # Plugin registry / discovery
    base.py            # PluginBase abstract class
    grok_voice/
      __init__.py
      plugin.py        # GrokVoicePlugin lifecycle + tool dispatch
      client.py        # XAIRealtimeClient WebSocket + auth + events
      audio_streamer.py # 24 kHz PCM capture/playback + interruption
tests/
  test_models.py
  test_config.py
  test_safety.py
  test_brain.py
config.yaml            # Default runtime configuration
pyproject.toml         # Packaging metadata + dev dependencies
requirements.txt       # Runtime dependencies (aligned with pyproject.toml)
```

## Environment Variables

| Variable | Used By |
|----------|---------|
| `GROQ_API_KEY` | STT + primary LLM |
| `OPENAI_API_KEY` | Fallback LLM |
| `CEREBRAS_API_KEY` | Cerebras LLM (primary or fallback) |
| `ANTHROPIC_API_KEY` | Anthropic computer-use provider |
| `XAI_API_KEY` | Grok Voice Realtime API plugin |
| `PORCUPINE_ACCESS_KEY` | Wake-word engine (optional) |

## Build & Run

```bash
# Install in editable mode with dev dependencies
pip install -e ".[dev]"

# Run the agent
python -m voiceuse

# Dry-run mode (mock LLM/STT responses, no API keys required)
python -m voiceuse --dry-run

# Check installation health
python -m voiceuse --check-install

# Enable file logging
python -m voiceuse --log-file voiceuse.log
```

## Testing

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_safety.py
```

## Lint & Type Check

```bash
# Format with ruff
ruff check voiceuse tests
ruff format voiceuse tests

# Type check with mypy
mypy voiceuse
```

## Packaging

```bash
# Build wheel
python -m build

# Verify entry points
python -m voiceuse --help
voiceuse --help
```

## Documentation

Public docs are served at <https://computer-use.agentmemorylabs.com/docs/> via
Cloudflare Pages and use the same custom styling as the landing page.

- Source Markdown lives in `docs/`.
- `docs/private/` is for personal notes — it is **not** listed in the docs build
  so it never gets published.
- `scripts/build_landing_docs.py` converts `docs/*.md` into the custom HTML pages
  in `landing-page/docs/`.
- `.github/workflows/deploy-pages.yml` runs the converter and deploys the
  `landing-page/` directory on every push to `main` that touches `docs/`,
  `scripts/build_landing_docs.py`, or `landing-page/`.

To preview locally:

```bash
python scripts/build_landing_docs.py
```

Then open `landing-page/docs/index.html` in a browser.

`landing-page/docs/` is generated output. The CI build regenerates it during
deploy, so it does not need to be committed (and is ignored by `.gitignore`).

The legacy GitHub Pages deployment (`gh-pages` branch at
`jarmen423.github.io/voice-computer-use-agent/`) should be disabled in the
repository settings and the `gh-pages` branch deleted.

## Adding a New Plugin

1. Create a new directory under `voiceuse/plugins/<my_plugin>/`.
2. Implement `PluginBase` in `plugin.py`.
3. Register it in `voiceuse/plugins/__init__.py` (`get_plugin()`).
4. Add config section in `voiceuse/config.py` under `PluginsConfig`.
5. Write tests in `tests/test_my_plugin.py`.

## Key Architecture Decisions

- **Replace Mode for Plugins**: When a plugin is enabled (e.g. Grok Voice), it
  fully replaces the default Brain + InputManager STT + TTSManager pipeline.
  Mixing sample rates (16 kHz Whisper vs 24 kHz xAI) and VAD logic is not
  supported.
- **Thread → Async Bridge**: Background threads (pynput hotkey, porcupine wake
  word) use `asyncio.run_coroutine_threadsafe()` to schedule async callbacks.
  Any plugin hooking into hotkeys must follow this pattern.
- **Safety Before Execution**: Both Brain and GrokVoicePlugin apply
  `SafetyGuard.check_command()` before dispatching tool calls. Destructive
  actions trigger a spoken confirmation loop.
- **Retry Decorator**: `@async_retry` from `voiceuse.retry` wraps transient
  API calls (network, timeout). Default: 3 attempts with exponential backoff.

## Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `mss` import error | Missing package | `pip install mss` |
| `pyautogui` import error | Missing package | `pip install pyautogui` |
| No TTS audio | Missing playback backend | Install `ffplay` (ffmpeg) or `mpv` |
| Hotkey not working | `pynput` missing or no permissions | `pip install pynput`, check OS permissions |
| Wake word not detected | Missing `PORCUPINE_ACCESS_KEY` | Set env var or disable wake word |
| STT freezes loop | Old blocking code | Ensure `transcribe_audio` uses `asyncio.to_thread()` |

## Contact / Context

If you are an agent resuming work on this project, check the todo list and
recent git history for the current state of implementation.
