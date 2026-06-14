# Usage

VoiceUse provides two primary interaction modes: **hotkey activation** and **wake word activation**.

## Activating VoiceUse

### Hotkey Mode (Default)

Hold ++right-ctrl++ and speak your command. Release the key to submit.

```
[Hold Right Ctrl] → "Open Chrome and navigate to github.com" → [Release]
```

### Wake Word Mode

Say the wake word (default: **"Computer"**), then speak your command. Voice Activity Detection (VAD) automatically detects when you stop speaking and submits.

```
"Computer, open my email"
```

!!! tip "Choosing a Wake Word"
    Built-in free keywords: `computer`, `jarvis`, `alexa`, `americano`, `blueberry`, `bumblebee`, `grapefruit`, `grasshopper`, `picovoice`, `porcupine`, `terminator`.

## Common Commands

### Window Management

| Say | Result |
|-----|--------|
| "Open Chrome" | Launches or focuses Chrome |
| "Focus VS Code" | Brings VS Code to front |
| "Minimize the current window" | Minimizes active window |
| "Close this window" | Closes active window (with confirmation) |
| "Move the window to the left monitor" | Moves window to specified monitor |

### Typing

| Say | Result |
|-----|--------|
| "Type hello world" | Types "hello world" at cursor |
| "Press enter" | Simulates Enter key |
| "Press control C" | Simulates Ctrl+C |
| "Paste" | Simulates Ctrl+V |

!!! note "Unicode Support"
    For complex Unicode text, VoiceUse uses `pyperclip` to paste through the clipboard rather than simulating individual keystrokes.

### Clicking UI Elements

| Say | Result |
|-----|--------|
| "Click the submit button" | Finds and clicks button by description |
| "Click the menu icon in the top left" | Uses vision + description |
| "Click the red delete button" | Color + text description |

The vision system takes a screenshot and uses either **Codex CLI** or **Anthropic Computer Use API** to locate the described element.

### Screenshots

| Say | Result |
|-----|--------|
| "Take a screenshot" | Captures full primary monitor |
| "Screenshot the current window" | Captures active window only |
| "Screenshot the Chrome window" | Captures specific window |

### System Commands

| Say | Result |
|-----|--------|
| "Run git status" | Executes `git status` in terminal |
| "List files" | Executes `ls` / `dir` |

!!! warning "Command Safety"
    System commands run through an allow-list. Unknown commands are blocked with an error message. Destructive commands trigger spoken confirmation.

### Web Browsing

| Say | Result |
|-----|--------|
| "Open github.com" | Opens URL in preferred browser |
| "Search for Python tutorials" | Opens search in preferred browser |

## Multi-Step Commands

The LLM can plan and execute multi-step actions from a single voice command:

```
"Open Chrome, go to github.com, and click the sign in button"
```

The Brain orchestrator breaks this down into:
1. `open_app("Chrome")`
2. `open_url("github.com")`
3. `click_element("sign in button", "Chrome")`

## Understanding Responses

VoiceUse speaks back through TTS to confirm actions:

| Tone | Meaning |
|------|---------|
| Neutral confirmation | Action completed successfully |
| Error tone | Something went wrong (details spoken) |
| Question tone | Asking for confirmation on destructive action |

## Command-Line Options

```bash
# Normal run
voiceuse

# Dry-run mode (mock responses, no API calls)
voiceuse --dry-run

# Check dependencies
voiceuse --check-install

# Enable file logging
voiceuse --log-file voiceuse.log

# Verbose debug output
voiceuse --verbose

# Show help
voiceuse --help
```

## Tips for Best Results

1. **Speak clearly** — Whisper is accurate but works best with clear speech
2. **Use app aliases** — Configure nicknames for frequently used apps
3. **Describe UI elements precisely** — "the blue submit button" works better than "the button"
4. **Good lighting** — Vision clicking works better with well-lit screens
5. **Reduce monitor scaling** — High DPI scaling can reduce vision accuracy
