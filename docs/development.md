# Development

Guide for contributors and developers extending VoiceUse.

## Project Structure

```
voiceuse/
  __init__.py
  main.py              # Entry point, subsystem wiring, graceful shutdown
  brain.py             # LLM orchestrator, tool schemas, safety dispatch
  config.py            # Pydantic YAML config with env var resolution
  input_manager.py     # pynput hotkeys, porcupine wake word, webrtcvad, Groq STT
  os_controller.py     # Cross-platform window/input/screenshot logic
  os_services.py       # Focused OS services (window, input, screenshot, commands)
  vision_bridge.py     # Screenshot → Codex/Anthropic → coordinate → click
  tts_manager.py       # edge-tts + pyttsx3 + multi-backend playback queue
  safety.py            # Destructive keyword detection + spoken confirmation
  models.py            # Shared dataclasses
  retry.py             # Exponential backoff decorator for API calls
  health.py            # Startup dependency checker
  tool_registry.py     # Shared tool schemas and dispatch
  action_audit.py      # Audit logging for tool calls
  observability.py     # Latency timing and structured logging
  agent_backend.py     # VoiceCommandBackend with native/external backends
  updater.py           # Self-update mechanism
  plugins/
    __init__.py        # Plugin registry / discovery
    base.py            # PluginBase abstract class
    grok_voice/
      __init__.py
      plugin.py        # GrokVoicePlugin lifecycle + tool dispatch
      client.py        # XAIRealtimeClient WebSocket + auth + events
      audio_streamer.py # 24 kHz PCM capture/playback + interruption
tests/
  test_*.py            # Unit tests for each module
```

## Setting Up Development Environment

```bash
# Clone repository
git clone https://github.com/jarmen423/voice-computer-use-agent.git
cd voice-computer-use-agent

# Create virtual environment
python -m venv .venv

# Activate
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"
```

## Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_safety.py

# Run with coverage
pytest --cov=voiceuse --cov-report=html
```

## Linting & Type Checking

```bash
# Format with ruff
ruff check voiceuse tests
ruff format voiceuse tests

# Type check with mypy
mypy voiceuse
```

## Adding a New Tool

To add a new OS control tool:

1. Define the schema in `voiceuse/tool_registry.py`:

    ```python
    TOOL_SCHEMAS = {
        "my_new_tool": {
            "description": "Does something useful",
            "parameters": {
                "type": "object",
                "properties": {
                    "param1": {"type": "string", "description": "First parameter"}
                },
                "required": ["param1"]
            }
        }
    }
    ```

2. Implement the handler:

    ```python
    def my_new_tool(param1: str) -> CommandResult:
        # Implementation
        return CommandResult.success("Done")
    ```

3. Register in the dispatcher:

    ```python
    TOOL_HANDLERS = {
        "my_new_tool": my_new_tool,
        # ...
    }
    ```

4. Add tests in `tests/test_tool_registry.py`

## Adding a New Plugin

1. Create directory: `voiceuse/plugins/my_plugin/`
2. Implement `PluginBase`:

    ```python
    from voiceuse.plugins.base import PluginBase

    class MyPlugin(PluginBase):
        async def start(self):
            pass

        async def stop(self):
            pass
    ```

3. Register in `voiceuse/plugins/__init__.py`:

    ```python
    def get_plugin(name: str) -> PluginBase:
        if name == "my_plugin":
            from .my_plugin.plugin import MyPlugin
            return MyPlugin()
    ```

4. Add config in `voiceuse/config.py` under `PluginsConfig`
5. Write tests in `tests/test_my_plugin.py`

## Key Architecture Decisions

- **Replace Mode for Plugins** — Plugins fully replace the default pipeline. Mixing sample rates (16 kHz Whisper vs 24 kHz xAI) is not supported.
- **Thread → Async Bridge** — Background threads use `asyncio.run_coroutine_threadsafe()` to schedule async callbacks.
- **Safety Before Execution** — Both Brain and plugins apply `SafetyGuard.check_command()` before dispatching tool calls.
- **Retry Decorator** — `@async_retry` wraps transient API calls with 3 attempts and exponential backoff.

## Building & Publishing

```bash
# Build wheel
python -m build

# Verify entry points
python -m voiceuse --help
voiceuse --help

# Test publish to TestPyPI
python -m twine upload --repository testpypi dist/*
```

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make changes with tests
4. Run linting: `ruff check voiceuse tests`
5. Run tests: `pytest`
6. Submit a pull request
