# Troubleshooting

Common issues and their solutions.

## Installation Issues

### `pyaudio` install fails

**Cause:** Missing PortAudio system library.

**Fix:**

=== "Windows"

    ```powershell
    pip install pipwin
    pipwin install pyaudio
    ```

=== "Debian / Ubuntu"

    ```bash
    sudo apt-get install portaudio19-dev python3-pyaudio
    ```

=== "macOS"

    ```bash
    brew install portaudio
    ```

### `mss` or `pyautogui` import error

**Fix:**

```bash
pip install mss pyautogui
```

## Runtime Issues

### No audio playback

**Cause:** Missing playback backend.

**Fix:** Install `ffplay` (via ffmpeg) or `mpv`:

=== "Windows"

    1. Download ffmpeg from [ffmpeg.org](https://ffmpeg.org)
    2. Add `bin` folder to PATH
    3. Verify: `ffplay -version`

=== "macOS"

    ```bash
    brew install ffmpeg
    ```

=== "Linux"

    ```bash
    sudo apt-get install ffmpeg
    # or
    sudo pacman -S ffmpeg
    ```

### Hotkey not working

**Causes & Fixes:**

| Cause | Fix |
|-------|-----|
| `pynput` not installed | `pip install pynput` |
| No OS permissions | Run as administrator (Windows) or check input permissions (macOS) |
| Conflicting hotkey | Change `audio.hotkey` in `config.yaml` |

### Wake word not detected

**Cause:** Missing `PORCUPINE_ACCESS_KEY` for custom models.

**Fix:**

- Built-in "computer" keyword works **without** a key
- Custom models require `PORCUPINE_ACCESS_KEY`
- Check microphone permissions

### STT / LLM calls hang

**Causes & Fixes:**

| Cause | Fix |
|-------|-----|
| Missing API key | Set `GROQ_API_KEY` |
| Network issues | Check internet connection |
| Rate limiting | Wait and retry; check provider status |

Run with `--verbose` for detailed error output:

```bash
voiceuse --verbose
```

### Window focus fails on Linux

**Cause:** `xdotool` not installed or Wayland incompatibility.

**Fix:**

```bash
sudo apt-get install xdotool wmctrl
```

!!! warning "Wayland"
    `xdotool` does not work on Wayland. Switch to X11 or use XWayland.

### Low confidence on vision clicks

**Causes & Fixes:**

| Cause | Fix |
|-------|-----|
| Poor lighting | Increase room lighting |
| High monitor scaling | Reduce DPI scaling to 100% |
| Ambiguous description | Be more specific: "the blue Submit button" |
| Wrong provider | Try switching between Codex and Anthropic |

### Grok Voice plugin won't start

**Causes & Fixes:**

| Cause | Fix |
|-------|-----|
| Missing `XAI_API_KEY` | Set environment variable |
| `websockets` not installed | `pip install websockets` |
| Microphone in use | Close other apps using the microphone |

## Getting Help

If your issue isn't listed here:

1. Run with `--verbose` and check the output
2. Check [GitHub Issues](https://github.com/jarmen423/voice-computer-use-agent/issues)
3. Open a new issue with:
   - Your OS and Python version
   - Your `config.yaml` (with API keys redacted)
   - The exact error message
   - Steps to reproduce
