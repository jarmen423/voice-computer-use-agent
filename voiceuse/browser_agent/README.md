# VoiceUse Browser Agent

A remote browser-control agent that runs on the Kubuntu laptop and is operated
from another machine (e.g. a Windows desktop) over Tailscale. It can launch a
fresh Chrome instance **or attach to an already-running Chrome** via the Chrome
DevTools Protocol (CDP), so it can reuse your real browser profile, cookies,
and extensions.

## How it works

- **Playwright** drives Chrome.
- **FastAPI** serves a small web chat UI.
- A vision-capable planner (OpenAI, Anthropic, or local **Codex CLI**) looks at
  screenshots and decides the next browser action.
- You open the chat UI in a browser on your Windows machine and type commands.

## Start the server

From the project root on Kubuntu:

```bash
set -a; source .env; set +a
export DISPLAY=:0
export XAUTHORITY=$(find /run/user/$(id -u) -maxdepth 1 -name 'xauth_*' -print -quit)
voiceuse-browser-agent --port 8123
```

The UI is then reachable on the Kubuntu Tailscale IP, e.g.
`http://100.111.169.60:8123`.

## Use an existing Chrome session

To control the Chrome you already have open (with your real logins), start
Chrome with remote debugging enabled:

```bash
google-chrome --remote-debugging-port=9222
```

Then click **Attach to existing Chrome** in the web UI.

## Configuration

Edit `config.yaml` under the `browser_agent` section:

```yaml
browser_agent:
  host: "0.0.0.0"
  port: 8123
  cdp_port: 9222
  chrome_path: "/usr/bin/google-chrome"
  headless: false
  profile_dir: null          # set to e.g. ~/.config/google-chrome to reuse profile
  max_steps: 15
  vision_enabled: false      # set true when using OpenAI/Anthropic vision models
  llm_provider: "codex"      # codex | openai | groq | anthropic
  llm_model: "codex"         # model name for API providers
  temperature: 0.2
  max_tokens: 2048
```

### LLM providers

| Provider | Requires | Notes |
|----------|----------|-------|
| `codex`  | `codex` CLI installed and authenticated | Recommended. Uses your ChatGPT subscription and sees screenshots. |
| `openai` | `OPENAI_API_KEY` | Set `vision_enabled: true` and use a vision model such as `gpt-4o-mini`. |
| `anthropic` | `ANTHROPIC_API_KEY` | Set `vision_enabled: true` and use a vision model such as `claude-3-5-sonnet-20241022`. |
| `groq`   | `GROQ_API_KEY` | Text-only mode; Groq currently does not expose vision models for this key. |

## Architecture

```
Windows browser  ──Tailscale──►  Kubuntu :8123
                                    │
                                    ▼
                            FastAPI + WebSocket
                                    │
                                    ▼
                         BrowserAgent (vision loop)
                                    │
                                    ▼
                         BrowserController (Playwright)
                                    │
                         ┌─────────┴─────────┐
                         ▼                   ▼
                   launch fresh Chrome    attach via CDP
```

## Files

- `controller.py` — Playwright wrapper (launch, attach, click, type, screenshot).
- `agent.py` — LLM planner loop and action parser.
- `web.py` — FastAPI app and WebSocket endpoint.
- `static/index.html` — Chat UI.
- `__main__.py` — Entry point (`voiceuse-browser-agent`).

## Known limitations

- Sites with aggressive bot protection (e.g. Google search from a fresh profile)
  may show CAPTCHAs. Attaching to your existing Chrome profile reduces this.
- Shadow-DOM inputs (e.g. DuckDuckGo homepage search box) are hard to target in
  text-only mode; use a vision provider for those sites.
