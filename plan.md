Creating a cross-platform local desktop voice agent named "VoiceUse". It uses cloud AI for inference but controls the OS locally. The system will be built as a Python package with a modular architecture.

## Core Modules
1.  **Orchestrator (`brain.py`)**: Central brain. Manages state, takes text input, queries LLMs (Groq/OpenAI) with tool schemas, executes tool calls by dispatching to other modules.
2.  **Input Manager (`input_manager.py`)**: Handles hotkeys (pynput), wake word (porcupine/pvporcupine), voice activity detection (webrtcvad), streams audio to Groq Whisper.
3.  **OS Controller (`os_controller.py`)**: Cross-platform abstraction for windows (pygetwindow/win32gui/xdotool), screenshots (mss/pyautogui), inputs (pyautogui/pynput), and app launching.
4.  **Vision Bridge (`vision_bridge.py`)**: Manages screenshotting specific monitors/windows and dispatches to Computer Use engines (Codex CLI or Anthropic API).
5.  **TTS Manager (`tts_manager.py`)**: Wraps edge-tts (cloud) and pyttsx3 (local fallback), with queue management.
6.  **Safety Layer (`safety.py`)**: Intercepts destructive tool calls, pauses execution, and prompts user for vocal confirmation via TTS/STT loop.

## Tech Stack
- **Language**: Python 3.10+
- **STT**: `groq` API (`whisper-large-v3`)
- **LLM**: `groq` API (`llama-3.3-70b-versatile`) with JSON mode / tool calling. Fallback to `openai` (`gpt-4o-mini`).
- **TTS**: `edge-tts` (primary), `pyttsx3` (fallback).
- **Computer Use Engine**: `codex` CLI (via `subprocess`) or `anthropic` API (`computer_20241022` tool).
- **Window/Input Control**: `pyautogui`, `pynput`, `pygetwindow` (Windows), `xdotool` (Linux), `cliclick` (macOS).
- **Screenshots**: `mss` (multi-monitor support).
- **Hotkeys**: `pynput`.
- **Wake Word**: `pvporcupine`.
- **VAD**: `webrtcvad`.
- **Config**: `pydantic` + `yaml`.

## Workflow
1. **Initialization**: Load config, start hotkey listener, start wake word listener.
2. **Activation**: Hotkey press (Right Ctrl) starts recording. Release stops. Wake word triggers recording.
3. **Audio Processing**: Stream to Groq Whisper -> Text.
4. **Command Parsing**: Send text + tool definitions to Groq LLM. LLM returns tool calls (e.g., `open_app`, `focus_window`, `click_element`).
5. **Execution**:
   - `open_app`: Use OS controller to launch/find window.
   - `focus_window`: Use OS controller to bring to foreground.
   - `click_element`: Vision bridge takes screenshot of target monitor, sends to Codex CLI/Anthropic, receives coordinates, OS controller clicks.
   - `type_text`: OS controller types.
6. **Safety**: If tool is destructive, TTS asks for confirmation, STT listens for "yes".
7. **Feedback**: TTS speaks confirmations/errors.

## Plan
- **Stage 1**: Write the full project scaffolding and core orchestrator (`brain.py`, `config.py`).
- **Stage 2**: Implement the Input Manager and TTS Manager.
- **Stage 3**: Implement the OS Controller and Vision Bridge.
- **Stage 4**: Integrate everything, write the main loop, and create the launcher.
- **Stage 5**: Write README and requirements.

## Implementation
I will create the directory structure and files iteratively. I will start with the core configuration and the main orchestrator, then move to the specialized modules. I need to be very careful with the cross-platform window management, especially the multi-monitor screenshot logic.